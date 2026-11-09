"""Datenschutz-Stufe gegen die Adresse prüfen (Roadmap 32)."""

import pytest
from helpers import FakeProvider, engine
from test_evaluation import llm_answer, make_project

from llmanno.core.config import GWDG_CHAT_AI, OLLAMA_LOCAL
from llmanno.core.export.report import report_html


@pytest.mark.parametrize(
    "url, local",
    [
        ("http://localhost:11434", True),
        ("http://127.0.0.1:11434", True),
        ("http://[::1]:11434", True),
        ("localhost:11434", True),
        ("http://ollama.localhost", True),
        ("http://192.168.1.20:11434", False),
        ("http://gpu-server.uni-x.de:11434", False),
        ("http://localhost.example.org", False),
    ],
)
def test_local_only_on_this_machine(url, local):
    provider = OLLAMA_LOCAL.model_copy(update={"base_url": url})
    assert provider.on_this_machine is local
    assert provider.privacy_level == ("local" if local else "external")


def test_key_in_clear_and_declared_levels_stay():
    academic = GWDG_CHAT_AI.model_copy(update={"base_url": "http://chat.example.org/v1"})
    assert academic.key_in_clear and academic.privacy_level == "academic"
    assert not GWDG_CHAT_AI.key_in_clear
    assert not OLLAMA_LOCAL.model_copy(update={"auth": "bearer"}).key_in_clear  # localhost


def test_remote_ollama_needs_consent_and_is_reported_as_external(tmp_path):
    """Ein Ollama-Eintrag mit fremder Adresse lief ohne Zustimmung und hieß im Report „lokal“."""
    project = make_project(tmp_path, run=False)
    remote = OLLAMA_LOCAL.model_copy(update={"base_url": "http://gpu-server.uni-x.de:11434"})
    project.update(providers=[remote])
    with pytest.raises(PermissionError, match="externe Anbieter"):
        engine(project, FakeProvider(llm_answer)).run()
    engine(project, FakeProvider(llm_answer)).run(remote_consent=True, item_limit=3)
    consent = next(e for e in project.store.audit() if e.event == "remote_consent")
    assert consent.details["providers"][0]["privacy"] == "external"
    assert "externe API" in report_html(project, None)
    project.close()


def test_models_tab_warns(qtbot, tmp_path):
    from llmanno.gui.models_page import ModelsPage

    project = make_project(tmp_path, run=False)
    page = ModelsPage()
    qtbot.addWidget(page)
    page.set_project(project)
    page.provider_list.setCurrentRow(0)
    assert page.privacy_warning.text() == ""
    page.url_edit.setText("http://gpu-server.uni-x.de:11434")
    page.url_edit.editingFinished.emit()
    assert "nicht dieser Rechner" in page.privacy_warning.text()
    page.auth_combo.setCurrentIndex(page.auth_combo.findData("bearer"))
    assert "unverschlüsselt" in page.privacy_warning.text()
    project.close()
