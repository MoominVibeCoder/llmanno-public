"""Stichprobenrechner für die Gold-Größe (PLAN v2-Backlog)."""

import numpy as np
import pytest

from llmanno.core.metrics.agreement import cohen_kappa
from llmanno.core.metrics.sample_size import (
    MIN_OUTSIDE,
    MIN_UNITS,
    expected_half_width,
    kappa_table,
    kappa_variance,
    units_needed,
)


def test_variance_matches_the_null_case_and_the_table_has_the_kappa():
    assert kappa_variance(kappa_table([0.5, 0.5], 0.0)) == pytest.approx(1.0)  # 1/n unter H0
    table = kappa_table([0.5, 0.3, 0.2], 0.7)
    p_o, p_e = np.trace(table), table.sum(1) @ table.sum(0)
    assert (p_o - p_e) / (1 - p_e) == pytest.approx(0.7)


@pytest.mark.parametrize(("shares", "kappa"), [([0.5, 0.3, 0.2], 0.7), ([0.9, 0.1], 0.6)])
def test_variance_matches_a_simulation(shares, kappa):
    rng = np.random.default_rng(3)
    n, k = 300, len(shares)
    cells = kappa_table(shares, kappa).ravel()
    estimates = []
    for _ in range(3000):
        drawn = rng.choice(k * k, size=n, p=cells)
        estimates.append(cohen_kappa(list(drawn // k), list(drawn % k)))
    assert np.std(estimates) == pytest.approx(
        np.sqrt(kappa_variance(kappa_table(shares, kappa)) / n), rel=0.08
    )


def test_gold_basis_uses_the_reference_at_the_level_of_the_evaluation(tmp_path):
    from test_evaluation import make_project

    from llmanno.core.validation import create_split, gold_basis

    project = make_project(tmp_path, run=False)
    basis = gold_basis(project)
    assert basis.from_gold and basis.current_test == 0
    assert sorted(basis.counts) == [20, 20, 20]  # Coder A: gleich verteilt
    parts = create_split(project)
    assert gold_basis(project).current_test == sum(p == "test" for p in parts.values())
    project.update(mode="multi")
    assert sorted(gold_basis(project).counts) == [20, 20, 20]  # je Code-Menge wie κ im Bericht
    project.close()


def test_small_samples_get_a_floor():
    """Review: Die Großstichproben-Näherung trägt nicht bei wenigen Einheiten."""
    assert units_needed([0.5, 0.5], 0.95, 0.3) == MIN_UNITS
    assert units_needed([0.98, 0.02], 0.95, 0.3) >= MIN_OUTSIDE / 0.02


def test_split_tab_shows_the_plan(qtbot, tmp_path):
    from test_evaluation import make_project

    from llmanno.gui.split_page import SplitPage

    project = make_project(tmp_path, run=False)
    page = SplitPage()
    qtbot.addWidget(page)
    page.set_project(project)
    page.refresh()
    assert "Einheiten mit Gold" in page.plan_info.text()
    before = page.plan_info.text()
    page.plan_width.setValue(0.05)
    assert page.plan_info.text() != before  # rechnet beim Ändern neu
    project.close()


def test_units_needed_and_half_width_are_inverse():
    n = units_needed([0.5, 0.3, 0.2], 0.7, 0.1)
    assert expected_half_width([0.5, 0.3, 0.2], 0.7, n) <= 0.1
    assert expected_half_width([0.5, 0.3, 0.2], 0.7, n - 1) > 0.1
    rare, even = units_needed([0.95, 0.05], 0.7, 0.1), units_needed([0.5, 0.5], 0.7, 0.1)
    assert rare > even  # seltene Codes brauchen mehr Gold
