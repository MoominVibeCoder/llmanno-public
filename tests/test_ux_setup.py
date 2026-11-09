"""Einstieg und Einrichtung: Hinweise, Rückfragen und Vorgaben in Projekt, Daten, Codebuch und
Modelle."""

import keyring
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox
from test_evaluation import make_project

from llmanno.core.config import GWDG_CHAT_AI, OLLAMA_LOCAL
from llmanno.core.project import Project
from llmanno.core.providers import THINKING_TOKENS, is_thinking_model
from llmanno.core.providers.base import ModelInfo, ProviderError
from llmanno.gui.models_page import ModelsPage


@pytest.fixture
def models(qtbot, tmp_path):
    project = make_project(tmp_path, run=False)
    page = ModelsPage()
    qtbot.addWidget(page)
    page.set_project(project)
    page.provider_list.setCurrentRow(0)
    yield page
    project.close()


def _add(page, name, capabilities=("completion",)):
    provider = page._selected()
    page._models[provider.key] = [ModelInfo(name, capabilities=capabilities)]
    page.model_combo.setEditText(name)
    page._add_to_panel()
    return page.project.config.panel[-1]


@pytest.mark.parametrize(
    "name, thinking",
    [
        ("gpt-oss:20b", True), ("openai/gpt-oss-120b", True), ("gpt-5-mini", True),
        ("o3-mini", True), ("qwen3:8b", True), ("deepseek-r1:7b", True),
        ("magistral-small", True), ("phi4-reasoning", True), ("llama3.1:8b", False),
        ("gemma3:4b", False), ("qwen2.5vl:3b", False), ("mistral-large-instruct", False),
    ],
)  # fmt: skip
def test_thinking_models_are_recognized_by_name(name, thinking):
    assert is_thinking_model(name) is thinking


def test_thinking_models_get_room_for_their_reasoning(models):
    assert _add(models, "gpt-oss:20b").max_tokens == THINKING_TOKENS
    assert str(THINKING_TOKENS) in models.notes.text()
    assert _add(models, "eigen:7b", ("completion", "thinking")).max_tokens == THINKING_TOKENS
    assert _add(models, "llama3.1:8b").max_tokens == 512


def test_removing_a_provider_asks_when_panel_models_go_too(models, monkeypatch):
    answers = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a: answers.pop(0))
    answers.append(QMessageBox.StandardButton.No)
    models._remove_provider()
    assert models.project.config.panel and models.project.config.providers
    answers.append(QMessageBox.StandardButton.Yes)
    models._remove_provider()
    assert not models.project.config.panel and not models.project.config.providers


def test_removing_an_unused_provider_does_not_ask(models):
    models.project.update(providers=[*models.project.config.providers, GWDG_CHAT_AI])
    models.refresh()
    models.provider_list.setCurrentRow(models.provider_list.count() - 1)
    models._remove_provider()  # ohne Panel-Modelle: keine Rückfrage (sonst scheitert der Test)
    assert GWDG_CHAT_AI.key not in {p.key for p in models.project.config.providers}


def test_connection_errors_name_the_way_out(models):
    down = ProviderError("Verbindung fehlgeschlagen", retryable=True)
    assert "Läuft Ollama" in models.connection_hint(down, OLLAMA_LOCAL)
    assert "nicht erreichbar" in models.connection_hint(down, GWDG_CHAT_AI)
    denied = ProviderError("HTTP 401: nö", retryable=False, status=401)
    assert "API-Key" in models.connection_hint(denied, GWDG_CHAT_AI)
    models._failed(denied, GWDG_CHAT_AI)
    assert "API-Key" in models.error.text() and "HTTP 401" in models.error.text()


def test_typed_key_is_saved_before_the_connection_test(models, monkeypatch):
    vault = {}
    monkeypatch.setattr(keyring, "get_password", lambda s, u: vault.get((s, u)))
    monkeypatch.setattr(keyring, "set_password", lambda s, u, p: vault.__setitem__((s, u), p))
    started = []
    monkeypatch.setattr(models, "_start", lambda *a: started.append(a))
    models.project.update(providers=[*models.project.config.providers, GWDG_CHAT_AI])
    models.refresh()
    models.provider_list.setCurrentRow(models.provider_list.count() - 1)
    models.key_edit.setText("geheim-123")
    models._test_connection()
    assert started and vault == {("llmanno", GWDG_CHAT_AI.base_url): "geheim-123"}
    assert models.key_edit.text() == ""
    assert "geheim-123" not in str([e.details for e in models.project.store.audit()])


def test_advanced_panel_columns_follow_the_section(models):
    toggle = models.panel_advanced.toggle
    toggle.setChecked(False)
    assert all(models.panel_table.isColumnHidden(c) for c in (1, 3, 4))
    assert not models.panel_table.isColumnHidden(2)  # Max. Tokens bleibt sichtbar
    toggle.setChecked(True)
    assert not any(models.panel_table.isColumnHidden(c) for c in (1, 3, 4))


def test_provider_list_shows_names_and_capabilities_in_plain_words(models):
    names = [models.provider_list.item(i).text() for i in range(models.provider_list.count())]
    assert names == ["Ollama (lokal)"]  # ohne internen Schlüssel „(ollama)“
    assert models.caps_label.text().startswith("Festes Antwortformat")


@pytest.fixture
def window(qtbot):
    from llmanno.gui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    yield window
    window.set_project(None)


def test_new_code_system_needs_data_first(window, tmp_path):
    window.set_project(Project.create(tmp_path / "leer", name="Leer"))
    assert not window.add_system_button.isEnabled()
    assert "Erst Daten importieren" in window.add_system_button.toolTip()
    window.set_project(make_project(tmp_path, run=False))
    assert window.add_system_button.isEnabled()


def test_data_tab_shows_the_imported_mapping(window, tmp_path):
    window.set_project(make_project(tmp_path, run=False))
    page = window.data_page
    page.refresh()
    assert page.id_combo.currentData() == "ID" and page.group_combo.currentData() == "Transkript"
    checked = [page.text_list.item(i) for i in range(page.text_list.count())]
    assert [i.text() for i in checked if i.checkState().name == "Checked"] == ["Text"]
    assert "Weiter im Tab Codebuch" in page.status_label.text()
    assert "Erst „Datei wählen“" in page.import_button.toolTip()


def test_empty_data_tab_describes_the_expected_table(window, tmp_path):
    window.set_project(Project.create(tmp_path / "leer", name="Leer"))
    window.data_page.refresh()
    assert "Zeile je Einheit" in window.data_page.status_label.text()


def test_regex_field_only_for_an_own_expression(window, tmp_path):
    window.set_project(make_project(tmp_path, run=False))
    page = window.codebook_page
    page.refresh()
    assert not page.advanced_form.isRowVisible(page.regex_edit)
    page.preset_combo.setCurrentIndex(page.preset_combo.findData("custom"))
    assert page.advanced_form.isRowVisible(page.regex_edit)


def test_start_page_offers_recent_projects(window, tmp_path):
    from llmanno.gui.project_page import RECENT_KEY

    project = make_project(tmp_path, run=False)
    folder = str(project.folder.resolve())
    project.close()
    QSettings().setValue(RECENT_KEY, [folder, str(tmp_path / "gibt-es-nicht")])
    window.set_project(None)
    recent = window.project_page.recent_list
    assert recent.count() == 1
    recent.itemActivated.emit(recent.item(0))
    assert window.project is not None and str(window.project.folder.resolve()) == folder
    QSettings().remove(RECENT_KEY)


def test_new_project_in_a_used_folder_says_what_to_do(tmp_path):
    (tmp_path / "datei.txt").write_text("x")
    with pytest.raises(FileExistsError, match="leeren Ordner"):
        Project.create(tmp_path, name="Studie")
    Project.create(tmp_path / "p", name="P").close()
    with pytest.raises(FileExistsError, match="Projekt öffnen"):
        Project.create(tmp_path / "p", name="P2")
