"""Splits & Freeze durch die Oberfläche: Split, Dev-Lauf, Freeze, Test-Auswertung, Aufheben."""

import pytest
from helpers import FakeProvider
from PySide6.QtWidgets import QMessageBox
from test_evaluation import llm_answer, make_project

from llmanno.core import engine as engine_module
from llmanno.core.project import Project
from llmanno.core.validation import count_test_evaluations
from llmanno.gui.main_window import MainWindow


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(engine_module, "make_provider", lambda cfg: FakeProvider(llm_answer))
    make_project(tmp_path, run=False).close()
    window = MainWindow()
    qtbot.addWidget(window)
    window.set_project(Project.open(tmp_path / "p"))
    yield window
    window.set_project(None)


def _confirm(monkeypatch):
    """Das Einfrieren fragt vorher nach (Checkliste); hier: Ja."""
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a: QMessageBox.StandardButton.Yes)
    )


def _run(window, qtbot):
    page = window.run_page
    page.refresh()
    page.full_button.click()
    # Nicht nur auf den Thread warten: die Tabs entsperrt erst der finished-Slot.
    qtbot.waitUntil(lambda: page.full_button.isEnabled(), timeout=60000)
    assert not page.error.text()


def test_split_freeze_and_test_evaluation(window, qtbot, tmp_path, monkeypatch):
    project, page = window.project, window.split_page
    page.refresh()
    assert not page.stratify.isEnabled()  # Kontextmodus: gruppenweise
    assert "Entwurf" in page.preview.toPlainText()
    page.dev_share.setValue(30)
    assert project.config.split.dev_share == pytest.approx(0.3)
    page.split_button.click()
    parts = project.store.split()
    dev = {u for u, p in parts.items() if p == "dev"}
    assert dev and page.split_table.rowCount() == 3  # K1, K2, K3
    assert "Dev" in page.split_info.text()

    # Vor dem Freeze: Lauf nur auf Dev, Evaluation nur Dev.
    _run(window, qtbot)
    assert window.run_page.dev_only.isChecked()
    assert {c.item_id for c in project.store.calls()} == dev
    evaluation = window.evaluation_page
    evaluation.refresh()
    assert [evaluation.subset.itemData(i) for i in range(evaluation.subset.count())] == ["dev"]

    assert page.freeze_button.isEnabled()
    _confirm(monkeypatch)
    page.freeze_button.click()
    qtbot.waitUntil(lambda: not page.busy, timeout=30000)
    assert not page.error.text()
    state = project.config.freeze
    assert "intakt" in page.freeze_state.text()
    assert not page.split_button.isEnabled() and not page.freeze_button.isEnabled()
    assert "eingefroren" in window.freeze_label.text()
    assert state.instrument_hash in page.preview.toPlainText()
    page.export_prereg_to(tmp_path / "prereg.md")
    assert state.instrument_hash in (tmp_path / "prereg.md").read_text(encoding="utf-8")

    # Nach dem Freeze: Vollauf über alles, Test-Auswertung nur nach Rückfrage.
    _run(window, qtbot)
    assert not window.run_page.dev_only.isChecked()
    assert {c.item_id for c in project.store.calls()} == set(parts)
    evaluation.refresh()
    evaluation.subset.setCurrentIndex(evaluation.subset.findData("test"))
    evaluation.resamples.setValue(100)
    answers = iter([QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes])
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a: next(answers)))
    evaluation.compute()
    assert evaluation._task is None  # abgelehnt
    evaluation.compute()
    qtbot.waitUntil(lambda: evaluation._report is not None, timeout=60000)
    qtbot.waitUntil(lambda: not evaluation._task.running)
    assert evaluation._report.subset == "test" and count_test_evaluations(project) == 1
    assert "Teilmenge: Test" in evaluation.view.toPlainText()
    assert "1-mal" in evaluation.subset_info.text()

    # Eine Änderung bricht den Freeze; sichtbar in Statuszeile und Tab, aufheben mit Grund.
    seed = project.config.coding.base_seed
    project.update(coding=project.config.coding.model_copy(update={"base_seed": seed + 1}))
    window._update_title()
    assert "gebrochen" in window.freeze_label.text()
    page.refresh()
    assert "gebrochen" in page.freeze_state.text()
    assert not page.unfreeze_button.isEnabled()
    page.reason.setText("Seed geändert, neuer Freeze folgt")
    page.unfreeze_button.click()
    assert project.config.freeze is None
    assert project.store.audit()[-1].details["reason"] == "Seed geändert, neuer Freeze folgt"
    assert not window.freeze_label.text()
    assert not page.split_button.isEnabled()  # Test wurde schon angesehen


def test_freeze_reports_provider_errors(window, qtbot, monkeypatch):
    class Offline(FakeProvider):
        def list_models(self):
            from llmanno.core.providers.base import ProviderError

            raise ProviderError("Verbindung abgelehnt", retryable=False)

    monkeypatch.setattr(engine_module, "make_provider", lambda cfg: Offline(llm_answer))
    page = window.split_page
    page.refresh()
    _confirm(monkeypatch)
    page.freeze_button.click()
    qtbot.waitUntil(lambda: bool(page.error.text()), timeout=30000)
    assert "nicht erreichbar" in page.error.text()
    assert window.project.config.freeze is None
    qtbot.waitUntil(lambda: page.freeze_button.isEnabled())


def test_start_buttons_stay_locked_while_a_run_is_active(window, qtbot):
    window.tabs.setCurrentWidget(window.split_page)  # Sperren der Tabs wechselt den Tab
    page = window.run_page
    page.refresh()
    page.full_button.click()
    assert page.running
    assert not page.full_button.isEnabled() and not page.pilot_button.isEnabled()
    qtbot.waitUntil(lambda: page.full_button.isEnabled(), timeout=60000)
