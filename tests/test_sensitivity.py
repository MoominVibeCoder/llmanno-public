"""Prompt-Sensitivität: Varianten, Läufe ohne Einfluss auf die Ergebnisse, inter-prompt α."""

import re
from dataclasses import replace

import pytest
from helpers import FakeProvider, engine
from test_evaluation import CODES, TRUTH, WRONG, llm_answer, make_project

from llmanno.core.engine import ISOLATED, prepare_prompts, results
from llmanno.core.prompt import VARIANT_TEXTS, render_system
from llmanno.core.sensitivity import (
    ORIGINAL,
    run_sensitivity,
    sensitivity_items,
    sensitivity_report,
    variants_for,
)
from llmanno.core.validation import create_split, freeze, freeze_intact


def wording_sensitive(request, n):
    """Weicht bei Umformulierung 2 für jede dritte Einheit ab; sonst wie llm_answer."""
    system, user = request.messages[0]["content"], request.messages[1]["content"]
    unit = re.search(r'<zieltext id="([^"]+)"', user).group(1)
    if system.startswith("Aufgabe: deduktive Inhaltsanalyse") and int(unit[1:]) % 3 == 0:
        other = CODES[(CODES.index(TRUTH[unit]) + 1) % 3]
        return (
            '{"rationale": "r", "candidates": ['
            f'{{"evidence": ["Text"], "code": "{other}", "confidence": 70}}]}}'
        )
    return llm_answer(request, n)


@pytest.fixture
def project(tmp_path):
    project = make_project(tmp_path)  # Kontextmodus, Hauptlauf schon kodiert
    yield project
    project.close()


def test_variants_change_only_the_wording(project):
    spec, _ = prepare_prompts(project)
    original = render_system(spec, 0)
    assert variants_for(project.config) == [ORIGINAL, *VARIANT_TEXTS, ISOLATED]
    for key in VARIANT_TEXTS:
        text = render_system(replace(spec, variant=key), 0)
        assert text != original
        assert '"confidence": <0-100>' in text and "Signalwörter" in text  # Regeln gleich
        assert spec.codebook_text.strip() in text


def test_sensitivity_runs_leave_results_and_freeze_alone(project, monkeypatch):
    from llmanno.core import engine as engine_module

    provider = FakeProvider(wording_sensitive)
    monkeypatch.setattr(engine_module, "make_provider", lambda cfg: provider)  # für freeze
    parts = create_split(project)
    before = results(project, sorted(parts))
    freeze(project)
    ids = sensitivity_items(project)
    assert set(ids) == {i for i, p in project.store.split().items() if p == "dev"}

    done = run_sensitivity(project, ids, provider_factory=lambda cfg: provider)
    assert [v for v, _ in done] == variants_for(project.config)
    assert all(s.calls == len(ids) for _, s in done[1:])  # ein Lauf je Einheit und Variante
    isolated = [r for r in provider.requests if "kontext_davor" not in r.messages[1]["content"]]
    assert len(isolated) >= len(ids)  # die Variante ohne Kontext
    assert results(project, sorted(parts)) == before  # Hauptergebnisse unverändert
    assert freeze_intact(project)
    assert not any(e.event == "freeze_broken" for e in project.store.audit())

    (report,) = sensitivity_report(project, {i: TRUTH[i] for i in ids})
    assert report.n_items == len(ids)
    assert 0 < report.alpha < 1
    rows = {r.variant: r for r in report.rows}
    assert rows["Original"].agreement == 1.0
    assert rows["Umformulierung 2"].agreement < 1.0 and rows["Umformulierung 1"].agreement == 1.0
    assert rows["Umformulierung 2"].alpha_reference < rows["Original"].alpha_reference
    assert report.max_spread[1] > 0
    assert sum(rows["Original"].shares.values()) == pytest.approx(1)

    # Fortsetzen kodiert nichts doppelt.
    again = run_sensitivity(project, ids, provider_factory=lambda cfg: provider)
    assert all(s.calls == 0 for _, s in again)


def test_items_without_split_and_limit(tmp_path):
    project = make_project(tmp_path, mode="isolated", run=False)
    assert variants_for(project.config) == [ORIGINAL, *VARIANT_TEXTS]  # ohne Kontextvariante
    ids = sensitivity_items(project, limit=10)
    assert len(ids) == 10 and ids == sorted(ids)
    assert sensitivity_items(project, limit=10) == ids  # feste Stichprobe
    assert sensitivity_report(project) == []
    engine(project, FakeProvider(llm_answer)).run()
    project.close()


def unstable(request, n):
    """Umformulierung 2 kippt jede zweite Einheit: deutlich instabil."""
    system, user = request.messages[0]["content"], request.messages[1]["content"]
    unit = re.search(r'<zieltext id="([^"]+)"', user).group(1)
    if system.startswith("Aufgabe: deduktive Inhaltsanalyse") and int(unit[1:]) % 2 == 0:
        other = CODES[(CODES.index(TRUTH[unit]) + 1) % 3]
        return (
            '{"rationale": "r", "candidates": ['
            f'{{"evidence": ["Text"], "code": "{other}", "confidence": 70}}]}}'
        )
    return llm_answer(request, n)


def test_sensitivity_from_the_run_tab_to_report_and_exports(qtbot, tmp_path, monkeypatch):
    import openpyxl

    from llmanno.core import engine as engine_module
    from llmanno.core.evaluation import evaluate
    from llmanno.core.export.evaluation import export_evaluation
    from llmanno.core.export.guide import guide_rows
    from llmanno.core.export.report import methods_paragraph, report_html
    from llmanno.core.project import Project
    from llmanno.gui.main_window import MainWindow

    monkeypatch.setattr(engine_module, "make_provider", lambda cfg: FakeProvider(unstable))
    make_project(tmp_path).close()  # ohne Split: alle 60 Einheiten
    window = MainWindow()
    qtbot.addWidget(window)
    window.set_project(Project.open(tmp_path / "p"))
    page = window.run_page
    page.refresh()
    page.sensitivity_limit.setValue(30)
    page.sensitivity_button.click()
    assert page.running and not page.sensitivity_button.isEnabled()
    qtbot.waitUntil(lambda: page.sensitivity_button.isEnabled(), timeout=60000)
    assert not page.error.text()
    assert any("Prompt-Sensitivität, 30 Einheiten × 5 Varianten" in page.log.item(i).text()
               for i in range(page.log.count()))  # fmt: skip

    project = window.project
    report = evaluate(project, resamples=20)
    (s,) = report.sensitivity
    assert s.n_items == 30 and len(s.rows) == 5 and len(s.wording_rows) == 4
    assert s.alpha < 0.8 and s.greedy and s.isolated.agreement == 1.0
    assert any("hängen am Wortlaut" in w for w in report.warnings)
    paragraph = methods_paragraph(project, report, "de")
    assert "mit 4 Formulierungen" in paragraph and "Ohne Kontext stimmten 100 %" in paragraph
    assert "wordings of the instructions" in methods_paragraph(project, report, "en")
    assert "Prompt-Sensitivität: ollama/fake" in report_html(project, report)
    design = next(a for _, t, a in guide_rows(project, report) if t == "Prompt-Design")
    assert "4 Formulierungen auf 30 Einheiten" in design
    export_evaluation(report, tmp_path / "k.xlsx")
    rows = list(openpyxl.load_workbook(tmp_path / "k.xlsx")["Sensitivität"].values)
    assert rows[0][:4] == ("Modell", "alpha inter-prompt", "Variante", "im alpha")
    assert len(rows) == 6 and rows[-1][3] is False  # ohne Kontext nicht im α

    evaluation = window.evaluation_page
    evaluation.set_project(project)
    evaluation._show(report, evaluation._stamp())
    assert "Prompt-Sensitivität: ollama/fake" in evaluation.view.toPlainText()
    window.set_project(None)


def context_only(request, n):
    """Ohne Kontext weicht jede zweite Einheit ab; die Formulierungen sind stabil."""
    user = request.messages[1]["content"]
    unit = re.search(r'<zieltext id="([^"]+)"', user).group(1)
    if "kontext_davor" not in user and "kontext_danach" not in user and int(unit[1:]) % 2 == 0:
        other = CODES[(CODES.index(TRUTH[unit]) + 1) % 3]
        return (
            '{"rationale": "r", "candidates": ['
            f'{{"evidence": ["Text"], "code": "{other}", "confidence": 70}}]}}'
        )
    return llm_answer(request, n)


def test_review_findings_on_sensitivity(tmp_path):
    """Review-Befunde 1, 2, 3, 7: eigene Identität, Instrumentwechsel, Kontext getrennt, greedy."""
    from llmanno.core.config import ModelConfig

    project = make_project(tmp_path, run=False)
    model = ModelConfig(provider="ollama", model="fake", temperature=0.3)
    project.update(panel=[model])
    provider = FakeProvider(context_only)
    engine(project, provider).run(item_limit=10)
    before = results(project, [f"U{i:02d}" for i in range(60)])
    ids = sensitivity_items(project, limit=40)
    run_sensitivity(project, ids, provider_factory=lambda cfg: provider)

    # 1: Das Original der Sensitivität schreibt nicht in den Hauptlauf (gleiche Temperatur 0,3).
    assert results(project, [f"U{i:02d}" for i in range(60)]) == before
    # 7: Die Varianten laufen greedy, auch bei gesetzter Temperatur.
    started = [e for e in project.store.audit() if e.event == "sensitivity_started"]
    assert {e.details["temperature"] for e in started} == {0.0}

    # 3: Die Kontext-Abweichung drückt nicht das α über den Wortlaut.
    (s,) = sensitivity_report(project)
    # Gerade IDs weichen ohne Kontext ab, außer WRONG: dort irrt schon das Original genauso.
    moved = sum(int(i[1:]) % 2 == 0 and i not in WRONG for i in ids)
    assert s.alpha == pytest.approx(1.0)
    assert s.isolated.agreement == pytest.approx(1 - moved / len(ids))
    assert s.max_spread[1] == pytest.approx(0.0)

    # 2: Nach einer Änderung am Instrument zählen die alten Läufe nicht mehr.
    coding = project.config.coding.model_copy(update={"max_rationale_words": 25})
    project.update(coding=coding)
    assert sensitivity_report(project) == []
    project.close()


def test_spread_uses_the_units_common_to_all_wordings(tmp_path):
    """Echter Lauf: eine ungültige Antwort in einer Variante täuschte 1,6 Prozentpunkte vor."""
    project = make_project(tmp_path, run=False)

    def flaky(request, n):
        user = request.messages[1]["content"]
        if "Lies zuerst den Zieltext." in request.messages[0]["content"] and 'id="U00"' in user:
            return "kein JSON"
        return llm_answer(request, n)

    ids = sensitivity_items(project, limit=30)
    run_sensitivity(project, ids, provider_factory=lambda cfg: FakeProvider(flaky))
    (s,) = sensitivity_report(project)
    assert s.n_items == len(ids) - 1 and all(r.n == len(ids) - 1 for r in s.wording_rows)
    assert s.max_spread[1] == pytest.approx(0.0)
    project.close()


def test_sensitivity_before_the_main_run_is_no_result(tmp_path):
    """Roadmap 3: Ohne Hauptlauf fiel latest_run auf den letzten Aufruf zurück, eine Variante."""
    from llmanno.core.engine import result_basis

    project = make_project(tmp_path, run=False)
    ids = sensitivity_items(project, limit=10)
    run_sensitivity(project, ids, provider_factory=lambda cfg: FakeProvider(llm_answer))
    key = project.config.panel[0].key
    assert project.store.latest_run(key) is None
    assert result_basis(project).runs == {}
    assert {r.status for r in results(project, ids).values()} == {"pending"}
    assert sensitivity_report(project)  # die Sensitivität selbst bleibt auswertbar

    engine(project, FakeProvider(llm_answer)).run(item_limit=5)
    assert project.store.latest_run(key) is not None
    project.close()
