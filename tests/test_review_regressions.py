"""Regressionstests zu den Befunden des Code-Reviews (je ein Szenario pro Befund)."""

import json

import httpx
import openpyxl
import pytest
from helpers import FakeProvider, ans, engine

from llmanno.core.aggregate import aggregate
from llmanno.core.config import ModelConfig
from llmanno.core.engine import Engine, RunAborted, result_basis, results
from llmanno.core.evidence import locate
from llmanno.core.export.xlsx import EXCEL_CELL_LIMIT, export_xlsx
from llmanno.core.providers.base import ModelInfo, ProviderError
from llmanno.core.providers.ollama import OllamaProvider
from llmanno.core.schema import AnswerError, parse_answer
from llmanno.core.store import CallRecord

IDS = ["1", "2", "3"]


@pytest.fixture
def project(ready_project):
    return ready_project


def test_results_follow_the_latest_started_run(project):
    # Befund 1: zurück zu einer vollständigen Konfiguration zeigt wieder deren Ergebnisse.
    engine(project, FakeProvider(lambda r, n: ans("K1"))).run()
    seed = project.config.coding.base_seed
    project.update(coding=project.config.coding.model_copy(update={"base_seed": seed + 10}))
    engine(project, FakeProvider(lambda r, n: ans("K2"))).run(item_limit=1)
    assert [r.status for r in results(project, IDS).values()] == ["ok", "pending", "pending"]
    project.update(coding=project.config.coding.model_copy(update={"base_seed": seed}))
    summary = engine(project, FakeProvider(lambda r, n: ans("K2"))).run()
    assert summary.calls == 0
    assert [r.final for r in results(project, IDS).values()] == [["K1"]] * 3


def test_results_use_the_settings_of_their_run(project):
    # Befund 1 (verwandt): Moduswechsel ohne neuen Lauf deutet alte Antworten nicht um.
    engine(project, FakeProvider(lambda r, n: ans("K1", alternatives=["K2"]))).run()
    project.update(mode="multi")
    basis = result_basis(project)
    assert basis.settings["mode"] == "single" and not basis.current
    assert results(project, IDS)["1"].final == ["K1"]


def test_models_with_other_settings_are_excluded(project):
    # Modell „fake“ läuft mit k = 3, dann wird k = 2 und nur „other“ neu kodiert.
    class Two(FakeProvider):
        def list_models(self):
            return [ModelInfo("fake", "d", 32768), ModelInfo("other", "d", 32768)]

    def run_panel(*names):
        project.update(panel=[ModelConfig(provider="ollama", model=n) for n in names])
        Engine(
            project, provider_factory=lambda c: Two(lambda r, n: ans("K2")), sleep=lambda s: None
        ).run(item_limit=1)

    run_panel("fake")
    project.update(k=2)
    run_panel("other")
    project.update(panel=[ModelConfig(provider="ollama", model=n) for n in ("fake", "other")])
    basis = result_basis(project)
    assert basis.current and set(basis.runs) == {"ollama/other"}
    assert basis.excluded == ["ollama/fake"]
    assert results(project, IDS)["1"].votes == {"ollama/other": ["K2"]}


def _call(model, run, codes):
    answer = {
        "rationale": "r",
        "candidates": [
            {
                "code": c,
                "confidence": 50,
                "evidence": [{"text": "x", "status": "exact", "start": 0, "end": 1}],
            }
            for c in codes
        ],
    }
    return CallRecord("u", model, run, "h", run, "s", "u", "ok", answer, [], None, [], {}, {})


def test_tie_is_flagged_with_k_1():
    # Befund 3
    calls = [_call("a", 0, ["A"]), _call("a", 1, ["A"]), _call("b", 0, ["B"]), _call("b", 1, ["B"])]
    result = aggregate(
        "u", calls, models=["a", "b"], mode="single", k=1, n_codes=2, abstain_id=None
    )
    assert "unentschieden" in result.flags and len(result.ranking) == 1


def test_multi_keeps_codes_over_threshold_even_if_abstain_ranks_high():
    # Befund 4
    calls = [_call("m1", 0, ["KEIN"]), _call("m2", 0, ["A", "B"])]
    result = aggregate(
        "u", calls, models=["m1", "m2"], mode="multi", k=2, n_codes=3, abstain_id="KEIN"
    )
    assert result.final == ["A", "B"]


def test_multi_without_abstain_may_answer_with_no_code():
    # Befund 5
    parsed = parse_answer(
        '{"rationale": "nichts passt", "candidates": []}',
        allowed=["A", "B"], abstain_id=None, mode="multi", k=3, max_signal_words=3,
    )  # fmt: skip
    assert parsed.candidates == []
    with pytest.raises(AnswerError):
        parse_answer(
            '{"rationale": "x", "candidates": []}',
            allowed=["A"], abstain_id=None, mode="single", k=1, max_signal_words=3,
        )  # fmt: skip


def test_nan_confidence_is_rejected():
    # Befund 10
    with pytest.raises(AnswerError):
        parse_answer(
            '{"rationale": "x", "candidates": '
            '[{"evidence": ["a"], "code": "A", "confidence": NaN}]}',
            allowed=["A"], abstain_id=None, mode="single", k=1, max_signal_words=3,
        )  # fmt: skip


@pytest.mark.parametrize("body", ["<html>Bad Gateway</html>", "[1, 2]"])
def test_non_json_bodies_become_provider_errors(body):
    # Befund 6a
    provider = OllamaProvider(
        "http://o", transport=httpx.MockTransport(lambda r: httpx.Response(200, text=body))
    )
    with pytest.raises(ProviderError):
        provider.list_models()


def test_failure_while_preparing_is_logged_and_closes_providers(project):
    # Befund 6b
    closed = []

    class Missing(FakeProvider):
        def list_models(self):
            return []

        def close(self):
            closed.append(True)

    with pytest.raises(RunAborted, match="nicht installiert"):
        engine(project, Missing(lambda r, n: ans())).run()
    assert project.store.audit()[-1].event == "run_aborted" and closed == [True]


def test_ollama_names_without_tag_match_latest(project):
    # Befund 7
    class Tagged(FakeProvider):
        def list_models(self):
            return [ModelInfo("fake:latest", digest="d", context_length=32768)]

    engine(project, Tagged(lambda r, n: ans())).run(item_limit=1)
    assert project.store.calls()


def test_show_fills_missing_context_length(project):
    # Befund 2a: ältere Ollama-Versionen nennen die Kontextlänge nur in /api/show.
    class Old(FakeProvider):
        def list_models(self):
            return [ModelInfo("fake", digest="d")]

        def show(self, name):
            return ModelInfo(name, context_length=2048)

    with pytest.raises(RunAborted, match="höchstens 2048"):
        engine(project, Old(lambda r, n: ans())).run()


def test_repair_responses_are_checked_for_truncation(project):
    # Befund 2b
    class Growing(FakeProvider):
        def chat(self, request):
            response = super().chat(request)
            self.tokens_in = 100_000  # der Repair wäre gekürzt
            return response

    with pytest.raises(RunAborted, match="gekürzt"):
        engine(project, Growing(lambda r, n: "kein json" if n == 1 else ans())).run(item_limit=1)


def test_num_ctx_leaves_room_for_a_repair(project):
    provider = FakeProvider(lambda r, n: ans())
    engine(project, provider).run(item_limit=1)
    num_ctx = provider.requests[0].num_ctx
    assert num_ctx >= 2 * project.config.panel[0].max_tokens


def test_evidence_repair_may_not_change_the_decision(project):
    # Befund 8
    script = lambda r, n: ans("K1", evidence=["erfunden"]) if len(r.messages) == 2 else ans("K2")  # noqa: E731
    engine(project, FakeProvider(script)).run(item_limit=1)
    call = project.store.calls()[0]
    assert call.answer["candidates"][0]["code"] == "K1"
    assert {"repair_verworfen", "nicht_belegt"} <= set(call.flags)


def test_evidence_repair_can_be_switched_off(project):
    project.update(coding=project.config.coding.model_copy(update={"evidence_repair": False}))
    provider = FakeProvider(lambda r, n: ans(evidence=["erfunden"]))
    engine(project, provider).run(item_limit=1)
    assert all(len(r.messages) == 2 for r in provider.requests)
    assert "nicht_belegt" in project.store.calls()[0].flags


def test_signal_words_respect_word_boundaries():
    # Befund 10: „Teil“ steht nur in „Gegenteil“.
    hit = locate("Teil", "Das Gegenteil davon.")
    assert hit.status == "fuzzy" and "Das Gegenteil davon."[hit.start : hit.end] == "Gegenteil"
    assert locate("Teil", "Ein Teil davon.").status == "exact"


def test_fuzzy_spans_are_extended_to_whole_words():
    text = "hello world foo"
    hit = locate("hello wrld foo", text)
    assert hit.status == "fuzzy" and text[hit.start : hit.end] == "hello world foo"


def test_export_caps_long_cells_and_cleans_headers(project, tmp_path):
    # Befund 9
    engine(project, FakeProvider(lambda r, n: ans())).run(item_limit=1)
    long_text = "x" * 40_000
    source = json.dumps({"ID": 1, "Text": long_text, "=Spalte\x07": "=1+1"})
    project.store._con.execute("UPDATE units SET source = ? WHERE unit_id = '1'", (source,))
    project.store._con.commit()
    path = tmp_path / "e.xlsx"
    export_xlsx(project, path)
    sheet = openpyxl.load_workbook(path)["Ergebnisse"]
    header = [c.value for c in sheet[1]]
    assert "=Spalte" in header and sheet.cell(1, header.index("=Spalte") + 1).data_type == "s"
    assert len(sheet.cell(2, header.index("Text") + 1).value) == EXCEL_CELL_LIMIT
