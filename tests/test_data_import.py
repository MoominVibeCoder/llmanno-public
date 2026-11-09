from datetime import datetime

import pytest
from helpers import write_xlsx

from llmanno.core.config import ColumnMapping
from llmanno.core.data_import import import_units, read_sheet, sheet_names, units_sha256

HEADER = ["ID", "Transkript", "Nr", "Sprecher", "Text", "Frage", "Coder A", "Coder B"]
ROWS = [
    [1, "T1", 1, "L", "Was ist ein Bruch?", "F1", "K1", "K1"],
    [2, "T1", 2, "S", "Ein Teil vom Ganzen.", "F1", "K2", "K3"],
    [3, "T2", 1, "L", "Gut, weiter.", None, "K3", None],
]


@pytest.fixture
def xlsx(tmp_path):
    return write_xlsx(tmp_path / "daten.xlsx", HEADER, ROWS)


def mapping(**overrides):
    fields = dict(
        id="ID", text=["Text"], group="Transkript", order="Nr", speaker="Sprecher",
        meta=["Frage"], gold=["Coder A", "Coder B"],
    )  # fmt: skip
    return ColumnMapping(**{**fields, **overrides})


def test_read_sheet(xlsx):
    assert sheet_names(xlsx) == ["Daten"]
    sheet = read_sheet(xlsx)
    assert sheet.header == HEADER
    assert [number for number, _ in sheet.rows] == [2, 3, 4]
    assert sheet.rows[0][1][0] == 1  # 1.0 aus Excel bleibt die ganze Zahl 1


def test_import_maps_all_fields(xlsx):
    report = import_units(read_sheet(xlsx), mapping())
    assert report.issues == []
    first = report.units[0]
    assert (first.unit_id, first.row, first.group, first.order, first.speaker) == (
        "1",
        2,
        "T1",
        1.0,
        "L",
    )
    assert first.text == "Was ist ein Bruch?"
    assert first.meta == {"Frage": "F1"}
    assert first.gold == {"Coder A": "K1", "Coder B": "K1"}
    assert first.source["Transkript"] == "T1"
    assert report.units[2].meta == {}  # leere Metadaten werden weggelassen


def test_errors_skip_rows_and_warnings_keep_them(tmp_path):
    rows = [
        [1, "T1", 1, "L", "ok", None, None, None],
        [1, "T1", 2, "L", "doppelt", None, None, None],
        [None, "T1", 3, "L", "ohne ID", None, None, None],
        [4, "T1", "x", "L", "Reihenfolge kaputt", None, None, None],
        [5, "T1", 5, "L", "  ", None, None, None],
    ]
    path = write_xlsx(tmp_path / "d.xlsx", HEADER, rows)
    report = import_units(read_sheet(path), mapping())
    assert [u.unit_id for u in report.units] == ["1", "4"]
    assert report.units[1].order == 5.0  # Zeilennummer als Ersatz
    assert [(i.severity, i.row) for i in report.issues] == [
        ("error", 3), ("error", 4), ("warning", 5), ("error", 6)
    ]  # fmt: skip
    assert report.n_skipped == 3


def test_without_id_column_uses_row_numbers(xlsx):
    report = import_units(read_sheet(xlsx), mapping(id=None))
    assert [u.unit_id for u in report.units] == ["Z2", "Z3", "Z4"]
    assert report.issues[0].severity == "warning"


def test_multiple_text_columns_are_labelled(xlsx):
    report = import_units(read_sheet(xlsx), mapping(text=["Frage", "Text"]))
    assert report.units[0].text == "Frage: F1\nText: Was ist ein Bruch?"
    assert report.units[2].text == "Text: Gut, weiter."


def test_unknown_column_is_rejected(xlsx):
    with pytest.raises(ValueError, match="Fehlt"):
        import_units(read_sheet(xlsx), mapping(group="Fehlt"))


def test_dates_become_iso_strings(tmp_path):
    path = write_xlsx(
        tmp_path / "d.xlsx", ["ID", "Text", "Datum"], [[1, "t", datetime(2026, 1, 2)]]
    )
    report = import_units(read_sheet(path), ColumnMapping(id="ID", text=["Text"]))
    assert report.units[0].source["Datum"] == "2026-01-02T00:00:00"


def test_units_hash_is_stable_and_sensitive(xlsx):
    units = import_units(read_sheet(xlsx), mapping()).units
    assert units_sha256(units) == units_sha256(list(units))
    other = import_units(read_sheet(xlsx), mapping(meta=[])).units
    assert units_sha256(units) != units_sha256(other)
