"""Code-Gruppen (Oberkategorien) und Auswertung auf beiden Ebenen (Roadmap 10)."""

import zipfile

import openpyxl
import pytest
from helpers import FakeProvider, engine
from test_evaluation import CODES, llm_answer, make_project

from llmanno.core.config import Code
from llmanno.core.engine import Engine, fingerprint
from llmanno.core.evaluation import evaluate
from llmanno.core.export.evaluation import export_evaluation
from llmanno.core.export.refi import export_qdpx
from llmanno.core.export.report import methods_paragraph, report_html

# Das LLM irrt bei jeder sechsten Einheit um einen Code weiter: K1 → K2 bleibt in der Gruppe
# „nah“, K2 → K3 und K3 → K1 wechseln sie.
GROUPS = {"K1": "nah", "K2": "nah", "K3": "fern"}


def grouped(project):
    codes = [Code(id=c, parent=GROUPS[c]) for c in CODES]
    project.update(codebook=project.config.codebook.model_copy(update={"codes": codes}))


def test_groups_and_their_validation():
    from llmanno.core.config import CodebookConfig

    base = {"source_name": "c", "sha256": "x", "encoding": "utf-8"}
    cb = CodebookConfig(**base, codes=[Code(id="A", parent=" G "), Code(id="B", parent="")])
    assert cb.codes[0].parent == "G" and cb.codes[1].parent is None
    assert cb.groups() == {"A": "G", "B": "B", "KEIN_CODE": "KEIN_CODE"}
    assert CodebookConfig(**base, codes=[Code(id="A")]).groups() == {}
    with pytest.raises(ValueError, match="Gruppenname gleicht einem Code-Kürzel: B"):
        CodebookConfig(**base, codes=[Code(id="A", parent="B"), Code(id="B")])


def test_groups_change_the_plan_but_not_the_run(tmp_path):
    project = make_project(tmp_path)
    calls, before = project.store.count_calls(), fingerprint(project)
    grouped(project)
    assert fingerprint(project) != before  # gehört zum Analyseplan
    engine(project, FakeProvider(llm_answer)).run()
    assert project.store.count_calls() == calls  # nichts neu kodiert
    prepared = []
    Engine(project, provider_factory=lambda cfg: FakeProvider(llm_answer)).prepare(prepared)
    assert prepared[0].config_hash == project.store.latest_config_hash(prepared[0].config.key)
    project.close()


def test_evaluation_report_and_exports_on_both_levels(tmp_path):
    project = make_project(tmp_path, coders=("A", "B"))
    grouped(project)
    report = evaluate(project, resamples=50)
    assert report.groups == ["nah", "fern"]
    by_ref = {g.reference: g.agreement for g in report.group_comparisons}
    code_level = {c.reference: c.agreement for c in report.comparisons}
    assert set(by_ref) == set(code_level)
    assert by_ref["A"].percent.value > code_level["A"].percent.value  # K1 → K2 zählt als richtig
    assert report.group_baseline is not None

    assert "Auf Ebene der 2 Code-Gruppen lag die Übereinstimmung" in methods_paragraph(
        project, report, "de"
    )
    assert "At the level of the 2 code groups" in methods_paragraph(project, report, "en")
    assert "Auf Ebene der Code-Gruppen (nah, fern)" in report_html(project, report)
    export_evaluation(report, tmp_path / "k.xlsx")
    rows = list(openpyxl.load_workbook(tmp_path / "k.xlsx")["Kennwerte"].values)
    assert any(r[0] == "Code-Gruppen" and r[1] == "LLM vs. A" and r[2] == "Cohens Kappa"
               for r in rows)  # fmt: skip

    export_qdpx(project, tmp_path / "p.qdpx")
    with zipfile.ZipFile(tmp_path / "p.qdpx") as archive:
        xml = archive.read("project.qde").decode("utf-8")
    group = xml.index('name="nah" isCodable="false"')
    assert group < xml.index('name="K1"') < xml.index('name="fern"') < xml.index('name="K3"')
    project.close()


def test_codebook_tab_keeps_the_group_on_save(qtbot, tmp_path):
    from llmanno.gui.codebook_page import CodebookPage

    project = make_project(tmp_path, run=False)
    page = CodebookPage()
    qtbot.addWidget(page)
    page.set_project(project)
    assert page.table.horizontalHeaderItem(2).text() == "Gruppe"
    page.table.item(0, 2).setText("nah")  # löst _apply aus
    assert project.config.codebook.codes[0].parent == "nah"
    page.table.item(1, 1).setText("geändertes Label")  # Speichern verliert die Gruppe nicht
    assert project.config.codebook.codes[0].parent == "nah"
    page.refresh()
    assert page.table.item(0, 2).text() == "nah"
    page.table.item(2, 2).setText("K1")
    assert "Gruppenname gleicht einem Code-Kürzel" in page.error.text()
    project.close()
