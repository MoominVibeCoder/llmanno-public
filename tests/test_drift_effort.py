"""Modellwechsel beim Anbieter, Reasoning-Aufwand, Kontaminationshinweis."""

import dataclasses
import json

import httpx
import pytest
from helpers import FakeProvider, engine
from test_evaluation import llm_answer, make_project
from test_providers import REQUEST, transport

from llmanno.core.config import KI_CONNECT_NRW, ModelConfig
from llmanno.core.engine import _model_dump, served_models
from llmanno.core.evaluation import evaluate
from llmanno.core.export.prereg import preregistration
from llmanno.core.export.report import methods_paragraph, report_html
from llmanno.core.providers.openai_compat import OpenAICompatProvider


@pytest.fixture
def project(tmp_path):
    project = make_project(tmp_path, run=False)
    yield project
    project.close()


class Swapping(FakeProvider):
    """Meldet ab dem zehnten Aufruf ein anderes Modell, wie ein stiller Austausch beim Anbieter."""

    def chat(self, request):
        response = super().chat(request)
        return dataclasses.replace(response, model="neu" if len(self.requests) >= 10 else "alt")


def test_a_changed_served_model_is_reported(project):
    engine(project, Swapping(llm_answer)).run()
    served = served_models(project)["ollama/fake"]
    assert set(served) == {"alt", "neu"}
    report = evaluate(project, resamples=20)
    assert any("verschiedene Modellnamen" in w and "neu (" in w for w in report.warnings)
    html = report_html(project, report)
    assert "vom Anbieter gemeldet" in html and "alt (" in html


def test_a_steady_served_model_gives_no_warning(project):
    engine(project, FakeProvider(llm_answer)).run()
    report = evaluate(project, resamples=20)
    assert not any("verschiedene Modellnamen" in w for w in report.warnings)
    assert "Ludwig et al. 2024" in report_html(project, report)  # Kontaminationshinweis


def test_reasoning_effort_reaches_only_openai_providers(project):
    seen = []
    reply = {"choices": [{"message": {"content": "{}"}}]}
    OpenAICompatProvider(
        "https://x/v1", transport=transport(lambda r: httpx.Response(200, json=reply), seen)
    ).chat(dataclasses.replace(REQUEST, reasoning_effort="low"))
    assert json.loads(seen[0].content)["reasoning_effort"] == "low"
    assert "reasoning_effort" not in _model_dump(ModelConfig(provider="o", model="m"))

    project.update(
        providers=[*project.config.providers, KI_CONNECT_NRW],
        panel=[
            ModelConfig(provider="ollama", model="fake", reasoning_effort="high"),
            ModelConfig(provider="kiconnect", model="fake", reasoning_effort="medium"),
        ],
    )
    provider = FakeProvider(llm_answer)
    engine(project, provider).run(remote_consent=True)
    efforts = {r.reasoning_effort for r in provider.requests}
    assert efforts == {None, "medium"}  # Ollama kennt den Parameter nicht
    calls = project.store.calls(model_key="kiconnect/fake")
    assert calls[0].request["reasoning_effort"] == "medium"
    assert "Reasoning-Aufwand medium" in methods_paragraph(project, None, "de")
    assert "reasoning effort medium" in methods_paragraph(project, None, "en")
    assert "| medium |" in preregistration(project)


def test_effort_box_in_the_panel(qtbot, project):
    from llmanno.core.project import Project
    from llmanno.gui.main_window import MainWindow

    project.update(
        providers=[*project.config.providers, KI_CONNECT_NRW],
        panel=[*project.config.panel, ModelConfig(provider="kiconnect", model="m")],
    )
    project.save()
    window = MainWindow()
    qtbot.addWidget(window)
    window.set_project(Project.open(project.folder))
    page = window.models_page
    page.refresh()
    ollama, remote = page.panel_table.cellWidget(0, 4), page.panel_table.cellWidget(1, 4)
    assert not ollama.isEnabled() and remote.isEnabled()
    remote.setCurrentText("xhigh")
    assert window.project.config.panel[1].reasoning_effort == "xhigh"
    remote.setCurrentIndex(0)
    assert window.project.config.panel[1].reasoning_effort is None
