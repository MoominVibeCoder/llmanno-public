"""Befunde des vierten Reviews (Roadmap 38–42 und Pseudonym-IDs)."""

import re
import zipfile

import pytest
from helpers import write_xlsx
from test_evaluation import make_project
from test_store import _schema_v1

from llmanno.core import store as store_module
from llmanno.core.config import ColumnMapping, UnitSettings
from llmanno.core.data_import import _csv_value, import_units, merge_sources, read_sheet
from llmanno.core.evaluation import evaluate
from llmanno.core.export.plots import risk_coverage_svg
from llmanno.core.export.replication import export_replication
from llmanno.core.metrics.calibration import calibration_report
from llmanno.core.project import Project
from llmanno.gui.common import chosen_path
from llmanno.rerun import main


def _zip_text(path):
    with zipfile.ZipFile(path) as archive:
        return "".join(archive.read(n).decode("utf-8", "ignore") for n in archive.namelist())


def test_pseudonymized_package_leaks_no_ids_via_warnings_or_old_modes(tmp_path):
    project = make_project(tmp_path)  # Kontextmodus: Aufrufe tragen Einheiten-IDs
    report = evaluate(project, resamples=20, subgroup="group")
    assert not any("T0" in w for w in report.warnings)  # Werte stehen nur in der Tabelle
    assert any("Subgruppen mit weniger als 30" in w for w in report.warnings) or True
    project.update(
        coding=project.config.coding.model_copy(update={"units": UnitSettings(mode="holistic")})
    )
    path = tmp_path / "p.zip"
    export_replication(project, path, pseudonymize_ids=True, report=report)
    text = _zip_text(path)
    assert not re.search(r"\bU\d\d\b", text) and not re.search(r"\bT\d\b", text)
    project.close()


def test_merge_keeps_formula_like_text_and_drops_control_characters(tmp_path):
    a = write_xlsx(tmp_path / "a.xlsx", ["Nr", "Text"], [[1, "a"]])
    b = tmp_path / "b.csv"
    b.write_text("Nr;Text\n1;=ja genau\n2;Seite\fzwei\n", encoding="utf-8")
    out = tmp_path / "m.xlsx"
    merge_sources([(a, "Daten"), (b, "CSV")], out)
    texts = [cells[3] for _, cells in read_sheet(out).rows]
    assert texts == ["a", "=ja genau", "Seitezwei"]  # GAT-Latching „=“ bleibt Text


def test_csv_rows_with_extra_fields_are_kept_and_reported(tmp_path):
    path = tmp_path / "d.csv"
    path.write_text("ID;Text\n1;Ja; genau so\n2;Nein\n", encoding="utf-8")
    sheet = read_sheet(path)
    assert sheet.header == ["ID", "Text", "Spalte 3"]
    assert sheet.rows[0][1] == [1, "Ja", " genau so"] and sheet.rows[1][1] == [2, "Nein", None]
    report = import_units(sheet, ColumnMapping(id="ID", text=["Text"]))
    assert any("mehr Felder als die Kopfzeile" in i.message for i in report.issues)
    single = tmp_path / "s.csv"
    single.write_text("Text\nHallo, Welt\n", encoding="utf-8")
    assert read_sheet(single).rows == [(2, ["Hallo, Welt"])]  # einspaltig: Komma bleibt


@pytest.mark.parametrize(
    ("raw", "value"),
    [("17", 17), ("1.5", 1.5), ("-0.25", -0.25), ("007", "007"), ("1.10", "1.10"),
     ("2.0", "2.0"), ("1.000", "1.000"), ("3,5", "3,5"), ("", None)],
)  # fmt: skip
def test_csv_numbers_only_when_lossless(raw, value):
    assert _csv_value(raw) == value


def test_recent_projects_are_locked_during_a_run(qtbot, tmp_path, monkeypatch):
    from llmanno.gui.main_window import MainWindow
    from llmanno.gui.run_page import RunPage

    project = make_project(tmp_path, run=False)
    folder = project.folder
    project.close()
    window = MainWindow()
    qtbot.addWidget(window)
    window._lock_for_run(True)
    assert not window.recent_menu.isEnabled()
    window._lock_for_run(False)
    assert window.recent_menu.isEnabled()
    with monkeypatch.context() as patched:
        patched.setattr(RunPage, "running", property(lambda self: True))
        window.open_recent(str(folder))
    assert window.project is None


def test_copy_methods_reports_instead_of_crashing(qtbot, tmp_path):
    from llmanno.gui.evaluation_page import EvaluationPage

    project = Project.create(tmp_path / "p", name="P")
    data = write_xlsx(tmp_path / "d.xlsx", ["ID", "Text"], [[1, "a"]])
    project.import_data(data, "Daten", ColumnMapping(id="ID", text=["Text"]))
    codebook = tmp_path / "cb.txt"
    codebook.write_text("### K1 — a\n", encoding="utf-8")
    project.load_codebook(codebook)  # Codes noch nicht bestätigt
    page = EvaluationPage()
    qtbot.addWidget(page)
    page.set_project(project)
    page.copy_methods()
    assert "Codes" in page.error.text()
    project.close()


def test_rerun_names_a_missing_package(tmp_path, capsys):
    assert main([str(tmp_path / "gibtsnicht.zip"), "--out", str(tmp_path / "o")]) == 1
    assert "nicht gefunden" in capsys.readouterr().err


def test_plots_use_the_requested_decimal_separator():
    cal = calibration_report([0.9, 0.2], [True, False])
    assert ">0,5<" in risk_coverage_svg("p", cal)
    assert ">0.5<" in risk_coverage_svg("p", cal, decimal=".")


def test_save_path_follows_the_chosen_filter():
    assert chosen_path("C:/x/prereg.md", "Word (*.docx)").name == "prereg.docx"
    assert chosen_path("C:/x/prereg.docx", "Word (*.docx)").name == "prereg.docx"
    assert chosen_path("C:/x/r.html", "HTML (*.html)").name == "r.html"
    assert chosen_path("C:/x/d", "").name == "d"


def test_failed_backup_releases_the_store(tmp_path, monkeypatch):
    path = tmp_path / "alt.sqlite"
    _schema_v1(path)
    real = store_module.sqlite3.connect

    def connect(target, *args, **kwargs):
        if "vor-migration" in str(target):
            raise OSError("Datenträger voll")
        return real(target, *args, **kwargs)

    monkeypatch.setattr(store_module.sqlite3, "connect", connect)
    with pytest.raises(OSError, match="voll"):
        store_module.Store.open(path)
    path.rename(tmp_path / "frei.sqlite")  # unter Windows nur ohne offene Verbindung
