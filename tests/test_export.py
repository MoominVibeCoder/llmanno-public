import openpyxl
import pytest
from helpers import FakeProvider, ans, engine

from llmanno.core.export.xlsx import export_xlsx


@pytest.fixture
def project(ready_project):
    return ready_project


def test_xlsx_export_has_results_runs_codes_and_info(project, tmp_path):
    engine(project, FakeProvider(lambda r, n: ans("K1", alternatives=["K2"]))).run()
    path = tmp_path / "export.xlsx"
    export_xlsx(project, path)

    workbook = openpyxl.load_workbook(path)
    assert workbook.sheetnames == ["Ergebnisse", "Läufe", "Codes", "Info"]
    rows = list(workbook["Ergebnisse"].values)
    header, first = rows[0], dict(zip(rows[0], rows[1], strict=True))
    assert header[:3] == ("item_id", "ID", "Text")
    assert (first["item_id"], first["llm_status"], first["llm_final"]) == ("1", "ok", "K1")
    assert (first["llm_code_1"], first["llm_p_sc_1"], first["llm_code_2"]) == ("K1", 1, "K2")
    assert first["llm_signalwoerter"] == "K1: Bruch" and first["llm_evidenz_status"] == "exact"
    assert len(rows) == 4

    runs = list(workbook["Läufe"].values)
    assert len(runs) == 1 + 6 and runs[1][4] == "ok"
    codes = [row[0] for row in workbook["Codes"].values]
    assert codes == ["code", "K1", "K2", "KEIN_CODE"]
    info = dict(workbook["Info"].values)
    assert info["Codebuch SHA-256"] == project.config.codebook.sha256
    assert "Plausibilisierungen" in info["Hinweis"]
    assert project.store.audit()[-1].event == "exported"


def test_formula_like_text_stays_text(project, tmp_path):
    project.store._con.execute(
        "UPDATE units SET source = ? WHERE unit_id = '1'", ('{"ID": 1, "Text": "=1+1"}',)
    )
    project.store._con.commit()
    path = tmp_path / "e.xlsx"
    export_xlsx(project, path)
    cell = openpyxl.load_workbook(path)["Ergebnisse"]["C2"]
    assert cell.value == "=1+1" and cell.data_type == "s"


def test_append_row_does_not_rescan_the_sheet(monkeypatch):
    """Roadmap 8: sheet[sheet.max_row] lief je Zeile über alle Zellen (85 s bei 3.000 Einheiten)."""
    from openpyxl.worksheet.worksheet import Worksheet

    from llmanno.core.export.xlsx import append_row

    sheet = openpyxl.Workbook().active
    rescan = property(lambda self: pytest.fail("max_row je Zeile"))
    monkeypatch.setattr(Worksheet, "max_row", rescan)
    append_row(sheet, ["=1+1", "x"])
    append_row(sheet, [])
    append_row(sheet, [2, {"a": 1}])
    monkeypatch.undo()
    assert sheet["A1"].value == "=1+1" and sheet["A1"].data_type == "s"
    assert sheet["A3"].value == 2 and sheet["B3"].value == '{"a": 1}'
    assert sheet.max_row == 3
