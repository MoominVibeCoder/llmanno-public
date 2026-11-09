import pytest
from helpers import FakeProvider
from test_evaluation import llm_answer, make_project

from llmanno.core import engine as engine_module
from llmanno.core.evaluation import evaluate
from llmanno.core.export.report import export_report, methods_paragraph, model_facts, report_html
from llmanno.core.validation import create_split, freeze


@pytest.fixture
def project(tmp_path):
    project = make_project(tmp_path, coders=("A", "B", "C"))
    yield project
    project.close()


def test_methods_paragraph_in_german_and_english(project):
    report = evaluate(project, resamples=30)
    de = methods_paragraph(project, report, "de")
    en = methods_paragraph(project, report, "en")
    assert de.startswith("60 Einheiten wurden mit 2 vorangehenden")
    assert "die Mehrheit der Kodierenden" in de and "Cohens κ = 0," in de
    assert "Alternative Annotator Test" in de and "(bestanden)" in de
    assert "the majority of human coders" in en and "Cohen's κ = 0." in en
    kappa = next(c for c in report.comparisons if c.reference.startswith("Mehrheit"))
    assert f"{kappa.agreement.kappa.value:.2f}".replace(".", ",") in de
    assert "eingefroren" not in de  # ohne Freeze kein Satz zum Validierungsdesign
    assert "deduktive Inhaltsanalyse mit festem Codebuch" in de  # Geltungsbereich
    assert "did not develop categories" in en


def test_report_documents_freeze_runs_and_prompt(project, tmp_path, monkeypatch):
    monkeypatch.setattr(engine_module, "make_provider", lambda cfg: FakeProvider(llm_answer))
    create_split(project)
    state = freeze(project)
    report = evaluate(project, subset="test", resamples=30)
    assert "Test-Einheiten eingefroren" in methods_paragraph(project, report, "de")

    facts = model_facts(project)
    calls = project.store.calls()
    assert facts[0].calls == len(calls) and facts[0].digest == "d1"
    assert facts[0].tokens_in == sum(c.meta["tokens_in"] for c in calls)

    path = tmp_path / "r.html"
    export_report(project, report, path)
    html = path.read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>")
    for part in ("Methodenabsatz", "1. Daten", "3. Modelle", "7. Ergebnisse", "Anhang: Prompt"):
        assert part in html
    assert state.instrument_hash in html and "Teilmenge: Test" in html
    assert "### K1 — a" in html  # Codebuch verbatim im Prompt-Anhang
    assert html.count("aria-label='Reliability-Diagramm") == 2  # p_sc und p_verbal
    assert project.store.audit()[-1].details["format"] == "report_html"


def test_report_without_evaluation(project):
    html = report_html(project, None)
    assert "Keine Auswertung berechnet" in html and "Methodenabsatz" in html


def test_docx_report_has_the_same_outline(project, tmp_path):
    from docx import Document

    report = evaluate(project, resamples=30)
    path = tmp_path / "r.docx"
    export_report(project, report, path)
    document = Document(str(path))
    styles = ("Heading", "Title")
    headings = [p.text for p in document.paragraphs if p.style.name.startswith(styles)]
    assert headings[0].startswith("Methoden-Report") and "7. Ergebnisse" in headings
    text = "\n".join(p.text for p in document.paragraphs)
    assert methods_paragraph(project, report, "en") in text
    assert len(document.tables) >= 8  # Daten, Codebuch, Modelle, Parameter, Ergebnisse, …
    assert any("Reliability-Diagramm" in p.text for p in document.paragraphs)
    assert project.store.audit()[-1].details["format"] == "report_docx"
