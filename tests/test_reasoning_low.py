"""Sensitivität „Reasoning niedrig“: Liefert weniger Reasoning dieselben Codes mit weniger Token?"""

from dataclasses import replace

import pytest
from helpers import FakeProvider, engine
from test_evaluation import TRUTH, llm_answer, make_project

from llmanno.core.config import GWDG_CHAT_AI, ModelConfig
from llmanno.core.engine import ISOLATED, REASONING_LOW
from llmanno.core.sensitivity import (
    reasoning_models,
    run_sensitivity,
    sensitivity_items,
    sensitivity_report,
    variants_for,
)


class Thinking(FakeProvider):
    """Denkt verborgen: viele Ausgabe-Token, mit Aufwand low weniger."""

    def chat(self, request):
        response = super().chat(request)
        return replace(response, tokens_out=60 if request.reasoning_effort == "low" else 400)


@pytest.fixture
def project(tmp_path):
    project = make_project(tmp_path, run=False)  # Kontextmodus
    project.update(
        providers=[*project.config.providers, GWDG_CHAT_AI],
        panel=[ModelConfig(provider=GWDG_CHAT_AI.key, model="gpt-oss-120b")],
    )
    yield project
    project.close()


def _sensitivity(project, provider):
    ids = sensitivity_items(project)
    done = run_sensitivity(project, ids, remote_consent=True, provider_factory=lambda c: provider)
    return ids, [v for v, _ in done]


def test_low_reasoning_runs_for_a_thinking_model_outside_alpha(project):
    assert variants_for(project.config)[-2:] == [ISOLATED, REASONING_LOW]
    provider = Thinking(llm_answer)
    ids, done = _sensitivity(project, provider)
    assert done[-1] == REASONING_LOW
    low = [r for r in provider.requests if r.reasoning_effort == "low"]
    assert len(low) == len(ids)
    assert all(r.reasoning_effort is None for r in provider.requests[: len(ids)])  # Original
    (report,) = sensitivity_report(project, {i: TRUTH[i] for i in ids})
    assert report.isolated.key == ISOLATED and report.reasoning.key == REASONING_LOW
    assert not report.reasoning.wording and len(report.wording_rows) == 4
    assert report.reasoning.agreement == 1.0
    assert (report.reasoning.tokens_out, report.rows[0].tokens_out) == (60, 400)
    assert report.rows[0].seconds is not None


def test_no_low_reasoning_without_evidence_or_where_it_cannot_drop(project):
    _, done = _sensitivity(project, FakeProvider(llm_answer))  # 20 Ausgabe-Token: denkt nicht
    assert REASONING_LOW not in done and reasoning_models(project) == []
    model = project.config.panel[0]
    project.update(panel=[model.model_copy(update={"reasoning_effort": "high"})])
    assert reasoning_models(project) == [model.key]  # gesetzter Aufwand reicht als Nachweis
    project.update(panel=[model.model_copy(update={"reasoning_effort": "low"})])
    assert REASONING_LOW not in variants_for(project.config)
    project.update(panel=[ModelConfig(provider="ollama", model="fake")])
    assert REASONING_LOW not in variants_for(project.config)  # Ollama kennt den Parameter nicht


def test_evaluation_and_exports_show_the_saving(project, qtbot, tmp_path):
    import openpyxl

    from llmanno.core.evaluation import evaluate
    from llmanno.core.export.evaluation import export_evaluation
    from llmanno.core.export.report import report_html
    from llmanno.gui.evaluation_page import EvaluationPage

    engine(project, Thinking(llm_answer)).run(remote_consent=True)
    _sensitivity(project, Thinking(llm_answer))
    report = evaluate(project, resamples=20)
    page = EvaluationPage()
    qtbot.addWidget(page)
    page.set_project(project, refresh=False)
    page._report = report
    page._render()
    html = page.view.toHtml()
    assert "Reasoning niedrig" in html and "Ø Token Ausgabe" in html
    export_evaluation(report, tmp_path / "e.xlsx")
    sheet = openpyxl.load_workbook(tmp_path / "e.xlsx")["Sensitivität"]
    rows = list(sheet.values)
    assert "Token Ausgabe (Mittel)" in rows[0]
    assert "Mit niedrigem Reasoning-Aufwand stimmten 100 %" in report_html(project, report)


def test_mixed_panel_lowers_only_the_thinking_model(project):
    project.update(panel=[*project.config.panel, ModelConfig(provider="ollama", model="fake")])
    provider = Thinking(llm_answer)
    ids, done = _sensitivity(project, provider)
    assert done[-1] == REASONING_LOW
    low = [r for r in provider.requests if r.reasoning_effort == "low"]
    assert len(low) == len(ids) and {r.model for r in low} == {"gpt-oss-120b"}


def test_rejected_effort_leaves_the_other_variants_valid(project):
    from llmanno.core.providers.base import ProviderError

    def refuse_low(request, n):
        if request.reasoning_effort == "low":
            return ProviderError("HTTP 400: reasoning_effort not supported", retryable=False)
        return llm_answer(request, n)

    ids = sensitivity_items(project)
    done = run_sensitivity(
        project, ids, remote_consent=True, provider_factory=lambda c: Thinking(refuse_low)
    )
    variant, summary = done[-1]
    assert variant == REASONING_LOW and "abgebrochen" in summary.notes[0]
    (report,) = sensitivity_report(project, {i: TRUTH[i] for i in ids})
    assert report.n_items == len(ids) and report.alpha == 1.0


def test_detection_ignores_low_effort_and_truncated_answers(project):
    """Fortsetzen nach einem Halt in „Reasoning niedrig“ scheitert nicht an der Heuristik."""
    provider = Thinking(llm_answer)
    ids = sensitivity_items(project)
    calls = {"n": 0}

    def stop_in_low():
        calls["n"] += 1
        return calls["n"] > 5 * len(ids) + 3  # Original, drei Umformulierungen, isoliert, 3 low

    run_sensitivity(project, ids, remote_consent=True, provider_factory=lambda c: provider,
                    should_stop=stop_in_low)  # fmt: skip
    key = project.config.panel[0].key
    assert reasoning_models(project) == [key]
    assert project.store.hidden_reasoning(key)
    assert project.store.mean_tokens_out(key) == 400  # nur Aufrufe ohne gesendeten Aufwand
    assert project.store.mean_tokens_out(key, "low") == 60


def test_advice_only_when_low_saves_tokens(project, qtbot):
    from llmanno.core.evaluation import evaluate
    from llmanno.gui.evaluation_page import EvaluationPage

    class Ignoring(FakeProvider):  # der Anbieter übergeht den Aufwand
        def chat(self, request):
            return replace(super().chat(request), tokens_out=400)

    engine(project, Ignoring(llm_answer)).run(remote_consent=True)
    _sensitivity(project, Ignoring(llm_answer))
    page = EvaluationPage()
    qtbot.addWidget(page)
    page.set_project(project, refresh=False)
    page._report = evaluate(project, resamples=20)
    page._render()
    assert "spart hier kaum" in page.view.toPlainText()
