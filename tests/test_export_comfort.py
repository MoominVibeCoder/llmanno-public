"""Roadmap 39: Split und Review-Status in XLSX und Statistik, Präregistrierung als Word."""

import openpyxl
import pandas as pd
import pytest
from docx import Document
from test_evaluation import make_project

from llmanno.core.export.prereg import export_preregistration
from llmanno.core.export.stats import export_stats
from llmanno.core.export.xlsx import export_xlsx
from llmanno.core.review import add_review, build_queue, review_status
from llmanno.core.validation import create_split


@pytest.fixture
def project(tmp_path):
    project = make_project(tmp_path)
    yield project
    project.close()


def _prepared(project):
    parts = create_split(project)
    queue = build_queue(project)
    first, second = queue[0].item_id, queue[1].item_id
    add_review(project, first, "Ana", "blind", ["K1"])
    add_review(project, second, "Ana", "assisted", ["K2"])
    return parts, queue, first, second


def test_review_status(project):
    _, queue, first, second = _prepared(project)
    status = review_status(project)
    assert status[first] == "blind" and status[second] == "assistiert"
    assert status[queue[2].item_id] == "offen"
    add_review(project, second, "Ben", "blind", ["K2"])
    assert review_status(project)[second] == "blind"  # blind schlägt assistiert


def test_xlsx_and_stats_carry_split_and_review_status(project, tmp_path):
    parts, _, first, _ = _prepared(project)
    export_xlsx(project, tmp_path / "e.xlsx")
    rows = list(openpyxl.load_workbook(tmp_path / "e.xlsx")["Ergebnisse"].values)
    header = rows[0]
    by_id = {r[0]: dict(zip(header, r, strict=True)) for r in rows[1:]}
    assert header[-2:] == ("split", "review_status")
    assert {by_id[i]["split"] for i in by_id} == {"dev", "test"}
    assert all(by_id[i]["split"] == parts[i] for i in by_id)
    assert by_id[first]["review_status"] == "blind"

    export_stats(project, tmp_path / "d.sav", "sav")
    frame = pd.read_spss(tmp_path / "d.sav", convert_categoricals=False)
    assert set(frame["split"]) == {"dev", "test"}
    assert frame.set_index("item_id").loc[first, "review_status"] == "blind"


def test_stats_without_split_or_reviews_have_no_empty_columns(project, tmp_path):
    export_stats(project, tmp_path / "d.dta", "dta")
    frame = pd.read_stata(tmp_path / "d.dta")
    assert "split" not in frame.columns and "review_status" not in frame.columns


def test_preregistration_as_word(project, tmp_path):
    create_split(project)
    path = tmp_path / "prereg.docx"
    export_preregistration(project, path)
    document = Document(str(path))
    headings = [p.text for p in document.paragraphs if p.style.name.startswith("Heading")]
    assert "1. Daten" in headings and "6. Analyseplan" in headings
    assert document.tables and document.tables[0].rows[0].cells[0].text == "Kürzel"
    text = "\n".join(p.text for p in document.paragraphs)
    assert "**" not in text and "Split-SHA-256" in text
    assert "### K1" in text  # Codebuch-Volltext als Code-Block
    assert project.store.audit()[-1].details["file"] == "prereg.docx"
