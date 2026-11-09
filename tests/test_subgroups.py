import openpyxl
import pytest
from test_evaluation import WRONG, make_project

from llmanno.core.config import UnitSettings
from llmanno.core.evaluation import NO_VALUE, evaluate, subgroup_options, subgroup_values
from llmanno.core.export.evaluation import export_evaluation
from llmanno.core.project import Project
from llmanno.core.store import Unit
from llmanno.core.units import Item, Line
from llmanno.gui.main_window import MainWindow


@pytest.fixture
def project(tmp_path):
    project = make_project(tmp_path)
    yield project
    project.close()


def test_metrics_per_subgroup(project, tmp_path):
    assert subgroup_options(project.config) == [("group", "Gruppe (Transkript)")]
    report = evaluate(project, resamples=50, subgroup="group")
    vs_a = next(c for c in report.comparisons if c.reference == "A")
    assert [r.value for r in vs_a.subgroups] == ["T0", "T1", "T2"]
    assert sum(r.n for r in vs_a.subgroups) == vs_a.agreement.n
    for r, block in zip(vs_a.subgroups, range(3), strict=True):
        ids = {f"U{i:02d}" for i in range(20 * block, 20 * block + 20)}
        assert r.accuracy == pytest.approx(1 - len(ids & WRONG) / 20)
        assert r.percent == pytest.approx(r.accuracy)
        assert r.kappa.low <= r.kappa.value <= r.kappa.high
        assert sum(s.gold for s in r.shares.values()) == pytest.approx(1)
    assert any("Subgruppen mit weniger als 30" in w for w in report.warnings)

    path = tmp_path / "k.xlsx"
    export_evaluation(report, path)
    rows = list(openpyxl.load_workbook(path)["Subgruppen"].values)
    assert rows[0][:4] == ("Vergleich", "Variable", "Ausprägung", "n")
    assert rows[1][:4] == ("LLM vs. A", "group", "T0", 20)
    assert len(rows) == 1 + 3 * len(report.comparisons)


def test_holistic_mode_offers_no_per_line_variables(project):
    project.update(
        coding=project.config.coding.model_copy(update={"units": UnitSettings(mode="holistic")})
    )
    assert subgroup_options(project.config) == []


def test_subgroup_values_from_meta_and_speaker():
    units = [
        Unit("1", 2, "g", 1, "L", "a", meta={"Klasse": 7}),
        Unit("2", 3, "g", 2, None, "b", meta={"Klasse": " "}),
    ]
    items = [Item(u.unit_id, (Line(u.unit_id, u.speaker, u.text),), meta=u.meta) for u in units]
    assert subgroup_values(units, items, "meta:Klasse") == {"1": "7", "2": NO_VALUE}
    assert subgroup_values(units, items, "speaker") == {"1": "L", "2": NO_VALUE}
    with pytest.raises(ValueError, match="Unbekannte"):
        subgroup_values(units, items, "alter")


def test_evaluation_page_shows_subgroups(qtbot, project):
    window = MainWindow()
    qtbot.addWidget(window)
    window.set_project(Project.open(project.folder))
    page = window.evaluation_page
    page.refresh()
    page.subgroup.setCurrentIndex(page.subgroup.findData("group"))
    page.resamples.setValue(100)
    page.compute()
    qtbot.waitUntil(lambda: page._report is not None, timeout=60000)
    qtbot.waitUntil(lambda: not page._task.running)
    html = page.view.toHtml()
    assert "Subgruppen: Gruppe (Transkript)" in html and "T2" in html
