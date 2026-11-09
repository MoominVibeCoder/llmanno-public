import pytest
from helpers import FakeProvider, ans, engine

from llmanno.core.aggregate import PANEL_INCOMPLETE, aggregate
from llmanno.core.config import GWDG_CHAT_AI, ModelConfig
from llmanno.core.engine import MAX_RETRY_AFTER_S, Engine, RunAborted, results
from llmanno.core.providers.base import ProviderError
from llmanno.core.store import CallRecord


@pytest.fixture
def project(ready_project):
    return ready_project


def test_adaptive_run_stops_after_agreement_and_aggregates(project):
    provider = FakeProvider(lambda r, n: ans())
    summary = engine(project, provider).run()
    assert summary.calls == 6 and summary.ok == 6  # 3 Einheiten × 2 einstimmige Läufe
    [call] = project.store.calls()[:1]
    assert call.answer["candidates"][0]["evidence"][0]["status"] == "exact"
    request = provider.requests[0]
    assert (request.temperature, request.seed, request.num_ctx) == (0.7, 1, 4096)
    assert [r.seed for r in provider.requests[:2]] == [1, 2]

    result = results(project, ["1", "2", "3"])["2"]
    assert result.final == ["K1"] and result.ranking[0].p_sc == 1.0
    assert result.ranking[0].p_verbal == pytest.approx(0.9)
    assert result.rationale == "weil K1" and result.flags == []
    assert result.evidence["K1"][0].count == 2
    events = [e.event for e in project.store.audit()]
    assert events[-2:] == ["run_started", "run_finished"]


def test_disagreement_runs_all_n_and_gives_fractions(project):
    codes = iter(["K1", "K2", "K1", "K1", "K2"] * 3)
    summary = engine(project, FakeProvider(lambda r, n: ans(next(codes)))).run()
    assert summary.calls == 15
    result = results(project, ["1"])["1"]
    assert [(s.code, s.p_sc) for s in result.ranking] == [("K1", 0.6), ("K2", 0.4)]
    assert "unentschieden" not in result.flags and 0 < result.entropy < 1


def test_stop_and_resume_loses_nothing_and_duplicates_nothing(project):
    provider = FakeProvider(lambda r, n: ans())
    calls = {"n": 0}

    def stop_after_four():
        calls["n"] += 1
        return calls["n"] > 4

    first = engine(project, provider).run(should_stop=stop_after_four)
    assert first.stopped and first.calls == 4
    second = engine(project, provider).run()
    assert second.calls == 2
    assert len(project.store.calls()) == 6
    assert {(c.item_id, c.run) for c in project.store.calls()} == {
        (i, r) for i in ("1", "2", "3") for r in range(2)
    }


def test_broken_json_is_repaired_once(project):
    script = lambda r, n: "kein json" if len(r.messages) == 2 else ans()  # noqa: E731
    engine(project, FakeProvider(script)).run(item_limit=1)
    call = project.store.calls()[0]
    assert call.status == "ok" and "repariert" in call.flags and len(call.raw) == 2
    assert call.meta["repair"]["tokens_in"] == 100


def test_unrepairable_answer_is_invalid_not_dropped(project):
    engine(project, FakeProvider(lambda r, n: "kein json")).run(item_limit=1)
    calls = project.store.calls()
    assert len(calls) == 5 and {c.status for c in calls} == {"invalid"}
    assert results(project, ["1"])["1"].status == "invalid"


def test_invented_evidence_triggers_repair(project):
    script = lambda r, n: ans(evidence=["erfunden"]) if len(r.messages) == 2 else ans()  # noqa: E731
    engine(project, FakeProvider(script)).run(item_limit=1)
    call = project.store.calls()[0]
    assert "repariert" in call.flags and "nicht_belegt" not in call.flags
    assert call.answer["candidates"][0]["evidence"][0]["text"] == "Bruch"


def test_evidence_still_missing_after_repair_is_flagged(project):
    engine(project, FakeProvider(lambda r, n: ans(evidence=["erfunden"]))).run(item_limit=1)
    call = project.store.calls()[0]
    assert call.status == "ok" and "nicht_belegt" in call.flags
    assert "nicht_belegt" in results(project, ["1"])["1"].flags


def test_retryable_errors_back_off_then_succeed(project):
    sleeps = []
    script = lambda r, n: ProviderError("weg", retryable=True) if n <= 2 else ans()  # noqa: E731
    engine(project, FakeProvider(script), sleeps).run(item_limit=1)
    assert sleeps == [2, 5]
    assert project.store.calls()[0].status == "ok"


def test_repeated_hard_errors_abort_the_run(project):
    error = ProviderError("HTTP 404: model not found", retryable=False)
    with pytest.raises(RunAborted, match="5 Fehler in Folge"):
        engine(project, FakeProvider(lambda r, n: error)).run()
    assert project.store.audit()[-1].event == "run_aborted"
    assert {c.status for c in project.store.calls()} == {"error"}


def test_error_calls_are_retried_on_resume(project):
    script = lambda r, n: ProviderError("x", retryable=False) if n == 1 else ans()  # noqa: E731
    engine(project, FakeProvider(script)).run(item_limit=1)
    # Der Fehler zählt nicht gegen den adaptiven Stopp: zwei gültige, einstimmige Läufe reichen.
    assert [c.status for c in project.store.calls()] == ["error", "ok", "ok"]
    engine(project, FakeProvider(lambda r, n: ans())).run(item_limit=1)
    assert [c.status for c in project.store.calls()] == ["ok", "ok", "ok"]


def test_truncated_prompt_aborts(project):
    with pytest.raises(RunAborted, match="gekürzt"):
        engine(project, FakeProvider(lambda r, n: ans(), tokens_in=4000)).run()


def test_prompt_longer_than_model_context_aborts_before_any_call(project):
    provider = FakeProvider(lambda r, n: ans(), context_length=2048)
    with pytest.raises(RunAborted, match="höchstens 2048"):
        engine(project, provider).run()
    assert provider.requests == []


def test_missing_ollama_model_aborts(project):
    project.update(panel=[ModelConfig(provider="ollama", model="fehlt")])
    with pytest.raises(RunAborted, match="nicht installiert"):
        engine(project, FakeProvider(lambda r, n: ans())).run()


def test_remote_provider_needs_consent_and_logs_it(project):
    project.update(
        providers=[*project.config.providers, GWDG_CHAT_AI],
        panel=[ModelConfig(provider="gwdg", model="fake")],
    )
    run = engine(project, FakeProvider(lambda r, n: ans()))
    assert [p.name for p in run.remote_providers()] == ["GWDG/KISSKI Chat AI"]
    with pytest.raises(PermissionError, match="GWDG"):
        run.run()
    run.run(item_limit=1, remote_consent=True)
    consent = next(e for e in project.store.audit() if e.event == "remote_consent")
    assert consent.details["providers"][0]["base_url"] == GWDG_CHAT_AI.base_url
    assert project.store.calls()[0].request["num_ctx"] is None  # kein Ollama-Parameter


def test_rate_limit_spaces_calls(project):
    ticks = iter(range(1000))  # jede Uhrabfrage liegt 0,5 s nach der vorigen
    project.update(
        providers=[project.config.providers[0].model_copy(update={"rate_limit_per_min": 30})]
    )
    sleeps = []
    run = Engine(
        project,
        provider_factory=lambda cfg: FakeProvider(lambda r, n: ans()),
        sleep=sleeps.append,
        clock=lambda: next(ticks) * 0.5,
    )
    run.run(item_limit=1)
    # 30/min = alle 2 s; zwischen zwei Aufrufen vergehen nur 0,5 s, also 1,5 s warten.
    assert sleeps and all(s == pytest.approx(1.5) for s in sleeps)


def test_new_configuration_starts_a_new_run_and_results_follow_it(project):
    engine(project, FakeProvider(lambda r, n: ans("K1"))).run(item_limit=1)
    project.update(k=1)
    engine(project, FakeProvider(lambda r, n: ans("K2"))).run(item_limit=1)
    assert len({c.config_hash for c in project.store.calls()}) == 2
    assert results(project, ["1"])["1"].final == ["K2"]


def test_more_runs_extend_an_existing_run(project):
    project.update(coding=project.config.coding.model_copy(update={"adaptive": False, "n_runs": 2}))
    engine(project, FakeProvider(lambda r, n: ans())).run(item_limit=1)
    project.update(coding=project.config.coding.model_copy(update={"n_runs": 4}))
    summary = engine(project, FakeProvider(lambda r, n: ans())).run(item_limit=1)
    assert summary.calls == 2 and len({c.config_hash for c in project.store.calls()}) == 1


def test_pilot_eta(project):
    summary = engine(project, FakeProvider(lambda r, n: ans())).run(item_limit=2)
    # Gemessen wird die Gesamtdauer je Aufruf (inklusive Repair), nicht die gemeldete Latenz.
    assert summary.latencies and summary.mean_latency_s < 0.5
    assert summary.eta_s(remaining_pairs=10) == pytest.approx(10 * 3 * summary.mean_latency_s)
    assert all("duration_s" in c.meta for c in project.store.calls())


def _call(model, run, codes, status="ok"):
    answer = {
        "rationale": f"{model}-{run}",
        "candidates": [
            {
                "code": c,
                "confidence": 60,
                "evidence": [{"text": "x", "status": "exact", "start": 0, "end": 1}],
            }
            for c in codes
        ],
    }
    return CallRecord(
        "u", model, run, "h", run, "s", "u", status, answer if status == "ok" else None, [], None,
        [], {}, {},
    )  # fmt: skip


def test_aggregate_panel_dissent_and_tie():
    calls = [
        _call("a", 0, ["K1"]),
        _call("a", 1, ["K1"]),
        _call("b", 0, ["K2"]),
        _call("b", 1, ["K2"]),
    ]
    result = aggregate(
        "u", calls, models=["a", "b"], mode="single", k=3, n_codes=3, abstain_id=None
    )
    assert {s.code: s.p_sc for s in result.ranking} == {"K1": 0.5, "K2": 0.5}
    assert {"dissens", "unentschieden"} <= set(result.flags)
    assert result.votes == {"a": ["K1"], "b": ["K2"]}
    assert result.rationale == "a-0"


def test_aggregate_multi_label_threshold_and_abstain():
    calls = [_call("a", 0, ["K1", "K2"]), _call("a", 1, ["K1"]), _call("a", 2, ["K1", "K3"])]
    result = aggregate(
        "u", calls, models=["a"], mode="multi", k=3, n_codes=4, abstain_id="KEIN_CODE"
    )
    assert result.final == ["K1"]
    # K2 bleibt unter der Schwelle (1/3), also gilt die Restkategorie.
    only_abstain = [
        _call("a", 0, ["KEIN_CODE"]),
        _call("a", 1, ["KEIN_CODE"]),
        _call("a", 2, ["K2"]),
    ]
    result = aggregate(
        "u", only_abstain, models=["a"], mode="multi", k=3, n_codes=4, abstain_id="KEIN_CODE"
    )
    assert result.final == ["KEIN_CODE"] and "abstain" in result.flags


def test_aggregate_ignores_models_outside_the_panel():
    calls = [_call("a", 0, ["K1"]), _call("alt", 0, ["K2"])]
    result = aggregate("u", calls, models=["a"], mode="single", k=3, n_codes=3, abstain_id=None)
    assert [s.code for s in result.ranking] == ["K1"] and result.n_valid == 1


def test_aggregate_flags_an_incomplete_panel():
    """Roadmap 4: Mit 1 von 2 Modellen stand die Einheit still als „ok“ da."""
    calls = [_call("a", 0, ["K1"]), _call("a", 1, ["K1"]), _call("b", 0, [], status="invalid")]
    result = aggregate(
        "u", calls, models=["a", "b"], mode="single", k=3, n_codes=3, abstain_id=None
    )
    assert result.status == "ok" and result.final == ["K1"]
    assert PANEL_INCOMPLETE in result.flags and "dissens" not in result.flags
    full = aggregate(
        "u", calls[:2] + [_call("b", 0, ["K1"])], models=["a", "b"], mode="single", k=3,
        n_codes=3, abstain_id=None,
    )  # fmt: skip
    assert PANEL_INCOMPLETE not in full.flags


def test_long_retry_after_aborts_instead_of_waiting_for_hours(project):
    """Roadmap 9: Retry-After 3600 hieß 4 × 1 h Warten je Aufruf, rund 20 h bis zum Abbruch."""
    sleeps = []
    quota = lambda r, n: ProviderError("HTTP 429", retryable=True, retry_after=3600)  # noqa: E731
    with pytest.raises(RunAborted, match="60 min Pause"):
        engine(project, FakeProvider(quota), sleeps).run(item_limit=1)
    assert sleeps == [] and project.store.calls() == []
    wait = ProviderError("HTTP 429", retryable=True, retry_after=MAX_RETRY_AFTER_S)
    short = lambda r, n: wait if n == 1 else ans()  # noqa: E731
    engine(project, FakeProvider(short), sleeps).run(item_limit=1)
    assert sleeps == [MAX_RETRY_AFTER_S] and project.store.calls()[0].status == "ok"


def test_stop_ends_a_wait_without_recording_the_call(project):
    """Roadmap 9: Anhalten wartete die Pause ab; die GUI übergibt dafür Event.wait."""
    import threading

    stop = threading.Event()
    waits = []

    def wait(seconds):  # wie Event.wait: der Nutzer hält während der Pause an
        waits.append(seconds)
        stop.set()
        return True

    busy = lambda r, n: ProviderError("HTTP 503", retryable=True)  # noqa: E731
    summary = Engine(project, provider_factory=lambda cfg: FakeProvider(busy), sleep=wait).run(
        should_stop=stop.is_set
    )
    assert summary.stopped and waits == [2]
    assert project.store.calls() == [] and project.store.audit()[-1].event == "run_stopped"


def test_a_network_outage_pauses_the_run_instead_of_ending_it(project, monkeypatch):
    """Roadmap 24: Ein Netzausfall von 5 Minuten beendete einen Nachtlauf."""
    from llmanno.core import engine as engine_module

    sleeps = []
    down = ProviderError("Verbindung fehlgeschlagen", retryable=True)
    flaky = lambda r, n: down if n <= 30 else ans()  # noqa: E731
    summary = engine(project, FakeProvider(flaky), sleeps).run()
    assert 60 in sleeps and summary.ok > 0 and not summary.stopped
    assert any(e.event == "run_waiting" for e in project.store.audit())

    # Dauerhaft weg: Nach der Wartezeit bricht der Lauf ab (kleines Projekt, kleine Schwellen).
    monkeypatch.setattr(engine_module, "MAX_CONSECUTIVE_ERRORS", 2)
    monkeypatch.setattr(engine_module, "MAX_OUTAGE_S", 60)
    project.update(panel=[ModelConfig(provider="ollama", model="fake", temperature=0.3)])
    always = lambda r, n: down  # noqa: E731
    with pytest.raises(RunAborted, match="nach 1 min Warten"):
        engine(project, FakeProvider(always), sleeps := []).run()
    assert sleeps.count(60) == 1


def test_systematically_invalid_answers_abort(project, monkeypatch):
    """Roadmap 24: Ein Modell, das das Format nie einhält, lief mit n_runs × 2 Aufrufen weiter.
    Gezählt wird je Einheit, damit eine einzelne heikle Einheit den Lauf nicht beendet."""
    from llmanno.core import engine as engine_module

    monkeypatch.setattr(engine_module, "MAX_INVALID_UNITS", 2)
    lone = lambda r, n: "kein JSON" if 'id="1"' in r.messages[1]["content"] else ans()  # noqa: E731
    engine(project, FakeProvider(lone)).run()  # nur Einheit 1 ungültig: kein Abbruch
    assert {c.item_id for c in project.store.calls() if c.status == "ok"} == {"2", "3"}

    project.update(panel=[ModelConfig(provider="ollama", model="fake", temperature=0.3)])
    with pytest.raises(RunAborted, match="2 Einheiten in Folge ohne gültige Antwort"):
        engine(project, FakeProvider(lambda r, n: "kein JSON")).run()
    latest = project.store.latest_config_hash("ollama/fake")
    coded = {c.item_id for c in project.store.calls(config_hash=latest)}
    assert coded == {"1", "2"}  # die dritte Einheit lief nicht mehr


def test_run_tab_shows_error_reasons_and_that_restart_repeats_them(qtbot):
    from llmanno.core.engine import Progress, RunSummary
    from llmanno.gui.run_page import RunPage

    page = RunPage()
    qtbot.addWidget(page)
    page._started = 0.0
    page._on_progress(Progress("m", "U1", 0, "error", 1, 10, "HTTP 500: Serverfehler"))
    page._pilot = False
    page._on_done(RunSummary(calls=3, ok=2, errors=1))
    lines = [page.log.item(i).text() for i in range(page.log.count())]
    assert any("Fehler: Einheit U1, Durchlauf 1 · HTTP 500: Serverfehler" in line for line in lines)
    assert any("ein neuer Start wiederholt sie" in line for line in lines)
