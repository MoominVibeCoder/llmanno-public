"""Roadmap 41: Hilfe/Über mit Version, Protokoll-Ansicht, zuletzt geöffnete Projekte,
API-Key entfernen."""

from importlib.metadata import version

import keyring
import keyring.errors
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox
from test_evaluation import make_project

from llmanno.core import providers
from llmanno.core.config import GWDG_CHAT_AI
from llmanno.core.project import Project
from llmanno.gui.audit_dialog import AuditDialog
from llmanno.gui.main_window import RECENT_KEY, MainWindow, recent_projects


@pytest.fixture
def fake_keyring(monkeypatch):
    """Nie den echten Schlüsselbund anfassen."""
    vault: dict[tuple[str, str], str] = {}

    def delete(service, user):
        if (service, user) not in vault:
            raise keyring.errors.PasswordDeleteError("fehlt")
        del vault[service, user]

    monkeypatch.setattr(keyring, "get_password", lambda s, u: vault.get((s, u)))
    monkeypatch.setattr(keyring, "set_password", lambda s, u, p: vault.__setitem__((s, u), p))
    monkeypatch.setattr(keyring, "delete_password", delete)
    return vault


def test_delete_api_key(fake_keyring):
    providers.set_api_key(GWDG_CHAT_AI, "test-wert")
    assert providers.get_api_key(GWDG_CHAT_AI)
    assert providers.delete_api_key(GWDG_CHAT_AI) is True
    assert providers.get_api_key(GWDG_CHAT_AI) is None
    assert providers.delete_api_key(GWDG_CHAT_AI) is False


def test_models_page_removes_a_key(qtbot, tmp_path, fake_keyring, monkeypatch):
    from llmanno.gui.models_page import ModelsPage

    project = make_project(tmp_path, run=False)
    project.update(providers=[*project.config.providers, GWDG_CHAT_AI])
    page = ModelsPage()
    qtbot.addWidget(page)
    page.set_project(project)
    page.provider_list.setCurrentRow(len(project.config.providers) - 1)
    assert not page.remove_key_button.isEnabled()  # noch kein Key
    providers.set_api_key(GWDG_CHAT_AI, "test-wert")
    page.provider_list.setCurrentRow(0)
    page.provider_list.setCurrentRow(len(project.config.providers) - 1)
    assert page.remove_key_button.isEnabled()

    asked = []
    answer = QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, "question", lambda *a: asked.append(a) or answer)
    page._remove_key()
    assert providers.get_api_key(GWDG_CHAT_AI) and GWDG_CHAT_AI.base_url in asked[0][2]
    answer = QMessageBox.StandardButton.Yes
    page._remove_key()
    assert providers.get_api_key(GWDG_CHAT_AI) is None
    assert not page.remove_key_button.isEnabled()
    entry = project.store.audit()[-1]
    assert entry.event == "api_key_removed" and "test-wert" not in str(entry.details)
    project.close()


def test_about_names_the_version(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    text = window.about_text()
    assert f"llmanno {version('llmanno')}" in text and "Prompt-Template" in text


def test_recent_projects_are_remembered(qtbot, tmp_path):
    QSettings().remove(RECENT_KEY)
    folders = []
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        project = make_project(tmp_path / name, run=False)
        folders.append(str(project.folder.resolve()))
        project.close()
    window = MainWindow()
    qtbot.addWidget(window)
    window.set_project(Project.open(tmp_path / "a" / "p"))
    window.set_project(Project.open(tmp_path / "b" / "p"))
    window.set_project(Project.open(tmp_path / "a" / "p"))
    assert recent_projects() == [folders[0], folders[1]]  # ohne Doppel, neueste zuerst

    window._fill_recent()
    actions = window.recent_menu.actions()
    assert [a.text().split("  ")[0] for a in actions] == ["p", "p"]
    window.set_project(None)
    actions[1].trigger()  # öffnet b
    assert window.project is not None and window.project.folder.resolve() == tmp_path / "b" / "p"
    window.set_project(None)


def test_audit_dialog_lists_and_filters(qtbot, tmp_path):
    project = make_project(tmp_path)
    project.store.log("exported", format="praeregistrierung", file="Präregistrierung.docx")
    dialog = AuditDialog(project)
    qtbot.addWidget(dialog)
    table = dialog.table
    assert table.rowCount() == len(project.store.audit())
    assert table.item(0, 1).text() == "Export"  # neueste zuerst, in Klartext
    assert table.item(0, 1).toolTip() == "exported"
    dialog.filter.setText("präregistrierung")
    visible = [r for r in range(table.rowCount()) if not table.isRowHidden(r)]
    assert visible == [0]
    dialog.filter.setText("")
    assert not any(table.isRowHidden(r) for r in range(table.rowCount()))
    project.close()
