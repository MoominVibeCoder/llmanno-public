"""Der ganze P1-Ablauf durch die Oberfläche, mit Fake-Anbieter statt Ollama."""

import openpyxl
import pytest
from helpers import TEXTS, FakeProvider, ans, write_xlsx

from llmanno.core import engine as engine_module
from llmanno.core.project import Project
from llmanno.gui.main_window import MainWindow


@pytest.fixture
def window(qtbot, tmp_path):
    window = MainWindow()
    qtbot.addWidget(window)
    window.set_project(Project.create(tmp_path / "studie", name="Studie"))
    return window


def test_full_workflow_through_the_gui(window, qtbot, tmp_path, monkeypatch):
    project = window.project
    data = write_xlsx(
        tmp_path / "daten.xlsx",
        ["ID", "Text", "Coder A"],
        [[i + 1, t, "K1"] for i, t in enumerate(TEXTS)],
    )

    # Daten: ID- und Text-Spalte werden erraten, Gold-Spalte wird angehakt.
    page = window.data_page
    page.open_file(data)
    assert page.id_combo.currentData() == "ID"
    assert page.mapping().text == ["Text"]
    page.gold_list.item(2).setCheckState(page.gold_list.item(2).checkState().Checked)
    page.import_button.click()
    assert project.config.data.n_units == 3
    assert "3 Einheiten importiert" in page.report.item(0).text()

    # Codebuch: Laden schlägt die Codes automatisch vor.
    codebook = tmp_path / "cb.txt"
    codebook.write_text("### K1 — Frage\nText\n\n### K2 — Antwort\nText\n", encoding="utf-8")
    window.codebook_page.load_file(codebook)
    assert [c.id for c in project.config.codebook.codes] == ["K1", "K2"]
    assert window.codebook_page.table.rowCount() == 2

    # Modelle: Modellname eintragen und ins Panel übernehmen.
    models = window.models_page
    models.refresh()
    models.model_combo.setEditText("fake")
    models.add_model_button.click()
    assert [m.key for m in project.config.panel] == ["ollama/fake"]

    # Prompt-Vorschau zeigt das Codebuch verbatim.
    window.prompt_page.refresh()
    assert "### K1 — Frage" in window.prompt_page.system_view.toPlainText()
    assert '<zieltext id="1">' in window.prompt_page.user_view.toPlainText()

    # Lauf im Hintergrund mit Fake-Anbieter; ungespeicherte Änderungen werden vorher gesichert.
    monkeypatch.setattr(
        engine_module, "make_provider", lambda cfg: FakeProvider(lambda r, n: ans())
    )
    run = window.run_page
    run.start(pilot=False)
    assert window.isWindowModified() is False
    assert not window.tabs.isTabEnabled(window.tabs.indexOf(window.data_page))
    qtbot.waitUntil(lambda: not run.running, timeout=15000)
    qtbot.waitUntil(lambda: window.tabs.isTabEnabled(window.tabs.indexOf(window.data_page)))
    assert any("Fertig: 6 Aufrufe" in run.log.item(i).text() for i in range(run.log.count()))

    # Ergebnisse: Tabelle und Detail mit hervorgehobenem Signalwort.
    results = window.results_page
    results.refresh()
    assert results.table.rowCount() == 3
    assert "3 von 3" in results.summary.text()
    html = results.detail_html("1")
    assert "<span style='background:#fde68a;color:#111827'>Bruch</span>" in html
    assert "LLM-Plausibilisierung" in html

    # Export.
    path = tmp_path / "ergebnis.xlsx"
    window.export_page.export_to(path)
    qtbot.waitUntil(lambda: not window.export_page.busy, timeout=60000)
    sheet = openpyxl.load_workbook(path)["Ergebnisse"]
    assert sheet.max_row == 4


def test_data_is_locked_after_coding(window, qtbot, tmp_path, monkeypatch, ready_project):
    window.set_project(Project.open(ready_project.folder))
    monkeypatch.setattr(
        engine_module, "make_provider", lambda cfg: FakeProvider(lambda r, n: ans())
    )
    window.run_page.start(pilot=True)
    qtbot.waitUntil(lambda: not window.run_page.running, timeout=15000)
    qtbot.waitUntil(lambda: window.run_page.full_button.isEnabled())  # Tabs wieder frei
    window.data_page.refresh()
    page = window.data_page
    # Texte sind festgeschrieben; eine Datei wählen geht weiter, um Gold nachzutragen.
    assert not page.import_button.isEnabled() and not page.text_list.isEnabled()
    assert page.choose_button.isEnabled() and page.gold_list.isEnabled()
    assert "festgeschrieben" in page.status_label.text()
    assert "Gold lässt sich nachtragen" in page.status_label.text()


def test_run_errors_are_shown_not_raised(window, qtbot):
    window.run_page.start(pilot=True)  # leeres Projekt: keine Daten
    assert "keine Daten" in window.run_page.error.text()
    assert not window.run_page.running


def test_prompt_page_keeps_fields_it_does_not_change(window, ready_project):
    window.set_project(Project.open(ready_project.folder))
    page = window.prompt_page
    page.refresh()
    page.evidence_repair.setChecked(False)
    assert window.project.config.coding.evidence_repair is False
    page.n_runs.setValue(7)
    assert window.project.config.coding.evidence_repair is False
    assert window.project.config.coding.n_runs == 7
    window.save_project()
