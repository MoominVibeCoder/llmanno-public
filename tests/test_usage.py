"""Verbrauch und Kontingent: Token, Cache, Reasoning und Restkontingent je Anbieter."""

import time
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from helpers import FakeProvider, ans, engine

from llmanno.core.config import GWDG_CHAT_AI, ModelConfig
from llmanno.core.engine import _open_calls, run_budget
from llmanno.core.providers.base import ChatRequest, ProviderError, quota
from llmanno.core.providers.openai_compat import OpenAICompatProvider

REQUEST = ChatRequest(model="m", messages=[{"role": "user", "content": "hi"}],
                      temperature=None, max_tokens=100)  # fmt: skip
# So meldet GWDG das Kontingent (docs.hpc.gwdg.de, SAIA)
GWDG_HEADERS = {
    "x-ratelimit-limit-minute": "10", "x-ratelimit-remaining-minute": "9",
    "x-ratelimit-limit-day": "400", "x-ratelimit-remaining-day": "399",
    "x-ratelimit-limit-month": "3000", "x-ratelimit-remaining-month": "2999",
    "ratelimit-reset": "54",
}  # fmt: skip


def _provider(response):
    return OpenAICompatProvider(
        "https://x/v1", api_key="k", transport=httpx.MockTransport(lambda r: response)
    )


def test_openai_reports_cache_reasoning_and_quota():
    reply = {
        "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 900, "completion_tokens": 400,
            "prompt_tokens_details": {"cached_tokens": 768},
            "completion_tokens_details": {"reasoning_tokens": 300},
        },
    }  # fmt: skip
    response = _provider(httpx.Response(200, json=reply, headers=GWDG_HEADERS)).chat(REQUEST)
    assert response.extra == {"tokens_cached": 768, "tokens_reasoning": 300}
    assert response.quota == {"minute": (9, 10), "day": (399, 400), "month": (2999, 3000)}


def test_quota_reads_openai_names_and_skips_the_rest():
    headers = {
        "X-RateLimit-Remaining-Requests": "59", "x-ratelimit-limit-requests": "60",
        "x-ratelimit-reset-requests": "1s", "x-ratelimit-remaining-tokens": "viel",
    }  # fmt: skip
    assert quota(headers) == {"requests": (59, 60)}


def test_exhausted_gwdg_quota_names_the_wait():
    """GWDG schickt bei 429 kein Retry-After, sondern ratelimit-reset."""
    response = httpx.Response(429, headers={"ratelimit-reset": "40000"}, text="limit")
    with pytest.raises(ProviderError) as info:
        _provider(response).chat(REQUEST)
    assert info.value.retryable and info.value.retry_after == 40000


class Metered(FakeProvider):
    def chat(self, request):
        response = super().chat(request)
        left = 400 - len(self.requests)
        extra = {"tokens_cached": 64, "tokens_reasoning": 15}
        return replace(response, extra=extra, quota={"day": (left, 400)})


def test_run_sums_usage_and_keeps_the_last_quota(ready_project):
    plain = engine(ready_project, FakeProvider(lambda r, n: ans())).run(item_limit=1)
    assert plain.tokens_in == 100 * plain.calls > 0
    assert plain.tokens_cached is None and plain.quota == {}  # nicht gemeldet
    summary = engine(ready_project, Metered(lambda r, n: ans())).run()
    calls = summary.calls
    assert (summary.tokens_in, summary.tokens_out) == (100 * calls, 20 * calls)
    assert (summary.tokens_cached, summary.tokens_reasoning) == (64 * calls, 15 * calls)
    assert summary.quota == {"ollama": {"day": (400 - calls, 400)}}


def test_budget_gives_ranges_seen_output_and_calls_per_provider(ready_project):
    cfg = ready_project.config
    budget = run_budget(ready_project)
    n, runs, least = 3, cfg.coding.n_runs, cfg.coding.adaptive_min_agree  # drei Einheiten
    assert (budget.calls_min, budget.calls) == (n * least, n * runs)
    assert budget.tokens_in_min * runs == budget.tokens_in * least
    assert budget.tokens_out_seen is None and budget.per_provider == {
        "ollama": (n * least, n * runs)
    }
    assert run_budget(ready_project, adaptive=False).calls_min == n * runs

    engine(ready_project, FakeProvider(lambda r, n: ans())).run(item_limit=1)
    resumed = run_budget(ready_project)  # Einheit 1 ist fertig (2 einstimmige Läufe)
    assert resumed.calls_min == (n - 1) * least
    assert resumed.calls == (n - 1) * runs  # gestoppte Einheit zählt nicht zur Obergrenze
    assert resumed.tokens_out_seen == (resumed.calls_min * 20, resumed.calls * 20)
    fresh = run_budget(ready_project, resume=False)
    assert fresh.tokens_out_seen == (n * least * 20, n * runs * 20)  # 20 Token je Antwort
    ready_project.update(panel=[*cfg.panel, ModelConfig(provider="ollama", model="neu")])
    assert run_budget(ready_project).tokens_out_seen is None  # ein Modell ohne Antworten


def test_open_calls_follow_the_adaptive_stop():
    """Noch offen je Einheit (mindestens, höchstens) bei 5 Läufen, Stopp nach 2 einstimmigen."""

    def call(status="ok", code="K1"):
        return SimpleNamespace(status=status, answer={"candidates": [{"code": code}]})

    def open_calls(*calls):
        return _open_calls(dict(enumerate(calls)), 5, 2, "single")

    assert open_calls() == (2, 5)
    assert open_calls(call()) == (1, 4)
    assert open_calls(call(), call()) == (0, 0)  # gestoppt
    assert open_calls(call(), call("ok", "K2")) == (3, 3)  # uneinig: alle übrigen Läufe
    assert open_calls(call(), call("invalid")) == (3, 3)  # ungültig verhindert den Stopp
    assert open_calls(call("invalid")) == (4, 4)
    assert _open_calls({1: call()}, 5, 2, "single") == (1, 4)  # Lauf 0 war ein Netzfehler
    assert _open_calls({}, 5, None, "single") == (5, 5)  # ohne adaptiven Stopp
    assert _open_calls({}, 2, 3, "single") == (2, 2)  # weniger Läufe als nötig für den Stopp


def test_repairs_count_towards_usage(ready_project):
    script = lambda r, n: "kaputt" if n == 1 else ans()  # noqa: E731
    summary = engine(ready_project, FakeProvider(script)).run(item_limit=1)
    assert summary.repaired == 1
    assert summary.tokens_in == 100 * (summary.calls + 1)  # der Repair ist ein Aufruf mehr


def test_quota_survives_overflow_and_broken_settings():
    from PySide6.QtCore import QSettings

    from llmanno.gui.run_page import _quota_key, load_quota, quota_current

    assert quota({"x-ratelimit-remaining-day": "inf", "x-ratelimit-limit-day": "1e999"}) == {}
    for broken in ('{"at": 1, "windows": [1, 2]}', '{"at": 1, "windows": {"day": ["x", 4]}}',
                   "kein json", '{"at": 1}'):  # fmt: skip
        QSettings().setValue(_quota_key("https://kaputt"), broken)
        assert load_quota("https://kaputt") is None
    now = time.time()
    assert quota_current(now, "day") and quota_current(now, "month")
    assert not quota_current(now - 40 * 86400, "month") and not quota_current(now, "hour")


def test_consent_shows_quota_and_warns_when_it_is_short(qtbot, ready_project, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from llmanno.gui.run_page import RunPage, load_quota, save_quota

    save_quota(GWDG_CHAT_AI.base_url, {"day": (4, 400), "month": (2999, 3000)})
    at, windows = load_quota(GWDG_CHAT_AI.base_url)
    assert time.time() - at < 60 and windows["day"] == (4, 400)
    ready_project.update(
        providers=[*ready_project.config.providers, GWDG_CHAT_AI],
        panel=[ModelConfig(provider=GWDG_CHAT_AI.key, model="m")],
    )
    budget = run_budget(ready_project)
    asked = []
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a: asked.append(a) or QMessageBox.StandardButton.No
    )
    page = RunPage()
    qtbot.addWidget(page)
    page._ask_consent([GWDG_CHAT_AI], 3, budget)
    text = asked[0][2]
    assert f"{budget.calls_min} bis {budget.calls} Aufrufe" in text
    assert "2999 von 3000 pro Monat, 4 von 400 pro Tag" in text
    assert "Achtung" in text and "frei sind noch 4 pro Tag" in text


def test_finished_run_logs_usage_and_stores_the_quota(qtbot, ready_project):
    from llmanno.core.engine import RunSummary
    from llmanno.gui.run_page import RunPage, load_quota

    ready_project.update(providers=[*ready_project.config.providers, GWDG_CHAT_AI])
    page = RunPage()
    qtbot.addWidget(page)
    page.set_project(ready_project)
    summary = RunSummary(
        calls=4, ok=4, tokens_in=4000, tokens_out=1600, tokens_reasoning=1200,
        calls_per_item=[2, 2], quota={GWDG_CHAT_AI.key: {"month": (2000, 3000)}},
    )  # fmt: skip
    page.progress.setRange(0, 10)
    page.progress.setValue(4)
    page._on_done(summary)
    assert page.progress.value() == 10  # übersprungene Varianten: Balken trotzdem voll
    lines = [page.log.item(i).text() for i in range(page.log.count())]
    assert any("4000 Token Eingabe, 1600 Ausgabe (davon 1200 Reasoning)" in t for t in lines)
    assert any("im Mittel 2.0 Aufrufe" in t for t in lines)
    assert any("noch 2000 von 3000 pro Monat" in t for t in lines)
    assert load_quota(GWDG_CHAT_AI.base_url)[1] == {"month": (2000, 3000)}


def test_aborted_run_still_reports_usage_and_quota(qtbot, ready_project):
    from llmanno.core.engine import RunAborted
    from llmanno.gui.run_page import RunPage, load_quota

    ready_project.update(providers=[*ready_project.config.providers, GWDG_CHAT_AI])
    summary = engine(ready_project, Metered(lambda r, n: ans())).run(item_limit=1)
    error = RunAborted("Kontingent erschöpft")
    error.summary = replace(summary, quota={GWDG_CHAT_AI.key: {"day": (0, 400)}})
    page = RunPage()
    qtbot.addWidget(page)
    page.set_project(ready_project)
    page._on_failed(error)
    assert load_quota(GWDG_CHAT_AI.base_url)[1] == {"day": (0, 400)}


def test_engine_attaches_the_summary_to_an_abort(ready_project):
    from llmanno.core.engine import RunAborted

    hard = ProviderError("HTTP 404", retryable=False)
    with pytest.raises(RunAborted) as info:
        engine(ready_project, Metered(lambda r, n: hard if n > 2 else ans())).run()
    assert info.value.summary is not None and info.value.summary.tokens_in == 200
