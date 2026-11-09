"""Bedienbarkeit der Kodier-Tabs: Rückfragen, Hinweise auf Voraussetzungen, Klartext."""

import pytest
from helpers import FakeProvider
from PySide6.QtWidgets import QMessageBox
from test_evaluation import llm_answer, make_project

from llmanno.core import engine as engine_module
from llmanno.core.config import Code, ModelConfig
from llmanno.core.engine import Engine, prepare_prompts
from llmanno.core.project import Project
from llmanno.core.validation import create_split


def _answer(monkeypatch, button):
    asked = []
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a: asked.append(a[2]) or button)
    )
    return asked


def test_freeze_asks_with_a_checklist_and_warns_without_split(qtbot, tmp_path, monkeypatch):
    from llmanno.gui.split_page import SplitPage

    monkeypatch.setattr(engine_module, "make_provider", lambda cfg: FakeProvider(llm_answer))
    project = make_project(tmp_path)
    page = SplitPage()
    qtbot.addWidget(page)
    page.set_project(project)
    asked = _answer(monkeypatch, QMessageBox.StandardButton.No)
    page.freeze_button.click()
    assert "keine Aufteilung" in asked[0] and project.config.freeze is None and not page.busy

    create_split(project)
    page.refresh()
    page.freeze_button.click()
    assert "Übungsteil kodiert" in asked[1] and "bricht" in asked[1]
    assert project.config.freeze is None

    _answer(monkeypatch, QMessageBox.StandardButton.Yes)
    page.freeze_button.click()
    qtbot.waitUntil(lambda: not page.busy, timeout=30000)
    assert project.config.freeze is not None
    assert not page.split_button.isEnabled() and "eingefroren" in page.split_lock.text()
    project.close()


def test_prompt_tab_explains_examples_runs_and_budget(qtbot, tmp_path):
    from llmanno.gui.prompt_page import PromptPage

    project = make_project(tmp_path, run=False)
    page = PromptPage()
    qtbot.addWidget(page)
    page.set_project(project)
    assert not page.examples_button.isEnabled()
    assert "Aufteilung" in page.examples_button.toolTip()
    create_split(project)
    page.refresh()
    assert page.examples_button.isEnabled()

    # Durchläufe ab 1 gezählt, wie im Lauf-Tab; der Startwert bleibt der des ersten.
    assert page.run_spin.value() == 1
    assert page.run_spin.maximum() == project.config.coding.n_runs
    seed = project.config.coding.base_seed
    assert f"Durchlauf 1 (Startwert {seed})" in page.stats.text()
    # Gesamtzahl und noch Offenes getrennt, Token-Details im Tooltip.
    assert "Insgesamt:" in page.stats.text() and "Noch offen:" in page.stats.text()
    assert "Token hin" in page.stats.toolTip()
    project.close()


def test_run_tab_shows_phase_and_what_the_button_codes(qtbot, tmp_path):
    from llmanno.gui.run_page import RunPage

    project = make_project(tmp_path)
    page = RunPage()
    qtbot.addWidget(page)
    page.set_project(project)
    assert "Noch keine Aufteilung" in page.phase.text()
    assert "60 von 60 Einheiten" in page.phase.text()
    assert page.full_button.text() == "Vollauf starten / fortsetzen"
    assert "Prüfteil" in page.full_button.toolTip()

    create_split(project)
    page.refresh()
    assert "Übungsphase" in page.phase.text() and "Übungsteil" in page.phase.text()
    assert page.dev_only.isChecked() and "Übungsteil" in page.full_button.text()
    page.dev_only.setChecked(False)
    assert page.full_button.text() == "Vollauf starten / fortsetzen"
    project.close()


def test_missing_prerequisites_name_the_tab(tmp_path):
    project = Project.create(tmp_path / "p", name="Leer")
    with pytest.raises(ValueError, match="Tab Daten"):
        prepare_prompts(project)
    project.close()

    project = make_project(tmp_path / "a", run=False)
    project.update(panel=[])
    with pytest.raises(ValueError, match="Tab Modelle"):
        Engine(project)
    codebook = project.config.codebook
    codes = [Code(id=c.id, active=False) for c in codebook.codes]
    project.update(
        codebook=codebook.model_copy(update={"codes": codes}),
        panel=[ModelConfig(provider="ollama", model="fake")],
    )
    with pytest.raises(ValueError, match="Tab Codebuch"):
        prepare_prompts(project)
    project.close()


def test_results_speak_plain_language(qtbot, tmp_path):
    from llmanno.gui.results_page import ResultsPage

    page = ResultsPage()
    qtbot.addWidget(page)
    empty = make_project(tmp_path / "leer", run=False)
    page.set_project(empty)
    assert "Noch nichts kodiert" in page.summary.text()
    assert "anklicken" in page.detail.toPlainText()
    empty.close()

    project = make_project(tmp_path)
    page.set_project(project)
    headers = [page.table.horizontalHeaderItem(c).text() for c in range(page.table.columnCount())]
    assert "LLM-Code" in headers and "Hinweise" in headers
    assert any("Konsistenz" in h and "p_sc" in h for h in headers)
    assert page._flags_text(["dissens", "ungueltige_laeufe", "neu"]) == (
        "Dissens im Panel, mit ungültigen Durchläufen, neu"
    )
    unsure = page.flag_combo.findData("view:unsure")
    assert "Prüfung" in page.flag_combo.itemText(unsure)
    assert "Gate" in page.flag_combo.itemData(unsure, 3)  # Tooltip

    page.table.selectRow(0)
    assert "Durchläufe" in page.detail.toPlainText()
    assert "Roh-Antworten" not in page.detail.toPlainText()
    page.show_raw.setChecked(True)
    assert "Roh-Antworten" in page.detail.toPlainText()
    project.close()
