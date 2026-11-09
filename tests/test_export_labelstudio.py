"""Export für Label Studio (Backlog v2)."""

import json
from xml.etree import ElementTree as ET

from test_evaluation import CODES, ROWS, make_project

from llmanno.core.export.labelstudio import export_labelstudio
from llmanno.core.validation import create_split


def test_tasks_with_predictions_and_matching_config(tmp_path):
    project = make_project(tmp_path)
    summary = export_labelstudio(project, tmp_path / "ls.json")
    tasks = json.loads((tmp_path / "ls.json").read_text(encoding="utf-8"))
    assert summary.tasks == len(tasks) == len(ROWS) and summary.predictions == len(ROWS)
    task = tasks[1]  # U01: das Test-LLM kodiert richtig (K2)
    assert task["data"]["unit"] == "U01" and task["data"]["text"] == ROWS[1][3]
    assert task["data"]["before"].startswith("[U00] ")  # Kontextmodus
    assert task["data"]["info"] == "[U01]"  # ohne Sprecher-Spalte nur die ID
    result = task["predictions"][0]["result"]
    assert result[0]["value"]["choices"] == ["K2"]
    span = next(r for r in result if r["type"] == "labels")
    text = task["data"]["text"]
    assert text[span["value"]["start"] : span["value"]["end"]] == span["value"]["text"] == "Wort1"
    view = ET.parse(summary.config).getroot()
    names = {e.get("name") for e in view.iter() if e.get("name")}
    assert names >= {"text", "code", "evidence", "before", "after"}
    assert [c.get("value") for c in view.find("Choices")][: len(CODES)] == CODES
    assert project.store.audit()[-1].details["format"] == "labelstudio"
    project.close()


def test_same_span_in_several_spellings_once_and_speaker_in_info(tmp_path):
    """Review: „Bruch“, Bruch. und Bruch landen an derselben Stelle."""
    from test_export_refi import _small_project

    candidate = {"evidence": ["Bruch", "Bruch.", "„Bruch“"], "code": "K1", "confidence": 90}
    answer = json.dumps({"rationale": "r", "candidates": [candidate]})
    rows = [["1", "G", 1, "L", "Ein Bruch, oder?"], ["2", "G", 2, "S", "Ja"]]
    project = _small_project(tmp_path, rows, "isolated", answer)
    export_labelstudio(project, tmp_path / "s.json")
    task = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))[0]
    spans = [r for r in task["predictions"][0]["result"] if r["type"] == "labels"]
    assert len(spans) == 1 and spans[0]["value"]["text"] == "Bruch"
    assert task["data"]["info"] == "[1] L"
    project.close()


def test_without_predictions_or_hidden_test_units(tmp_path):
    project = make_project(tmp_path)
    blind = export_labelstudio(project, tmp_path / "b.json", predictions=False)
    assert blind.predictions == 0
    tasks = json.loads((tmp_path / "b.json").read_text(encoding="utf-8"))
    assert all("predictions" not in t for t in tasks)
    parts = create_split(project)
    dev = export_labelstudio(project, tmp_path / "d.json", only_dev=True)
    assert dev.tasks == len(ROWS)  # Texte bleiben
    assert dev.predictions == sum(p == "dev" for p in parts.values())
    project.close()


def test_export_page_writes_label_studio(qtbot, tmp_path):
    from llmanno.core.project import Project
    from llmanno.gui.main_window import MainWindow

    project = make_project(tmp_path)
    folder = project.folder
    project.close()
    window = MainWindow()
    qtbot.addWidget(window)
    window.set_project(Project.open(folder))
    page = window.export_page
    page.refresh()
    assert page.ls_button.isEnabled()
    page.export_labelstudio_to(tmp_path / "g.json")
    qtbot.waitUntil(lambda: not page.busy, timeout=60000)
    assert not page.error.text() and (tmp_path / "g.xml").is_file()
    window.set_project(None)
