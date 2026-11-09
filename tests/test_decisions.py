"""Entscheidungen des Users vom 2026-10-07: AGPL, Evidenz-Repair bei kleinen Modellen aus."""

import tomllib
from pathlib import Path

import pytest
from test_evaluation import make_project

from llmanno.core import providers
from llmanno.core.providers.base import ModelInfo

ROOT = Path(__file__).resolve().parents[1]


def test_license_is_agpl():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert meta["license"] == "AGPL-3.0-or-later"
    assert (
        (ROOT / "LICENSE")
        .read_text(encoding="utf-8")
        .lstrip()
        .startswith("GNU AFFERO GENERAL PUBLIC LICENSE")
    )
    for name in ("LGPL-3.0.txt", "GPL-3.0.txt", "HINWEISE.txt"):
        assert (ROOT / "packaging" / "licenses" / name).is_file()


@pytest.mark.parametrize(
    ("size", "billions"),
    [("3.8B", 3.8), ("27B", 27.0), ("567.81M", 0.56781), ("", None), (None, None)],
)
def test_parameter_billions(size, billions):
    info = ModelInfo("m", details={"parameter_size": size} if size is not None else {})
    assert providers.parameter_billions(info) == pytest.approx(billions)


def _page(qtbot, tmp_path, size):
    from llmanno.gui.models_page import ModelsPage

    project = make_project(tmp_path, run=False)
    project.update(panel=[])
    page = ModelsPage()
    qtbot.addWidget(page)
    page.set_project(project)
    page.provider_list.setCurrentRow(0)
    provider = page._selected()
    assert provider is not None and provider.kind == "ollama"
    info = ModelInfo("klein", capabilities=("completion",), details={"parameter_size": size})
    page._models[provider.key] = [info]
    page.model_combo.setEditText("klein")
    page._add_to_panel()
    return project, page


def test_small_model_switches_evidence_repair_off(qtbot, tmp_path):
    project, page = _page(qtbot, tmp_path, "3.8B")
    assert [m.model for m in project.config.panel] == ["klein"]
    assert project.config.coding.evidence_repair is False
    assert "kleines Modell" in page.notes.text()
    project.close()


def test_large_model_keeps_evidence_repair(qtbot, tmp_path):
    project, page = _page(qtbot, tmp_path, "27B")
    assert project.config.coding.evidence_repair is True
    project.close()


def test_switching_repair_on_shows_a_hint_for_local_models(qtbot, tmp_path):
    from llmanno.gui.prompt_page import PromptPage

    project = make_project(tmp_path, run=False)  # Panel: ollama/fake
    page = PromptPage()
    qtbot.addWidget(page)
    page.set_project(project)
    page.evidence_repair.setChecked(False)
    assert page.repair_hint.text() == ""
    page.evidence_repair.setChecked(True)
    assert "lokales Modell" in page.repair_hint.text()
    project.close()


def test_about_names_the_license(qtbot):
    from llmanno.gui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    assert "AGPL" in window.about_text()
