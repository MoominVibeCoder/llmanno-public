"""Abweichungsansicht im Ergebnis-Tab (Roadmap 21)."""

from test_evaluation import TRUTH, WRONG, make_project

from llmanno.core.validation import create_split
from llmanno.gui.results_page import ResultsPage


def rows(page):
    return {page.table.item(r, 0).text(): page.table.item(r, 3).text()
            for r in range(page.table.rowCount())}  # fmt: skip


def test_reference_column_filters_and_hidden_test_gold(qtbot, tmp_path):
    project = make_project(tmp_path, coders=("A",))
    page = ResultsPage()
    qtbot.addWidget(page)
    page.set_project(project)
    assert page.table.horizontalHeaderItem(3).text() == "Referenz"
    assert rows(page)["U01"] == TRUTH["U01"]  # einzige Gold-Spalte A
    page.flag_combo.setCurrentIndex(page.flag_combo.findData("view:deviates"))
    assert set(rows(page)) == WRONG  # das LLM irrt genau dort

    parts = create_split(project)
    page.refresh()
    test = {i for i, p in parts.items() if p == "test"}
    shown = rows(page)
    assert set(shown) == WRONG - test  # Test zählt vor dem Freeze nie als Abweichung
    page.flag_combo.setCurrentIndex(0)
    shown = rows(page)
    assert all(shown[i] == "verborgen (Test)" for i in test)
    assert page.part_combo.findData("test") == -1  # „nur Test“ erst nach dem Freeze
    page.part_combo.setCurrentIndex(page.part_combo.findData("dev"))
    assert set(rows(page)) == {i for i, p in parts.items() if p == "dev"}

    page.part_combo.setCurrentIndex(0)
    page.flag_combo.setCurrentIndex(page.flag_combo.findData("view:unsure"))
    assert rows(page) == {}  # die Testantworten sind einstimmig
    project.close()
