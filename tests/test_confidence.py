"""Konfidenz prüfen: AURC, isotone Rekalibrierung, Panel-Signal, Trefferquote je Läufe."""

import numpy as np
import pytest
from helpers import FakeProvider, engine
from sklearn.isotonic import IsotonicRegression
from test_evaluation import make_project
from test_review import shaky_answer

from llmanno.core.config import ModelConfig
from llmanno.core.evaluation import evaluate
from llmanno.core.metrics.calibration import (
    aurc,
    calibration_report,
    cross_fitted_isotonic,
    isotonic_fit,
    isotonic_predict,
)
from llmanno.core.providers.base import ModelInfo


def test_isotonic_fit_matches_sklearn():
    rng = np.random.default_rng(5)
    x = np.round(rng.random(300), 2)  # mit Gleichständen
    y = (rng.random(300) < x**2).astype(float)
    xs, fitted = isotonic_fit(x, y)
    ours = isotonic_predict(xs, fitted, x)
    theirs = IsotonicRegression(out_of_bounds="clip").fit(x, y).predict(x)
    assert ours == pytest.approx(theirs)
    assert np.all(np.diff(fitted) >= 0)


def test_aurc_rewards_a_ranking_that_puts_errors_last():
    good = aurc([0.9, 0.8, 0.2, 0.1], [1, 1, 0, 0])
    assert good == pytest.approx((0 + 0 + 1 / 3 + 2 / 4) / 4)
    assert aurc([0.1, 0.2, 0.8, 0.9], [1, 1, 0, 0]) > good
    assert aurc([0.5] * 4, [1, 1, 0, 0]) == pytest.approx(0.5)  # Gleichstand: Fehlerrate


def test_recalibration_repairs_overconfidence():
    rng = np.random.default_rng(1)
    conf = rng.random(2000)
    hit = rng.random(2000) < conf**3  # das Modell ist viel zu sicher
    report = calibration_report(conf, hit)
    assert report.ece > 0.15 and report.ece_recalibrated < 0.05
    recal = cross_fitted_isotonic(conf, hit.astype(float))
    assert recal.shape == conf.shape and 0 <= recal.min() and recal.max() <= 1
    assert np.isnan(calibration_report([0.9] * 10, [True] * 10).ece_recalibrated)  # zu wenige


def test_evaluation_reports_runs_levels_and_the_panel_signal(tmp_path):
    project = make_project(tmp_path, run=False)
    engine(project, FakeProvider(shaky_answer)).run()  # adaptiv: 2/2 oder 5 Läufe
    report = evaluate(project, resamples=20)
    main = next(c for c in report.comparisons if c.reference == "A")
    levels = {label: n for label, n, _ in main.by_runs}
    assert "2/2" in levels and any(label.endswith("/5") for label in levels)
    assert sum(levels.values()) == main.agreement.n
    assert set(main.calibration) == {"p_sc", "p_verbal"}
    assert 0 <= main.calibration["p_sc"].aurc <= 1
    project.close()

    class Two(FakeProvider):
        def list_models(self):
            return [ModelInfo("fake", "d", 32768), ModelInfo("other", "d", 32768)]

    panel = make_project(tmp_path / "zwei", run=False)
    panel.update(panel=[*panel.config.panel, ModelConfig(provider="ollama", model="other")])
    engine(panel, Two(shaky_answer)).run()
    main = next(c for c in evaluate(panel, resamples=20).comparisons if c.reference == "A")
    assert "panel" in main.calibration
    assert all("(N " in label for label, _, _ in main.by_runs)  # Panel: Mittel über Modelle
    panel.close()
