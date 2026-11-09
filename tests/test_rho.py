"""Shaffers ρ (Nachbau von rhoR::calcRho)."""

import numpy as np
import pytest

from llmanno.core.metrics.rho import _kappa, rho


def _sample(rng, n, baserate, kappa):
    """Gold und LLM mit gegebener Basisrate und κ (gleiche Ränder)."""
    p = np.array([baserate, 1 - baserate])
    table = (1 - kappa) * np.outer(p, p) + kappa * np.diag(p)
    cells = rng.choice(4, size=n, p=table.ravel())
    return cells // 2 == 0, cells % 2 == 0


def test_kappa_and_edge_cases():
    assert _kappa(40, 10, 10, 40) == pytest.approx(0.6)
    assert rho([True] * 5, [True] * 5) is None  # Gold kennt nur eine Kategorie
    assert rho([], []) is None


def test_high_kappa_generalises_low_kappa_does_not():
    rng = np.random.default_rng(1)
    gold, pred = _sample(rng, 200, 0.3, 0.95)
    assert rho(gold, pred) < 0.05
    gold, pred = _sample(rng, 200, 0.3, 0.4)
    assert rho(gold, pred) == 1.0  # unter dem Mittel der Nullverteilung
    assert rho(gold, pred, seed=3) == rho(gold, pred, seed=3)


def test_error_rate_is_controlled_on_average_over_the_null_range():
    """Wie rhoR: 5 % gemittelt über κ zwischen 0,40 und der Schwelle, nicht an der Schwelle."""
    rng = np.random.default_rng(7)
    kappas = rng.uniform(0.4, 0.65, 300)
    hits = [rho(*_sample(rng, 80, 0.3, k), seed=i) < 0.05 for i, k in enumerate(kappas)]
    assert np.mean(hits) <= 0.08
    at_threshold = [rho(*_sample(rng, 80, 0.3, 0.65), seed=i) < 0.05 for i in range(200)]
    assert np.mean(at_threshold) > 0.1  # bekannte Schwäche, in der Anleitung genannt


def test_evaluation_reports_kappa_and_rho_per_code(qtbot, tmp_path):
    import openpyxl
    from test_evaluation import CODES, TRUTH, make_project

    from llmanno.core.engine import results
    from llmanno.core.evaluation import evaluate
    from llmanno.core.export.evaluation import export_evaluation
    from llmanno.core.export.report import report_html
    from llmanno.core.metrics.agreement import cohen_kappa
    from llmanno.gui.evaluation_page import EvaluationPage

    project = make_project(tmp_path)
    report = evaluate(project, resamples=20)
    main = next(c for c in report.comparisons if c.reference == "A")
    values = {c: v for c, v in main.rho.items() if v is not None}  # Restkategorie: nie im Gold
    assert set(values) == set(CODES) and all(0 <= v <= 1 for v in values.values())
    ids = sorted(TRUTH)
    llm = {i: r.final[0] for i, r in results(project, ids).items()}
    gold = [TRUTH[i] == "K1" for i in ids]
    assert main.code_kappa["K1"] == pytest.approx(cohen_kappa(gold, [llm[i] == "K1" for i in ids]))
    export_evaluation(report, tmp_path / "k.xlsx")
    header = next(openpyxl.load_workbook(tmp_path / "k.xlsx")["Je Code"].values)
    assert header[-2:] == ("Cohens κ je Code (ja/nein)", "ρ (κ > 0.65)")
    assert "Shaffer" in report_html(project, report)
    page = EvaluationPage()
    qtbot.addWidget(page)
    page.set_project(project)
    page._show(report, page._stamp())
    assert "rhoR" in page.view.toPlainText()
    project.close()


def _table(tp, fn, fp, tn):
    gold = [True] * (tp + fn) + [False] * (fp + tn)
    pred = [True] * tp + [False] * fn + [True] * fp + [False] * tn
    return gold, pred


@pytest.mark.parametrize(
    ("table", "expected"),
    [((1, 0, 0, 19), 0.492), ((2, 0, 0, 48), 0.244), ((4, 1, 0, 95), 0.048)],
)
def test_matches_rhoR_for_rare_codes(table, expected):
    """Review: Werte von rhoR 1.3.1 (rhoCT, Mittel über 30 Seeds). Test-Sets nur aus Treffern
    oder nur aus gemeinsamen Nein zählen dort als κ = 1, nicht als undefiniert."""
    values = [rho(*_table(*table), seed=s) for s in range(20)]
    assert np.mean(values) == pytest.approx(expected, abs=0.03)


def test_limits():
    gold = [True] * 2000 + [False] * 8001
    assert rho(gold, gold) is None  # mehr Einheiten als die simulierte Grundgesamtheit
    with pytest.raises(ValueError, match="Schwelle"):
        rho([True, False], [True, False], threshold=1.0)


def test_more_units_give_smaller_rho():
    rng = np.random.default_rng(2)
    gold, pred = _sample(rng, 400, 0.3, 0.8)
    assert rho(gold[:400], pred[:400]) <= rho(gold[:60], pred[:60])
