"""Roadmap 42: CSV-Import und mehrere Dateien oder Blätter zusammenführen."""

import csv

import openpyxl
import pytest
from helpers import FakeProvider, engine, write_xlsx
from PySide6.QtCore import Qt
from test_evaluation import CODES, ROWS, llm_answer

from llmanno.core.config import Code, ColumnMapping, ModelConfig, UnitSettings
from llmanno.core.data_import import (
    SOURCE_COLUMN,
    SOURCE_ID_COLUMN,
    import_units,
    merge_sources,
    read_sheet,
    sheet_names,
)
from llmanno.core.export.replication import export_replication
from llmanno.core.project import Project
from llmanno.rerun import rerun

HEADER = ["ID", "Transkript", "Nr", "Text", "A", "B", "C"]


def _csv(path, rows, delimiter=";", encoding="utf-8-sig"):
    with path.open("w", encoding=encoding, newline="") as handle:
        csv.writer(handle, delimiter=delimiter).writerows(rows)
    return path


def test_csv_like_excel_would_read_it(tmp_path):
    path = _csv(
        tmp_path / "d.csv",
        [["ID", "Text", "Nr", "Nr"], ["007", "Größe\nzweite Zeile", "17", "1.5"], ["", "", "", ""]],
    )
    assert sheet_names(path) == ["CSV"]
    sheet = read_sheet(path, "CSV")
    assert sheet.header == ["ID", "Text", "Nr", "Nr (2)"]
    assert sheet.rows == [(2, ["007", "Größe\nzweite Zeile", 17, 1.5])]  # leere Zeile entfällt

    windows = _csv(tmp_path / "w.csv", [["ID", "Text"], ["1", "Übung"]], ",", "cp1252")
    assert read_sheet(windows).rows == [(2, [1, "Übung"])]
    tabs = _csv(tmp_path / "t.tsv", [["ID", "Text"], ["1", "a, b; c"]], "\t", "utf-8")
    assert read_sheet(tabs).rows == [(2, [1, "a, b; c"])]


def _project(tmp_path, data, sheet, mapping):
    project = Project.create(tmp_path / "p", name="CSV")
    report = project.import_data(data, sheet, mapping)
    codebook = tmp_path / "cb.txt"
    codebook.write_text("### K1 — a\n### K2 — b\n### K3 — c\n", encoding="utf-8")
    project.load_codebook(codebook)
    project.update(
        codebook=project.config.codebook.model_copy(update={"codes": [Code(id=c) for c in CODES]}),
        coding=project.config.coding.model_copy(update={"units": UnitSettings(mode="context")}),
        panel=[ModelConfig(provider="ollama", model="fake")],
    )
    project.save()
    return project, report


def test_csv_project_codes_and_reruns(tmp_path):
    data = _csv(tmp_path / "d.csv", [HEADER, *ROWS])
    mapping = ColumnMapping(id="ID", text=["Text"], group="Transkript", order="Nr", gold=["A"])
    project, report = _project(tmp_path, data, "CSV", mapping)
    assert not [i for i in report.issues if "keine Zahl" in i.message]  # Nr als Zahl gelesen
    assert project.data_path.name == "data.csv" and project.data_path.is_file()
    engine(project, FakeProvider(llm_answer)).run()
    package = tmp_path / "paket.zip"
    export_replication(project, package, include_texts=True)
    project.close()
    result = rerun(package, tmp_path / "r", provider_factory=lambda cfg: FakeProvider(llm_answer))
    assert result.items_identical == result.items_compared == len(ROWS)


def test_merge_files_and_sheets(tmp_path):
    first = write_xlsx(tmp_path / "stunde1.xlsx", ["Nr", "Text"], [[1, "a"], [2, "b"]])
    workbook = openpyxl.load_workbook(first)
    extra = workbook.create_sheet("Teil 2")
    extra.append(["Nr", "Text", "Sprecher"])
    extra.append([1, "c", "L"])
    workbook.save(first)
    second = _csv(tmp_path / "stunde2.csv", [["Nr", "Text"], ["1", "d"]])
    out = tmp_path / "alle.xlsx"
    notes = merge_sources([(first, "Daten"), (first, "Teil 2"), (second, "CSV")], out)
    assert notes == [
        "„stunde1 › Daten“ hat die Spalten Sprecher nicht; sie bleiben leer.",
        "„stunde2“ hat die Spalten Sprecher nicht; sie bleiben leer.",
    ]
    sheet = read_sheet(out)
    assert sheet.header == [SOURCE_COLUMN, SOURCE_ID_COLUMN, "Nr", "Text", "Sprecher"]
    sources = [r[0] for _, r in sheet.rows]
    assert sources == ["stunde1 › Daten", "stunde1 › Daten", "stunde1 › Teil 2", "stunde2"]
    assert [r[1] for _, r in sheet.rows] == [
        "stunde1 › Daten:2", "stunde1 › Daten:3", "stunde1 › Teil 2:2", "stunde2:2"
    ]  # fmt: skip
    mapping = ColumnMapping(id=SOURCE_ID_COLUMN, text=["Text"], group=SOURCE_COLUMN, order="Nr")
    report = import_units(sheet, mapping)
    assert len(report.units) == 4 and not [i for i in report.issues if i.severity == "error"]
    assert {u.group for u in report.units} == set(sources)


def test_merge_refuses_too_few_sources_or_taken_columns(tmp_path):
    one = write_xlsx(tmp_path / "a.xlsx", ["Quelle", "Text"], [["x", "a"]])
    with pytest.raises(ValueError, match="mindestens zwei"):
        merge_sources([(one, "Daten")], tmp_path / "o.xlsx")
    two = write_xlsx(tmp_path / "b.xlsx", ["Text"], [["b"]])
    with pytest.raises(ValueError, match="„Quelle“ gibt es schon"):
        merge_sources([(one, "Daten"), (two, "Daten")], tmp_path / "o.xlsx")


def test_data_page_merges_and_preselects(qtbot, tmp_path):
    from llmanno.gui.data_page import DataPage, SourceDialog

    a = write_xlsx(tmp_path / "a.xlsx", ["Nr", "Text"], [[1, "a"]])
    b = write_xlsx(tmp_path / "b.xlsx", ["Nr", "Text"], [[1, "b"]])
    dialog = SourceDialog([(a, "Daten"), (b, "Daten")])
    qtbot.addWidget(dialog)
    dialog.list.item(1).setCheckState(Qt.CheckState.Unchecked)
    assert dialog.chosen() == [(a, "Daten")]

    project = Project.create(tmp_path / "p", name="M")
    page = DataPage()
    qtbot.addWidget(page)
    page.set_project(project)
    page.merge_to([(a, "Daten"), (b, "Daten")], tmp_path / "m.xlsx")
    assert not page.error.text()
    assert page.group_combo.currentData() == SOURCE_COLUMN
    assert page.id_combo.currentData() == SOURCE_ID_COLUMN
    assert "2 Quellen" in page.report.item(0).text()
    page._import()
    assert project.config.data.n_units == 2 and project.config.data.mapping.group == SOURCE_COLUMN
    project.close()
