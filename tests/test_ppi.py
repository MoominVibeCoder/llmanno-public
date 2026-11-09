import math

import numpy as np
import openpyxl
import pytest
from helpers import FakeProvider, engine
from test_evaluation import TRUTH, make_project
from test_review import shaky_answer

from llmanno.core.evaluation import evaluate
from llmanno.core.export.evaluation import export_evaluation
from llmanno.core.metrics.prevalence import classical_mean, ppi_mean
from llmanno.core.review import RANDOM, add_review, build_queue


def test_ppi_matches_the_closed_form():
    f_u, f_l, y = [1, 0, 1, 1], [1, 0, 1], [1, 1, 1]
    est = ppi_mean(f_u, f_l, y)
    rect = np.array(f_l) - np.array(y)
    theta = np.mean(f_u) - rect.mean()
    se = math.sqrt(np.var(f_u, ddof=1) / 4 + np.var(rect, ddof=1) / 3)
    assert est.value == pytest.approx(theta) == pytest.approx(0.75 + 1 / 3)
    assert est.high - est.value == pytest.approx(1.959964 * se, rel=1e-5)
    assert est.n == 3
    perfect = ppi_mean([1, 0, 0, 1, 1], [1, 0], [1, 0])
    assert perfect.value == pytest.approx(0.6)
    with pytest.raises(ValueError, match="mindestens zwei"):
        ppi_mean([1], [1, 0], [1, 0])


def test_ppi_is_unbiased_and_covers_where_the_llm_is_biased():
    rng = np.random.default_rng(7)
    truth, big_n, n, sims = 0.3, 2000, 150, 400
    thetas, covered, narrower = [], 0, 0
    for _ in range(sims):
        y_all = rng.random(big_n) < truth
        # Das LLM überschätzt den Code: 90 % Treffer, aber 15 % falsch-positiv.
        f_all = np.where(y_all, rng.random(big_n) < 0.9, rng.random(big_n) < 0.15)
        labeled = rng.choice(big_n, n, replace=False)
        mask = np.zeros(big_n, dtype=bool)
        mask[labeled] = True
        est = ppi_mean(f_all[~mask], f_all[mask], y_all[mask])
        thetas.append(est.value)
        covered += est.low <= truth <= est.high
        classic = classical_mean(y_all[mask])
        narrower += (est.high - est.low) < (classic.high - classic.low)
    assert abs(np.mean(thetas) - truth) < 0.01  # naiv läge der Anteil bei 0,375
    assert 0.92 <= covered / sims <= 0.98
    assert narrower / sims > 0.9  # PPI ist enger als die Stichprobe allein


@pytest.fixture
def reviewed(tmp_path):
    project = make_project(tmp_path, run=False)
    engine(project, FakeProvider(shaky_answer)).run()
    project.update(review=project.config.review.model_copy(update={"random_share": 0.5}))
    for entry in build_queue(project):
        add_review(project, entry.item_id, "Ana", "blind", [TRUTH[entry.item_id]])
    yield project
    project.close()


def test_evaluation_reports_ppi_from_the_random_review_sample(reviewed, tmp_path):
    report = evaluate(reviewed, resamples=20)
    drawn = {e.item_id for e in reviewed.store.queue() if RANDOM in e.reasons}
    assert report.prevalence_n == (len(drawn), 60 - len(drawn))
    assert [r.code for r in report.prevalence] == ["K1", "K2", "K3", "KEIN_CODE"]
    k1 = report.prevalence[0]
    assert k1.sample.value == pytest.approx(sum(TRUTH[i] == "K1" for i in drawn) / len(drawn))
    assert k1.ppi.low <= k1.ppi.value <= k1.ppi.high
    assert report.prevalence[-1].ppi.value == pytest.approx(0)  # nie vergeben, nie im Gold

    path = tmp_path / "k.xlsx"
    export_evaluation(report, path)
    rows = list(openpyxl.load_workbook(path)["Prävalenz PPI"].values)
    assert rows[0][:2] == ("Code", "LLM naiv") and rows[1][0] == "K1"


def test_no_ppi_without_a_random_sample(tmp_path):
    project = make_project(tmp_path)
    assert evaluate(project, resamples=20).prevalence == []
    project.close()


def test_ppi_appears_in_the_evaluation_tab_and_report(qtbot, reviewed):
    from llmanno.core.export.report import report_html
    from llmanno.gui.evaluation_page import EvaluationPage

    report = evaluate(reviewed, resamples=20)
    page = EvaluationPage()
    qtbot.addWidget(page)
    page.set_project(reviewed)
    page._show(report, page._stamp())
    assert "Bias-korrigierte Prävalenz (PPI++)" in page.view.toPlainText()
    assert "Bias-korrigierte Prävalenz (PPI++)" in report_html(reviewed, report)


def test_ppi_plus_tunes_lambda_between_sample_and_ppi():
    from llmanno.core.metrics.prevalence import ppi_plus_mean

    rng = np.random.default_rng(3)
    y = rng.random(200) < 0.4
    noise = rng.random(1000) < 0.5  # LLM ohne Bezug zum Gold
    est, lam = ppi_plus_mean(noise[200:], noise[:200], y)
    assert lam < 0.2  # fast nur die Stichprobe
    classic = classical_mean(y)
    assert est.high - est.low == pytest.approx(classic.high - classic.low, rel=0.1)

    good, lam = ppi_plus_mean([1, 0, 1, 1, 0, 1], [1, 0, 1], [1, 0, 1])
    assert 0 < lam <= 1 and good.value == pytest.approx(lam * 4 / 6 + (2 - lam * 2) / 3)
    with pytest.raises(ValueError, match="mindestens zwei"):
        ppi_plus_mean([1], [1, 0], [1, 0])


def test_cluster_robust_ppi_plus_covers_with_transcript_effects():
    """Code-Anteil und LLM-Fehler schwanken je Transkript: ohne Cluster ist das KI zu eng."""
    from llmanno.core.metrics.prevalence import ppi_plus_mean

    rng = np.random.default_rng(11)
    truth, groups, size, n, sims = 0.3, 40, 25, 200, 300
    covered = {"cluster": 0, "plain": 0}
    for _ in range(sims):
        p = rng.beta(3, 7, groups)  # Mittel 0,3
        bias = rng.normal(0, 0.15, groups)
        cluster = np.repeat(np.arange(groups), size)
        y = rng.random(groups * size) < p[cluster]
        f = np.where(
            rng.random(groups * size) < 0.8, y, rng.random(groups * size) < 0.5 + bias[cluster]
        )
        mask = np.zeros(groups * size, dtype=bool)
        mask[rng.choice(groups * size, n, replace=False)] = True
        clustered, _ = ppi_plus_mean(
            f[~mask],
            f[mask],
            y[mask],
            clusters_unlabeled=cluster[~mask],
            clusters_labeled=cluster[mask],
        )
        plain, _ = ppi_plus_mean(f[~mask], f[mask], y[mask])
        covered["cluster"] += clustered.low <= truth <= clustered.high
        covered["plain"] += plain.low <= truth <= plain.high
    assert 0.90 <= covered["cluster"] / sims <= 0.98
    assert covered["plain"] / sims < 0.92  # ohne Cluster zu eng (hier 88 %)
