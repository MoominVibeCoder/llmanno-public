import pytest
from helpers import write_xlsx
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from llmanno.core import providers
from llmanno.core.config import GWDG_CHAT_AI
from llmanno.core.providers import is_thinking_model
from llmanno.core.providers.base import ModelInfo
from llmanno.gui.setup_wizard import SetupWizard

KEY = "geheim-123-key"


@pytest.fixture(autouse=True)
def keyring(monkeypatch):
    """Nie den echten Schlüsselbund berühren."""
    stored = {}
    monkeypatch.setattr(
        providers, "set_api_key", lambda config, key: stored.__setitem__(config.base_url, key)
    )
    monkeypatch.setattr(providers, "get_api_key", lambda config: stored.get(config.base_url))
    return stored


class FakeProvider:
    def __init__(self, calls, config, key):
        calls.append((config.key, key))

    def list_models(self):
        return [ModelInfo("gpt-oss-120b"), ModelInfo("llama-3.3-70b-instruct")]

    def close(self):
        pass


@pytest.fixture
def calls():
    return []


@pytest.fixture
def wizard(qtbot, tmp_path, calls):
    wizard = SetupWizard(
        provider_factory=lambda config, key: FakeProvider(calls, config, key),
        folder=tmp_path / "projekt",
    )
    qtbot.addWidget(wizard)
    wizard.show()
    return wizard


@pytest.fixture
def files(tmp_path):
    data = write_xlsx(
        tmp_path / "daten.xlsx",
        ["ID", "Transkript", "Sprecher", "Text", "Coder A"],
        [
            [1, "T1", "L", "Was ist ein Bruch?", "K1"],
            [2, "T1", "S", "Ein Bruch ist ein Teil.", "K2"],
            [3, "T2", "S", "Bruch heißt Teil.", "K2"],
        ],
    )
    book = tmp_path / "codebuch.md"
    book.write_text("# Codebuch\n\n### K1 — Frage\n\n### K2 — Antwort\n", encoding="utf-8")
    return data, book


def to_model_page(wizard, files):
    data, book = files
    wizard.next()
    page = wizard.project_page
    page.name_edit.setText("Studie")
    page.multi_radio.setChecked(True)
    assert page.isComplete()
    wizard.next()

    page = wizard.data_page
    assert not page.isComplete()
    page.open_file(data)
    assert page.text_combo.currentData() == "Text"  # geraten wie im Tab Daten
    assert page.id_combo.currentData() == "ID"
    assert page.unit_box.isHidden()
    page.group_combo.setCurrentIndex(page.group_combo.findData("Transkript"))
    assert not page.unit_box.isHidden()
    page.unit_combo.setCurrentIndex(page.unit_combo.findData("context"))
    page.speaker_combo.setCurrentIndex(page.speaker_combo.findData("Sprecher"))
    page.gold_list.findItems("Coder A", Qt.MatchFlag.MatchExactly)[0].setCheckState(
        Qt.CheckState.Checked
    )
    assert page.isComplete()
    wizard.next()

    page = wizard.codebook_page
    assert not page.isComplete()
    page.open_file(book)
    assert page.table.rowCount() == 2
    assert page.table.item(1, 1).text() == "Antwort"
    assert page.isComplete()
    wizard.next()
    assert wizard.currentPage() is wizard.model_page


def test_wizard_creates_project_with_data_codebook_and_model(wizard, files, qtbot, calls, keyring):
    to_model_page(wizard, files)
    page = wizard.model_page
    assert page.isComplete()  # „später“ ist vorgewählt
    page.choice.button(1).setChecked(True)  # GWDG/KISSKI
    assert not page.isComplete()
    page.key_edit.setText(KEY)
    page.test_connection()
    qtbot.waitUntil(lambda: page.model_combo.count() == 2)
    assert calls == [("gwdg", KEY)]
    assert keyring == {}  # erst beim Fertigstellen
    page.model_combo.setCurrentText("gpt-oss-120b")
    assert page.isComplete()
    wizard.next()
    assert wizard.currentPage() is wizard.finish_page
    assert "Splits & Freeze" in wizard.finish_page.next_step.text()
    assert wizard.project is None and not (wizard.project_page.folder()).exists()

    wizard.accept()
    assert wizard.result() == QDialog.DialogCode.Accepted
    project = wizard.project
    assert project is not None
    try:
        config = project.config
        assert config.name == "Studie" and config.mode == "multi"
        mapping = config.data.mapping
        assert (mapping.id, mapping.text, mapping.group) == ("ID", ["Text"], "Transkript")
        assert (mapping.speaker, mapping.gold) == ("Sprecher", ["Coder A"])
        assert config.data.n_units == 3 and len(project.store.units()) == 3
        assert config.coding.units.mode == "context"
        assert [(c.id, c.label) for c in config.codebook.codes] == [
            ("K1", "Frage"),
            ("K2", "Antwort"),
        ]
        assert config.codebook.abstain is True
        assert [p.key for p in config.providers] == ["ollama", "gwdg"]
        [model] = config.panel
        assert (model.provider, model.model, model.max_tokens) == ("gwdg", "gpt-oss-120b", 4096)
        assert keyring == {GWDG_CHAT_AI.base_url: KEY}
        assert KEY not in (project.folder / "project.json").read_text(encoding="utf-8")
    finally:
        project.close()
    for path in project.folder.rglob("*"):
        if path.is_file():
            assert KEY.encode() not in path.read_bytes(), path.name


def test_model_page_can_be_skipped(wizard, files, keyring):
    to_model_page(wizard, files)
    assert wizard.model_page.isComplete()
    wizard.next()
    assert "Tab Modelle" in wizard.finish_page.next_step.text()
    wizard.accept()
    project = wizard.project
    assert project is not None
    try:
        assert project.config.panel == []
        assert [p.key for p in project.config.providers] == ["ollama"]
        assert keyring == {}
    finally:
        project.close()


def test_non_empty_folder_is_rejected(wizard, tmp_path):
    full = tmp_path / "voll"
    full.mkdir()
    (full / "notiz.txt").write_text("x", encoding="utf-8")
    wizard.next()
    page = wizard.project_page
    page.name_edit.setText("Studie")
    page.folder_edit.setText(str(full))
    assert not page.isComplete()
    assert "nicht leer" in page.folder_error.text()
    wizard.next()
    assert wizard.currentPage() is page
    page.folder_edit.setText(str(full / "neu"))
    assert page.isComplete()


def test_failure_after_creating_is_shown_and_closes_the_project(wizard, files):
    to_model_page(wizard, files)
    wizard.next()
    files[0].unlink()  # Daten zwischen Assistent und Fertigstellen verschwunden
    wizard.accept()
    assert wizard.project is None
    assert wizard.result() != QDialog.DialogCode.Accepted
    assert "nicht fertig eingerichtet" in wizard.finish_page.error.text()
    folder = wizard.project_page.folder()
    assert (folder / "project.json").is_file()  # nichts gelöscht
    assert not (folder / "project.lock").exists()  # aber geschlossen


def test_thinking_models_are_recognized():
    for name in ("gpt-oss-120b", "OpenAI GPT OSS 120B", "qwen3.8-27b", "o3-mini", "deepseek-r1"):
        assert is_thinking_model(name), name
    for name in ("llama-3.3-70b-instruct", "gemma3:4b", "mistral-large"):
        assert not is_thinking_model(name), name


def test_key_belongs_to_the_provider_it_was_typed_for(wizard, files, qtbot, calls, keyring):
    """Review: ein GWDG-Key ging beim Wechsel auf Ollama mit (Test und Schlüsselbund)."""
    to_model_page(wizard, files)
    page = wizard.model_page
    page.choice.button(1).setChecked(True)  # GWDG/KISSKI
    page.test_connection()
    assert "API-Key" in page.error.text() and calls == []  # ohne Key kein Versuch
    page.key_edit.setText(KEY)
    page.choice.button(0).setChecked(True)  # Ollama
    assert page.key_edit.text() == ""
    page.test_connection()
    qtbot.waitUntil(lambda: page.model_combo.count() == 2)
    assert calls == [("ollama", "")]
    page.model_combo.setCurrentText("llama-3.3-70b-instruct")
    wizard.next()
    wizard.accept()
    assert keyring == {}  # kein Key unter der Ollama-Adresse
    wizard.project.close()
