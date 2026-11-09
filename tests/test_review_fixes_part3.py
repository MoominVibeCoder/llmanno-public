"""Befunde des Reviews zu den Roadmap-Punkten 14, 18, 19, 21, 25, 28."""

import pytest
from helpers import FakeProvider, write_xlsx
from test_evaluation import ROWS, llm_answer, make_project

from llmanno.core.config import UnitSettings
from llmanno.core.engine import fingerprint, run_budget
from llmanno.core.project import Project
from llmanno.core.transfer import transfer_instrument

COLUMNS = ["ID", "Transkript", "Nr", "Text", "A", "B", "C"]


def test_kennwerte_button_exports_again(qtbot, tmp_path, monkeypatch):
    """Befund 1: Ein zweites _export überschrieb das erste; der Knopf warf TypeError."""
    from llmanno.core.evaluation import evaluate
    from llmanno.gui.evaluation_page import EvaluationPage

    project = make_project(tmp_path)
    page = EvaluationPage()
    qtbot.addWidget(page)
    page.set_project(project)
    page._show(evaluate(project, resamples=20), page._stamp())
    target = tmp_path / "k.xlsx"
    monkeypatch.setattr(
        "llmanno.gui.evaluation_page.QFileDialog.getSaveFileName", lambda *a: (str(target), "")
    )
    page.export_button.click()
    assert target.is_file()
    project.close()


def test_transfer_uses_the_saved_state_and_is_locked_during_runs(qtbot, tmp_path, monkeypatch):
    """Befunde 2 und 6."""
    from PySide6.QtWidgets import QMessageBox

    from llmanno.gui.main_window import MainWindow

    source = make_project(tmp_path / "alt")
    folder = source.folder
    source.close()
    data = write_xlsx(tmp_path / "w2.xlsx", COLUMNS[:4], [[f"N{i}", "W", i, "x"] for i in range(4)])
    window = MainWindow()
    qtbot.addWidget(window)
    window.set_project(Project.open(folder))
    coding = window.project.config.coding
    window.project.update(coding=coding.model_copy(update={"max_rationale_words": 77}))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    target = tmp_path / "neu"
    monkeypatch.setattr(
        "llmanno.gui.main_window.QFileDialog.getExistingDirectory", lambda *a: str(target)
    )
    monkeypatch.setattr(
        "llmanno.gui.main_window.QFileDialog.getOpenFileName", lambda *a: (str(data), "")
    )
    window.transfer_action.trigger()
    assert window.project.folder == target
    assert window.project.config.coding.max_rationale_words == coding.max_rationale_words
    window._lock_for_run(True)
    assert not window.transfer_action.isEnabled()
    window._lock_for_run(False)
    window.set_project(None)


def test_target_column_selection_is_part_of_the_instrument(tmp_path):
    """Befund 3: Die Werte der Zielspalte deckte kein Hash; die Übertragung prüfte sie nicht."""
    project = make_project(tmp_path, coders=("A",), run=False)
    units = project.config.coding.model_copy(
        update={"units": UnitSettings(mode="context", target_column="B")}
    )
    project.update(coding=units)
    before = fingerprint(project)
    rows = [list(r) for r in ROWS]
    rows[0][COLUMNS.index("B")] = "nein"  # ändert, welche Zeile kodiert wird
    changed = write_xlsx(tmp_path / "g.xlsx", COLUMNS, rows)
    with pytest.raises(ValueError, match="passen nicht"):
        project.update_gold(changed, "Daten", ["A"], None)
    assert fingerprint(project) == before
    no_target = write_xlsx(tmp_path / "w.xlsx", COLUMNS[:4], [r[:4] for r in ROWS])
    with pytest.raises(ValueError, match="Spalte „B“ nicht"):
        transfer_instrument(project, tmp_path / "neu", no_target)
    project.close()


def test_transfer_of_a_broken_freeze_says_so(tmp_path, monkeypatch):
    """Befund 4."""
    from llmanno.core import engine as engine_module
    from llmanno.core.export.report import report_html
    from llmanno.core.validation import create_split, freeze

    source = make_project(tmp_path / "alt")
    monkeypatch.setattr(engine_module, "make_provider", lambda cfg: FakeProvider(llm_answer))
    create_split(source)
    freeze(source)
    source.update(coding=source.config.coding.model_copy(update={"max_rationale_words": 33}))
    data = write_xlsx(tmp_path / "w2.xlsx", COLUMNS[:4], [[f"N{i}", "W", i, "x"] for i in range(4)])
    project = transfer_instrument(source, tmp_path / "neu", data)
    assert "der Freeze war aber gebrochen" in report_html(project, None)
    project.close()
    source.close()


def test_dev_filter_resets_without_split(qtbot, tmp_path):
    """Befund 5: „nur Dev“ blieb gewählt und gesperrt; die Tabelle war leer."""
    from llmanno.core.validation import create_split
    from llmanno.gui.results_page import ResultsPage

    with_split = make_project(tmp_path / "a")
    create_split(with_split)
    plain = make_project(tmp_path / "b")
    page = ResultsPage()
    qtbot.addWidget(page)
    page.set_project(with_split)
    page.part_combo.setCurrentIndex(page.part_combo.findData("dev"))
    page.set_project(plain)
    assert page.part_combo.currentData() is None and page.table.rowCount() == 60
    with_split.close()
    plain.close()


def test_outlier_ignores_the_shared_system_prompt_and_budget_is_cached(
    qtbot, tmp_path, monkeypatch
):
    """Befunde 7 und 8."""
    from llmanno.core.config import Code, ColumnMapping, ModelConfig

    project = Project.create(tmp_path / "p", name="Lang")
    rows = [[f"U{i}", f"Satz {i}."] for i in range(10)] + [["LANG", "Wort " * 2500]]
    project.import_data(
        write_xlsx(tmp_path / "d.xlsx", ["ID", "Text"], rows), "Daten",
        ColumnMapping(id="ID", text=["Text"]),
    )  # fmt: skip
    codebook = tmp_path / "cb.txt"
    codebook.write_text("### K1 — a\n" + "Definition. " * 1200 + "\n### K2 — b\n", encoding="utf-8")
    project.load_codebook(codebook)
    project.update(
        codebook=project.config.codebook.model_copy(
            update={"codes": [Code(id="K1"), Code(id="K2")]}
        ),
        panel=[ModelConfig(provider="ollama", model="a")],
    )
    budget = run_budget(project)
    assert budget.system > 3000 and budget.outlier and budget.longest[0] == "LANG"

    from llmanno.gui import prompt_page

    calls = []
    real = prompt_page.run_budget
    monkeypatch.setattr(prompt_page, "run_budget", lambda *a, **k: calls.append(1) or real(*a, **k))
    page = prompt_page.PromptPage()
    qtbot.addWidget(page)
    page.set_project(project)
    page.item_combo.setCurrentIndex(1)
    page.run_spin.setValue(1) if page.run_spin.maximum() >= 1 else None
    assert len(calls) == 1
    project.close()


def test_gold_update_keeps_analysis_columns_optional(tmp_path):
    """Befund 9: Eine Gold-Datei ohne gewählte Analyse-Spalte scheiterte."""
    project = make_project(tmp_path, coders=("A",))
    data = project.config.data
    project.update(
        data=data.model_copy(
            update={"mapping": data.mapping.model_copy(update={"analysis": ["C"]})}
        )
    )
    rows = [[r[0], r[1], r[2], r[3], r[4], r[5]] for r in ROWS]  # ohne Spalte C
    path = write_xlsx(tmp_path / "g.xlsx", COLUMNS[:6], rows)
    project.update_gold(path, "Daten", ["A", "B"], None, analysis=["C"])
    assert project.config.data.mapping.analysis == [] and project.config.data.mapping.gold == [
        "A",
        "B",
    ]
    project.close()
