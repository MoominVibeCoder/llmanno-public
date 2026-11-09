"""GUIDE-LLM-Checkliste (Feuerriegel et al. 2026), vorausgefüllt."""

import zipfile

import pytest
from helpers import FakeProvider, engine
from test_evaluation import ROWS, llm_answer, make_project

from llmanno.core.config import KI_CONNECT_NRW, ModelConfig
from llmanno.core.evaluation import evaluate
from llmanno.core.export.guide import guide_markdown, guide_rows, todo
from llmanno.core.export.replication import ROOT, export_replication
from llmanno.core.export.report import report_html

CORE = ["A.1", "A.2", "B.1", "B.2", "B.3", "B.4", "B.5", "C.1", "C.2", "D.1", "E.1", "E.2", "F.1",
        "G.1"]  # fmt: skip


@pytest.fixture
def project(tmp_path):
    project = make_project(tmp_path)
    yield project
    project.close()


def test_checklist_is_prefilled_without_raw_texts(project):
    report = evaluate(project, resamples=20)
    rows = guide_rows(project, report)
    assert [p for p, _, _ in rows if p] == CORE and len(rows) == 22
    by_item = {p: a for p, _, a in rows if p}
    assert "60 Einheiten" in by_item["A.1"] and "keine Kategorien" in by_item["A.1"]
    assert "2 Referenzkodierung(en) (A, B)" in by_item["A.2"]
    assert "fake über Ollama (lokal)" in by_item["B.1"] and "Digest d1" in by_item["B.1"]
    assert "Seeds 1 + Laufindex" in by_item["B.3"]
    assert "Cohens κ" in by_item["E.1"] and "Mensch-Mensch-α" in by_item["E.1"]
    assert "Keine Übermittlung an externe Anbieter" in by_item["D.1"]
    assert by_item["G.1"] == todo()
    design = next(a for _, t, a in rows if t == "Prompt-Design")
    assert "1 Lauf-Identität(en)" in design
    everything = " ".join(a for _, _, a in rows)
    assert not any(r[3] in everything for r in ROWS)  # keine Texte der Einheiten

    markdown = guide_markdown(project, report)
    assert (
        markdown.startswith("# GUIDE-LLM-Checkliste")
        and "| G.1 | Interessenkonflikte |" in markdown
    )
    assert "GUIDE-LLM-Checkliste" in report_html(project, report)


def test_checklist_records_consent_iterations_and_goes_into_the_package(project, tmp_path):
    project.update(
        providers=[*project.config.providers, KI_CONNECT_NRW],
        panel=[ModelConfig(provider="kiconnect", model="fake", provider_temperature=True)],
    )
    engine(project, FakeProvider(llm_answer)).run(remote_consent=True)
    rows = {t: a for _, t, a in guide_rows(project, None)}
    assert (
        "Zustimmung zur Übermittlung an externe Anbieter 1-mal protokolliert"
        in rows["Datensensibilität"]
    )
    assert "Temperatur nach Vorgabe des Anbieters" in rows["Parameter"]
    assert "2 Lauf-Identität(en)" in rows["Prompt-Design"]  # lokal und über KI:connect
    assert rows["Validierung"].startswith("Keine Auswertung")

    export_replication(project, tmp_path / "p.zip")
    with zipfile.ZipFile(tmp_path / "p.zip") as archive:
        text = archive.read(f"{ROOT}/guide_llm.md").decode("utf-8")
    assert "| A.1 | Zweck |" in text


def test_checklist_reports_a_broken_freeze(project, monkeypatch):
    """Review-Befund: GUIDE nannte das Instrument eingefroren, obwohl der Freeze gebrochen war."""
    from llmanno.core import engine as engine_module
    from llmanno.core.validation import create_split, freeze

    monkeypatch.setattr(engine_module, "make_provider", lambda cfg: FakeProvider(llm_answer))
    create_split(project)
    freeze(project)
    report = evaluate(project, subset="dev", resamples=20)
    assert "eingefroren" in dict((p, a) for p, _, a in guide_rows(project, report))["E.1"]
    coding = project.config.coding.model_copy(update={"max_rationale_words": 25})
    project.update(coding=coding)
    validation = dict((p, a) for p, _, a in guide_rows(project, report))["E.1"]
    assert "Freeze gebrochen" in validation and "vor der Test-Auswertung" not in validation
