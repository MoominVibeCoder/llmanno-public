"""P4: confidence-driven inference, alle blinden Reviews nach Ziehwahrscheinlichkeit gewichtet."""

import random

import numpy as np
import openpyxl
import pytest
from helpers import FakeProvider, engine
from test_evaluation import TRUTH, make_project
from test_review import shaky_answer

from llmanno.core.evaluation import evaluate
from llmanno.core.export.evaluation import export_evaluation
from llmanno.core.metrics.prevalence import MIN_TUNING, ppi_plus_mean, weighted_mean
from llmanno.core.review import RANDOM, add_review, build_queue


def test_weighted_mean_closed_form_and_tuning_threshold():
    f, gold, pi = [1, 0, 1, 0], [1, None, 0, None], [1.0, None, 0.5, None]
    est, lam = weighted_mean(f, gold, pi)
    assert lam == 1.0  # zu wenige Zufallsfälle für Power-Tuning
    z = [1.0, 0.0, 0 / 0.5 + 1 * (1 - 2), 0.0]  # λ·f + ξ/π·(y − λ·f)
    assert est.value == pytest.approx(np.mean(z)) and est.n == 2

    rng = np.random.default_rng(1)
    f = rng.random(400) < 0.4
    drawn = np.zeros(400, dtype=bool)
    drawn[: MIN_TUNING + 5] = True
    gold = [float(v) if d else None for v, d in zip(f, drawn, strict=True)]  # LLM fehlerfrei
    est, lam = weighted_mean(f, gold, [0.1 if d else None for d in drawn])
    a = np.where(drawn, f / 0.1, 0.0)
    b = f * (1 - np.where(drawn, 1 / 0.1, 0.0))
    assert lam == pytest.approx(float(np.clip(-np.cov(a, b)[0, 1] / b.var(ddof=1), 0, 1)))
    assert est.value == pytest.approx(float(np.mean(a + lam * b)))

    with pytest.raises(ValueError, match="Ziehwahrscheinlichkeit"):
        weighted_mean([1, 0, 1], [1, 0, None], [1.0, 0.0, None])
    everyone, _ = weighted_mean([0, 0, 0], [1, 0, 1], [1.0, 1.0, 1.0])
    assert everyone.value == pytest.approx(2 / 3)  # alle gelabelt: das Gold zählt


def test_gate_reviews_count_without_bias_and_narrow_the_interval():
    """Wie build_queue: Gate π = 1, dazu eine Zufallsstichprobe fester Größe ohne Zurücklegen.
    Das LLM irrt vor allem bei unsicheren Einheiten, dort ist der Code auch häufiger: Ein
    ungewichteter Anteil aller Reviews wäre verzerrt, der gewichtete nicht."""
    rng, draw = np.random.default_rng(5), random.Random(5)
    big_n, n_random, sims = 2000, 100, 400
    errors, naive, covered, narrower = [], [], 0, 0
    for _ in range(sims):
        unsure = rng.random(big_n) < 0.2
        y = np.where(unsure, rng.random(big_n) < 0.6, rng.random(big_n) < 0.25)
        truth = y.mean()
        err = np.where(unsure, rng.random(big_n) < 0.4, rng.random(big_n) < 0.03)
        f = np.where(err, ~y, y)
        drawn = np.zeros(big_n, dtype=bool)
        drawn[draw.sample(range(big_n), n_random)] = True
        labeled = unsure | drawn
        pi = [(1.0 if u else n_random / big_n) if k else None
              for u, k in zip(unsure, labeled, strict=True)]  # fmt: skip
        gold = [float(v) if k else None for v, k in zip(y, labeled, strict=True)]
        est, _ = weighted_mean(f, gold, pi)
        errors.append(est.value - truth)
        naive.append(y[labeled].mean() - truth)
        covered += est.low <= truth <= est.high
        ppi, _ = ppi_plus_mean(f[~drawn], f[drawn], y[drawn])
        narrower += (est.high - est.low) < (ppi.high - ppi.low)
    assert abs(np.mean(errors)) < 3 * np.std(errors) / np.sqrt(sims) + 0.002
    assert np.mean(naive) > 0.1  # ungewichtet deutlich zu hoch
    assert covered / sims >= 0.93  # für genau diese Einheiten konservativ (Paper-Varianz)
    assert narrower / sims > 0.9


@pytest.fixture
def gated(tmp_path):
    project = make_project(tmp_path, run=False)
    engine(project, FakeProvider(shaky_answer)).run()
    review = project.config.review.model_copy(update={"random_share": 0.3, "tau": 0.7})
    project.update(review=review)
    yield project, build_queue(project)
    project.close()


def _review_all(project, queue):
    for entry in queue:
        add_review(project, entry.item_id, "Ana", "blind", [TRUTH[entry.item_id]])


def test_evaluation_reports_the_weighted_prevalence(gated, tmp_path):
    project, queue = gated
    assert any(set(e.reasons) - {RANDOM} for e in queue)  # es gibt Gate-Fälle
    _review_all(project, queue)
    report = evaluate(project, resamples=20)
    assert report.prevalence_weighted_n == len(queue) and report.prevalence_weighted_open == 0
    k1 = next(r for r in report.prevalence if r.code == "K1")
    assert k1.weighted is not None and k1.weighted.low <= k1.weighted.value <= k1.weighted.high
    assert k1.weighted_lambda == 1.0  # 18 Zufallsfälle: unter der Schwelle fürs Tuning
    path = tmp_path / "k.xlsx"
    export_evaluation(report, path)
    rows = list(openpyxl.load_workbook(path)["Prävalenz PPI"].values)
    assert rows[0][9] == "Alle Reviews gewichtet" and rows[1][9] is not None


def test_open_queue_withholds_the_weighted_prevalence(gated):
    """Review: Auch fehlende Zufallsfälle verzerren die Gewichtung, nicht nur Gate-Fälle."""
    project, queue = gated
    _review_all(project, [e for e in queue if set(e.reasons) - {RANDOM}])  # nur das Gate
    _review_all(project, [e for e in queue if e.reasons == [RANDOM]][:2])  # für PPI++
    report = evaluate(project, resamples=20)
    assert report.prevalence  # PPI++ bleibt
    assert report.prevalence_weighted_n is None
    assert all(r.weighted is None for r in report.prevalence)
    open_ = sum(1 for e in queue if e.reasons == [RANDOM]) - 2
    assert any(f"{open_} Einheiten haben noch keine" in str(w) for w in report.warnings)


def test_only_the_current_queue_counts_after_a_rebuild(gated):
    """Review: Nach einem Neubau stimmen die alten Ziehwahrscheinlichkeiten nicht mehr."""
    project, first = gated
    _review_all(project, first)
    outside = next(i for i in TRUTH if i not in {e.item_id for e in first})
    add_review(project, outside, "Ana", "blind", [TRUTH[outside]])
    project.update(review=project.config.review.model_copy(update={"seed": 2}))
    second = build_queue(project)
    reviewed = {e.item_id for e in first} | {outside}
    _review_all(project, [e for e in second if e.item_id not in reviewed])
    report = evaluate(project, resamples=20)
    assert report.prevalence_weighted_n == len(second)  # frühere Ziehungen zählen nicht
    assert {e.item_id for e in first} - {e.item_id for e in second}  # es gab solche


def test_no_weighted_prevalence_without_a_random_share(gated):
    project, _ = gated
    project.update(review=project.config.review.model_copy(update={"random_share": 0.0}))
    _review_all(project, build_queue(project))
    report = evaluate(project, resamples=20)
    assert report.prevalence_weighted_n is None
    assert all(r.weighted is None for r in report.prevalence)


def test_weighted_prevalence_in_the_tab_and_report(qtbot, gated):
    from llmanno.core.export.report import report_html
    from llmanno.gui.evaluation_page import EvaluationPage

    project, queue = gated
    _review_all(project, queue)
    report = evaluate(project, resamples=20)
    page = EvaluationPage()
    qtbot.addWidget(page)
    page.set_project(project)
    page._show(report, page._stamp())
    assert "Alle Reviews gewichtet" in page.view.toPlainText()
    html = report_html(project, report)
    assert "Alle Reviews gewichtet" in html and "Gligorić" in html
