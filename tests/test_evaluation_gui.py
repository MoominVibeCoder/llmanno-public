import openpyxl
import pytest
from test_evaluation import make_project

from llmanno.core.evaluation import evaluate
from llmanno.core.export.evaluation import export_evaluation, metric_rows
from llmanno.core.project import Project
from llmanno.gui.main_window import MainWindow


@pytest.fixture
def evaluated(tmp_path):
    project = make_project(tmp_path, coders=("A", "B", "C"))
    yield project
    project.close()


def test_metric_rows_and_xlsx_export(evaluated, tmp_path):
    report = evaluate(evaluated, resamples=30)
    rows = metric_rows(report)
    kappa = next(r for r in rows if r[1] == "LLM vs. A" and r[2] == "Cohens Kappa")
    assert kappa[3] == pytest.approx(report.comparisons[0].agreement.kappa.value)
    assert kappa[4] <= kappa[3] <= kappa[5]
    assert any(r[0] == "Alt-Test" and r[2] == "bestanden (ω ≥ 0,5)" for r in rows)

    path = tmp_path / "k.xlsx"
    export_evaluation(report, path)
    workbook = openpyxl.load_workbook(path)
    assert workbook.sheetnames == [
        "Kennwerte",
        "Je Code",
        "Konfusion",
        "Kalibrierung",
        "Alt-Test",
        "Hinweise",
    ]
    assert workbook["Konfusion"]["B2"].value == "K1"


def test_evaluation_page_computes_in_background(qtbot, evaluated, tmp_path):
    window = MainWindow()
    qtbot.addWidget(window)
    window.set_project(Project.open(evaluated.folder))
    page = window.evaluation_page
    page.resamples.setValue(100)
    page.compute()
    qtbot.waitUntil(lambda: page._report is not None, timeout=60000)
    html = page.view.toHtml()
    assert "Cohens" in html and "Alt-Test" in html and "Konfusionsmatrix" in html
    assert page.reference.count() == 4  # A, B, C, Mehrheit
    assert page.export_button.isEnabled()
    page.export_report(tmp_path / "k.xlsx")
    assert (tmp_path / "k.xlsx").is_file()
    page.export_stats_to(tmp_path / "d.sav", "sav")
    qtbot.waitUntil(lambda: not page.busy, timeout=60000)
    assert page.report_button.isEnabled()
    page.export_report_to(tmp_path / "r.html")
    qtbot.waitUntil(lambda: not page.busy, timeout=60000)
    assert "Methodenabsatz" in (tmp_path / "r.html").read_text(encoding="utf-8")
    page.export_report_to(tmp_path / "r.docx")
    qtbot.waitUntil(lambda: not page.busy, timeout=60000)
    assert (tmp_path / "r.docx").stat().st_size > 10_000
    assert (tmp_path / "d.sav").is_file()
    qtbot.waitUntil(lambda: not page._task.running)


def test_stale_evaluation_is_not_exported(qtbot, evaluated, tmp_path):
    """Roadmap 2: Nach Projektwechsel oder neuen Läufen blieb die alte Auswertung exportierbar."""
    from helpers import FakeProvider, engine
    from test_evaluation import llm_answer

    (tmp_path / "b").mkdir()
    other = make_project(tmp_path / "b", coders=("A",), run=False)
    other_folder = other.folder
    other.close()
    window = MainWindow()
    qtbot.addWidget(window)
    window.set_project(Project.open(evaluated.folder))
    page = window.evaluation_page
    page.resamples.setValue(100)
    page.compute()
    qtbot.waitUntil(lambda: page.report is not None, timeout=60000)
    qtbot.waitUntil(lambda: not page._task.running)

    assert window.export_page.current_report() is page.report

    # Neue Aufrufe (hier über eine zweite Verbindung, Konfiguration im Fenster unverändert)
    # machen die Auswertung zur veralteten Anzeige ohne Export.
    side = Project.open(evaluated.folder)
    side.update(panel=[side.config.panel[0].model_copy(update={"temperature": 0.2})])
    engine(side, FakeProvider(llm_answer)).run(item_limit=3)
    side.close()
    page.refresh()
    assert page.report is None and not page.export_button.isEnabled()
    assert "veraltet" in page.status.text()
    assert window.export_page.current_report() is None

    # Projektwechsel verwirft sie ganz, auch eine noch laufende Berechnung.
    page.compute()
    window.set_project(Project.open(other_folder))
    assert page.report is None and page._report is None
    assert page.view.toPlainText() == "" and page.reference.count() == 0
    qtbot.waitUntil(lambda: not page._task.running, timeout=60000)
    qtbot.wait(50)
    assert page._report is None and window.export_page.current_report() is None
    window.set_project(None)
