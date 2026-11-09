"""Bedienbarkeit der Tabs Review, Evaluation, Export und des Protokolls: Rückfragen, Gründe
für gesperrte Knöpfe, Klartext und eingeklappte Expertenwerte."""

import pytest
from helpers import FakeProvider, engine
from test_evaluation import make_project
from test_review import shaky_answer

from llmanno.core import review as review_module


@pytest.fixture
def reviewed(tmp_path):
    project = make_project(tmp_path, run=False)
    engine(project, FakeProvider(shaky_answer)).run()
    yield project
    project.close()


def test_review_asks_before_assisted_and_explains_itself(qtbot, reviewed, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QMessageBox

    from llmanno.core.review import SUGGESTION_SHOWN
    from llmanno.gui.review_page import ReviewPage

    page = ReviewPage()
    qtbot.addWidget(page)
    page.set_project(reviewed)
    assert page.position.text().startswith("Noch keine Prüfliste")
    page.make_queue()
    assert page.settings_hint.isHidden()
    page.tau.setValue(0.9)  # gilt erst mit der nächsten Prüfliste
    assert not page.settings_hint.isHidden()
    page.make_queue()  # noch keine Reviews: ohne Rückfrage
    assert page.settings_hint.isHidden()

    # Ohne Namen kein Speichern; der Tooltip sagt, was fehlt.
    page.name.setText("")
    assert not page.save_button.isEnabled() and "Namen" in page.save_button.toolTip()
    page.name.setText("Ana")
    assert page.save_button.isEnabled() and not page.save_button.toolTip()

    # Wechselt die Person selbst auf assistiert, fragt das Programm; „Nein“ bleibt blind.
    asked = []
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a: asked.append(a) or QMessageBox.StandardButton.No
    )
    page.mode.setFocus()
    qtbot.keyClick(page.mode, Qt.Key.Key_Down)  # wie eine Person an der Combo
    assert len(asked) == 1 and page.mode.currentData() == "blind"
    assert not page.suggestion.text()
    assert not any(e.event == SUGGESTION_SHOWN for e in reviewed.store.audit())
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    qtbot.keyClick(page.mode, Qt.Key.Key_Down)
    assert page.mode.currentData() == "assisted"
    assert "Anteil der Durchläufe" in page.suggestion.text() and " %" in page.suggestion.text()

    # Am Ende der Liste nennt der Tab die offenen Einheiten statt „abgeschlossen“.
    page._go(len(page._queue))
    assert "noch ohne Review" in page.position.text()

    # Der Fehler der Rekalibrierung steht beim Schalter, samt Abhilfe.
    monkeypatch.setattr(review_module, "MIN_CALIBRATION", 1000)
    page.recalibrated.setChecked(True)
    assert not page.recalibrated.isChecked()
    assert "Abhilfe" in page.calibration_error.text() and not page.error.text()
    assert not page.calibration_error.isHidden()


def test_evaluation_starts_with_prerequisites_and_core_metrics(qtbot, tmp_path):
    from llmanno.core.evaluation import evaluate
    from llmanno.gui.evaluation_page import EvaluationPage

    project = make_project(tmp_path)  # zwei Kodierende: kein Alt-Test
    page = EvaluationPage()
    qtbot.addWidget(page)
    page.all_metrics.setChecked(False)
    page.set_project(project)
    text = page.view.toPlainText()
    assert "Noch keine Auswertung" in text and "Ergebnisse eines Laufs" in text
    assert not page.export_button.isEnabled()
    assert "Kennwerte berechnen" in page.export_button.toolTip()
    assert not page.epsilon.isEnabled() and "mindestens 3" in page.epsilon.toolTip()
    assert "Dev" in page.stats_button.toolTip()
    assert not page.gold_fold.toggle.isChecked()  # alle Gold-Werte entsprechen einem Code

    page._show(evaluate(project, resamples=20), page._stamp())
    text = page.view.toPlainText()
    assert "Hauptvergleich" in text and "Krippendorffs α: wie κ" in text
    assert "BP/PABAK" not in text and "Über die Läufe eines Modells" not in text
    assert "Konsistenz (p_sc)" in text and "ECE:" in text and "McNemar p:" in text
    page.all_metrics.setChecked(True)
    text = page.view.toPlainText()
    assert "BP/PABAK" in text and "Über die Läufe eines Modells" in text
    assert page.export_button.isEnabled() and "XLSX" not in page.export_button.toolTip()

    # User 2026-10-12: κ je Code nicht gefunden. Die Auswahl springt jetzt zur Tabelle.
    assert "Cohens κ je Code" in page.view.toPlainText()
    page.resize(900, 500)
    page.show()
    bar = page.view.verticalScrollBar()
    bar.setValue(0)
    page.reference.activated.emit(0)
    assert bar.value() > 0
    project.close()


def test_export_defaults_reasons_and_open_folder(qtbot, tmp_path):
    from llmanno.gui.export_page import ExportPage

    project = make_project(tmp_path, run=False)
    page = ExportPage()
    qtbot.addWidget(page)
    page.set_project(project)
    assert not page.ls_predictions.isChecked()  # blind kodieren ist der Normalfall
    assert not page.xlsx_button.isEnabled() and "Tab Lauf" in page.xlsx_button.toolTip()
    assert page.qdpx_button.isEnabled() and "MAXQDA" in page.qdpx_button.toolTip()
    assert "Datenschutz" in page.include_texts.toolTip()
    assert page.open_folder.isHidden()
    page.export_qdc_to(tmp_path / "c.qdc")
    qtbot.waitUntil(lambda: not page.open_folder.isHidden(), timeout=60000)
    qtbot.waitUntil(lambda: not page.busy, timeout=60000)
    assert (tmp_path / "c.qdc").is_file()
    project.close()


def test_audit_dialog_speaks_plain_language(qtbot, tmp_path):
    from llmanno.gui.audit_dialog import AuditDialog

    project = make_project(tmp_path, run=False)
    project.store.log("exported", format="qdc", file="x.qdc", settings={"tau": 0.6})
    dialog = AuditDialog(project)
    qtbot.addWidget(dialog)
    when, event, details = (dialog.table.item(0, c) for c in range(3))
    assert event.text() == "Export" and event.toolTip() == "exported"
    assert details.text() == "format: qdc · file: x.qdc · settings: tau"
    assert '"tau": 0.6' in details.toolTip()
    assert "UTC" in when.toolTip() and "UTC" not in when.text()
    dialog.filter.setText("export")  # Klartext und Schlüssel sind beide durchsuchbar
    assert not dialog.table.isRowHidden(0)
    project.close()
