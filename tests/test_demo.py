"""„Demo öffnen“: Beispielprojekt mit simulierten Antworten, ohne eigene Daten und ohne Modell."""

from pathlib import Path

import pytest

from llmanno.core.demo import DEMO_DIR, DEMO_PROVIDER, MODELS, create_demo
from llmanno.core.engine import prepare_prompts, results
from llmanno.core.evaluation import evaluate
from llmanno.core.export.report import report_html


def test_demo_data_ships_with_the_package():
    assert (DEMO_DIR / "daten.xlsx").is_file() and (DEMO_DIR / "codebuch.md").is_file()


def test_create_demo_codes_everything_and_says_it_is_simulated(tmp_path):
    project = create_demo(tmp_path / "demo")
    calls = project.store.calls()
    assert {c.status for c in calls} == {"ok"}
    assert {c.model_key for c in calls} == {f"{DEMO_PROVIDER.key}/{m}" for m in MODELS}
    _, items = prepare_prompts(project)
    by_item = results(project, [i.item_id for i in items])
    assert len(by_item) == 200 and all(r.status == "ok" for r in by_item.values())
    report = evaluate(project, resamples=50)
    kappa = report.comparisons[0].agreement.kappa.value
    assert 0.5 < kappa < 1  # Fehler sind eingestreut, die Demo ist nicht perfekt
    html = report_html(project, report)
    assert "Demo-Simulator (kein LLM)" in html and "simulierte Antworten" in project.config.name
    assert project.store.audit()[-1].event == "demo_created"
    project.close()


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    from llmanno.gui.main_window import MainWindow

    monkeypatch.setattr(MainWindow, "demo_folder", lambda self: tmp_path / "Demo")
    window = MainWindow()
    qtbot.addWidget(window)
    yield window
    window.set_project(None)


def test_open_demo_creates_then_reopens(qtbot, window, tmp_path, monkeypatch):
    window.open_demo()
    # Erst wenn auch die Aufgabe beendet ist: Solange sie läuft, ignoriert open_demo den Aufruf.
    qtbot.waitUntil(lambda: window.project is not None and not window.demo_busy, timeout=60000)
    assert window.project.folder == tmp_path / "Demo"
    assert window.tabs.currentWidget() is window.results_page

    answers = iter(["open", "new"])
    monkeypatch.setattr(type(window), "_ask_demo", lambda self: next(answers))
    window.open_demo()  # Öffnen: die vorhandene
    assert window.project.folder == tmp_path / "Demo"
    window.open_demo()  # Neu anlegen: neue Demo daneben, nichts gelöscht
    qtbot.waitUntil(
        lambda: window.project is not None and window.project.folder.name.startswith("Demo_"),
        timeout=60000,
    )
    assert (tmp_path / "Demo" / "project.json").is_file()
    assert Path(window.project.folder).parent == tmp_path
