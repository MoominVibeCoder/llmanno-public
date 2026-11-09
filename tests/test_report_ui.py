"""Roadmap 38: Report ohne Auswertung und im Export-Tab; Grafiken und Methodenabsatz in der
Evaluation."""

import math
from xml.etree import ElementTree as ET

import pytest
from test_evaluation import make_project

from llmanno.core.evaluation import PrevalenceRow, evaluate
from llmanno.core.export.plots import prevalence_svg, reliability_svg, risk_coverage_svg
from llmanno.core.export.report import report_html
from llmanno.core.metrics.bootstrap import Estimate
from llmanno.core.metrics.calibration import aurc, calibration_report
from llmanno.core.project import Project
from llmanno.gui.main_window import MainWindow


@pytest.fixture
def project(tmp_path):
    project = make_project(tmp_path, coders=("A", "B", "C"))
    yield project
    project.close()


def test_risk_coverage_curve_matches_aurc():
    conf = [0.9, 0.8, 0.8, 0.6, 0.4, 0.3]
    hit = [True, True, False, True, False, False]
    cal = calibration_report(conf, hit)
    assert cal.curve[-1] == (1.0, pytest.approx(0.5))  # alle angenommen: Fehlerrate gesamt
    assert cal.curve[0] == (pytest.approx(1 / 6), 0.0)
    assert sum(r for _, r in cal.curve) / len(cal.curve) == pytest.approx(aurc(conf, hit))
    big = calibration_report([i / 1000 for i in range(1000)], [i % 3 > 0 for i in range(1000)])
    assert len(big.curve) == 100 and big.curve[-1][0] == 1.0


def test_plots_are_valid_svg():
    cal = calibration_report([0.9, 0.7, 0.2, 0.5], [True, False, False, True])
    nan = Estimate(0.65, math.nan, math.nan, 30, 0)
    rows = [
        PrevalenceRow("K1", 0.4, Estimate(0.35, 0.2, 0.5, 30, 0), Estimate(0.38, 0.3, 0.46, 90, 0)),
        PrevalenceRow("K2", 0.6, nan, Estimate(0.6, 0.5, 0.7, 90, 0)),
    ]
    for svg in (reliability_svg("A", cal), risk_coverage_svg("A", cal), prevalence_svg(rows)):
        root = ET.fromstring(svg)
        assert root.tag.endswith("svg") and float(root.get("width")) > 0
    assert prevalence_svg(rows).count("<circle") >= 6 + 3  # Legende + Punkte


def test_html_report_shows_risk_coverage(project):
    html = report_html(project, evaluate(project, resamples=20))
    assert "Risk-Coverage-Kurve" in html and "Reliability-Diagramm" in html


def test_evaluation_page_shows_plots_and_normalized_confusion(qtbot, project):
    window = MainWindow()
    qtbot.addWidget(window)
    window.set_project(Project.open(project.folder))
    page = window.evaluation_page
    page.resamples.setValue(100)
    page.compute()
    qtbot.waitUntil(lambda: page._report is not None, timeout=60000)
    qtbot.waitUntil(lambda: not page.busy)
    assert page.view.images and all(not image.isNull() for image in page.view.images.values())
    assert "plot:" in page.view.toHtml()
    assert " %" not in page.view.toPlainText().split("Konfusionsmatrix")[1].split("Kalibrierung")[0]
    page.normalized.setChecked(True)
    matrix = page.view.toPlainText().split("Konfusionsmatrix")[1].split("Kalibrierung")[0]
    assert "100 %" in matrix or "%" in matrix
    old = set(page.view.images)
    page.normalized.setChecked(False)
    assert not old & set(page.view.images)  # neue Namen, alte Bilder verworfen

    page.methods_lang.setCurrentIndex(page.methods_lang.findData("en"))
    page.copy_methods()
    from PySide6.QtGui import QGuiApplication

    text = QGuiApplication.clipboard().text()
    assert "fake" in text and "Cohen" in text
    assert "ohne Kennwerte" not in page.status.text()
    window.set_project(None)


def test_report_without_evaluation_from_both_tabs(qtbot, project, tmp_path):
    window = MainWindow()
    qtbot.addWidget(window)
    window.set_project(Project.open(project.folder))
    export = window.export_page
    export.refresh()
    assert export.report_button.isEnabled()
    export.export_report_to(tmp_path / "pilot.html")
    qtbot.waitUntil(lambda: not export.busy, timeout=60000)
    assert not export.error.text()
    assert "Keine Auswertung berechnet" in (tmp_path / "pilot.html").read_text(encoding="utf-8")

    page = window.evaluation_page
    page.refresh()
    assert page.report_button.isEnabled() and not page.export_button.isEnabled()
    page.export_report_to(tmp_path / "pilot2.html")
    qtbot.waitUntil(lambda: not page.busy, timeout=60000)
    assert "ohne Kennwerte" in page.status.text()
    page.copy_methods()
    assert "ohne Kennwerte" in page.status.text()
    window.set_project(None)
