"""Führung durch den Ablauf (Nächster Schritt) und Kurzurteil der Evaluation (Ampel)."""

from dataclasses import replace

import pytest
from helpers import FakeProvider, ans, engine
from test_evaluation import llm_answer, make_project

from llmanno.core.evaluation import evaluate
from llmanno.core.metrics.bootstrap import Estimate
from llmanno.core.steps import next_step, workflow
from llmanno.core.validation import create_split, freeze
from llmanno.core.verdict import verdict


@pytest.fixture
def project(tmp_path):
    project = make_project(tmp_path, run=False)
    yield project
    project.close()


def _keys(project):
    return [(s.key, s.done) for s in workflow(project)]


def test_steps_with_gold_follow_split_dev_freeze_test(project):
    steps = workflow(project)
    assert [s.key for s in steps] == [
        "data", "codebook", "models", "split", "pilot", "dev", "freeze", "full", "test", "export",
    ]  # fmt: skip
    assert next_step(steps).key == "split"
    parts = create_split(project)
    assert next_step(workflow(project)).key == "pilot"
    dev = [i for i, p in parts.items() if p == "dev"]
    engine(project, FakeProvider(llm_answer)).run(item_ids=dev)
    assert next_step(workflow(project)).key == "freeze"
    freeze(project, {"ollama/fake": project.store.calls()[0].config_hash})
    assert next_step(workflow(project)).key == "full"
    engine(project, FakeProvider(llm_answer)).run()
    evaluate(project, subset="test", resamples=20)
    assert next_step(workflow(project)).key == "export"


def test_steps_without_gold_end_with_the_review(ready_project):
    steps = workflow(ready_project)
    assert "split" not in [s.key for s in steps] and "review" in [s.key for s in steps]
    engine(ready_project, FakeProvider(lambda r, n: ans())).run()
    assert next_step(workflow(ready_project)).key == "review"


def _with_alpha(report, value, low, high, n=60):
    main = report.comparisons[0]
    alpha = Estimate(value, low, high, n, 20)
    agreement = replace(main.agreement, alpha=alpha, n=n)
    return replace(report, comparisons=[replace(main, agreement=agreement)])


def test_verdict_traffic_light_and_next_step(project):
    engine(project, FakeProvider(llm_answer)).run()
    report = evaluate(project, resamples=50)
    v = verdict(report, project.config.data.mapping.consensus)
    assert v is not None and v.subset == "all" and not v.has_split
    assert {c.key for c in v.checks} == {"alpha", "humans", "weakest", "basis", "robust"}
    assert v.check("humans").level != "grey"  # zwei Kodierende: Vergleich mit Menschen
    good = verdict(_with_alpha(report, 0.85, 0.80, 0.90))
    assert good.check("alpha").level == "green" and good.next_step in ("split", "more_gold")
    assert verdict(_with_alpha(report, 0.70, 0.60, 0.80)).check("alpha").level == "yellow"
    poor = verdict(_with_alpha(report, 0.50, 0.40, 0.60))
    assert poor.check("alpha").level == "red"
    assert poor.next_step == "sharpen"
    wide = verdict(_with_alpha(report, 0.85, 0.70, 0.95, n=25))
    assert wide.check("basis").level == "yellow"
    test = verdict(replace(_with_alpha(report, 0.85, 0.81, 0.89), subset="test"))
    assert test.next_step == "report"
    assert verdict(replace(report, comparisons=[])) is None


def test_steps_bar_marks_done_next_and_jumps(qtbot, project):
    from llmanno.gui.steps_bar import StepsBar

    bar = StepsBar()
    qtbot.addWidget(bar)
    jumps = []
    bar.go_to.connect(jumps.append)
    bar.show_steps(workflow(project))
    assert "✓" in bar.chain.text() and "<b>●" in bar.chain.text()
    assert "Splits & Freeze" in bar.hint.text()
    bar.button.click()
    bar.chain.linkActivated.emit("schritt:models")
    assert jumps == ["split", "models"]


def test_verdict_box_names_reference_levels_and_next_step(project):
    from llmanno.gui.verdict_view import VerdictText

    engine(project, FakeProvider(llm_answer)).run()
    report = evaluate(project, resamples=50)
    html = VerdictText().html(verdict(_with_alpha(report, 0.50, 0.40, 0.60)))
    assert "Kurzurteil" in html and "zu niedrig" in html and "Konfusionsmatrix" in html
    assert "ohne Aufteilung" in html


def test_main_window_shows_steps_and_new_project_uses_the_wizard(qtbot, project, monkeypatch):
    from llmanno.gui import main_window as mw

    window = mw.MainWindow()
    qtbot.addWidget(window)
    window.show()
    assert not window.steps_bar.isVisible()  # ohne Projekt
    monkeypatch.setattr(mw, "run_setup_wizard", lambda parent: project)
    monkeypatch.setattr(window, "_confirm_discard", lambda: True)
    window.new_project()
    assert window.project is project and window.steps_bar.isVisible()
    assert window.tabs.currentWidget() is window.split_page  # nächster Schritt: aufteilen
    window.steps_action.setChecked(False)
    assert not window.steps_bar.isVisible()
    window.steps_action.setChecked(True)
    create_split(project)
    window.go_to_step("dev")
    assert window.tabs.currentWidget() is window.run_page and window.run_page.dev_only.isChecked()
    window.set_project(None)


def test_evaluation_tab_opens_with_the_short_verdict(qtbot, project):
    from llmanno.gui.evaluation_page import EvaluationPage

    engine(project, FakeProvider(llm_answer)).run()
    page = EvaluationPage()
    qtbot.addWidget(page)
    page.set_project(project, refresh=False)
    page._report = evaluate(project, resamples=20)
    page._render()
    text = page.view.toPlainText()
    assert text.index("Kurzurteil") < text.index("LLM gegen Referenz")


# --- Befunde des Reviews ---------------------------------------------------------------------


def test_failed_calls_and_side_exports_do_not_tick_steps(project):
    from llmanno.core.providers.base import ProviderError

    create_split(project)
    hard = ProviderError("HTTP 401", retryable=False)
    with pytest.raises(Exception, match="5 Fehler"):
        engine(project, FakeProvider(lambda r, n: hard)).run()
    assert next_step(workflow(project)).key == "pilot"  # nur Fehler: nichts kodiert
    project.store.log("exported", format="praeregistrierung", file="p.md")
    assert not dict(_keys(project))["export"]


def test_review_step_needs_blind_reviews_of_the_sample(ready_project):
    from llmanno.core.store import QueueEntry, Review

    engine(ready_project, FakeProvider(lambda r, n: ans())).run()
    ids = sorted(ready_project.store.called_items())
    ready_project.store.replace_queue(
        [QueueEntry(i, n, ["random"] if n else ["konfidenz"], 0.5) for n, i in enumerate(ids)]
    )
    steps = dict(_keys(ready_project))
    assert steps["review"] is False
    for entry in ready_project.store.queue():
        ready_project.store.add_review(Review(entry.item_id, "Ana", "assisted", ["K1"]))
    assert dict(_keys(ready_project))["review"] is False  # assistiert zählt nicht
    for item in ids[1:]:  # nur die Zufallsstichprobe blind
        ready_project.store.add_review(Review(item, "Ben", "blind", ["K1"]))
    assert dict(_keys(ready_project))["review"] is True


def test_verdict_edge_cases(project):
    import math

    from llmanno.core.review import REVIEW_RANDOM

    engine(project, FakeProvider(llm_answer)).run()
    report = evaluate(project, resamples=50)
    nan = verdict(_with_alpha(report, math.nan, math.nan, math.nan))
    assert nan.check("alpha").level == "grey" and nan.check("humans").level == "grey"
    uncertain = verdict(_with_alpha(report, 0.85, 0.60, 0.95))
    assert uncertain.check("alpha").values["cause"] == "uncertain"
    main = report.comparisons[0]
    reviewed = replace(report, baseline=None, comparisons=[replace(main, reference=REVIEW_RANDOM)])
    good = verdict(_with_alpha(reviewed, 0.85, 0.81, 0.89))
    assert good.by_review and good.next_step == "report"  # ohne Gold kein Split
    broken = replace(_with_alpha(report, 0.85, 0.81, 0.89), subset="test", freeze_intact=False)
    assert verdict(broken).next_step == "broken"


def test_verdict_box_numbers_and_partial_robustness(project):
    from llmanno.core.metrics.differential import DifferentialTest
    from llmanno.gui.verdict_view import VerdictText

    engine(project, FakeProvider(llm_answer)).run()
    report = evaluate(project, resamples=50)
    fine = DifferentialTest("Schulform", {}, 0, 0.0, (1, 1), 0.5, False, 0.5)
    report = replace(report, sensitivity=[], differential=[fine])
    html = VerdictText().html(verdict(_with_alpha(report, 0.85, 0.60, 0.95)))
    assert "0,85" in html and "untere Intervallgrenze" in html
    assert "Wortlaut nicht geprüft" in html  # nur Subgruppen geprüft
