"""Befunde des Reviews zu den Roadmap-Punkten 1–10."""

import shutil
from datetime import UTC, datetime

import pytest
from helpers import FakeProvider, engine, write_xlsx
from test_evaluation import ROWS, llm_answer, make_project

from llmanno.core.aggregate import PANEL_INCOMPLETE
from llmanno.core.config import ModelConfig
from llmanno.core.data_import import units_sha256
from llmanno.core.engine import results
from llmanno.core.export.replication import export_replication
from llmanno.core.export.report import run_models
from llmanno.core.providers.base import ModelInfo
from llmanno.core.store import CallRecord
from llmanno.rerun import rerun

HEADER = ["ID", "Transkript", "Nr", "Text", "A", "B", "C"]
IDS = [r[0] for r in ROWS]


def legacy_project(tmp_path):
    """Wie vor dieser Reihe angelegt: Einheiten-Prüfsumme mit Gold, Lauf unter diesem Wert."""
    project = make_project(tmp_path, coders=("A",), run=False)
    data = project.config.data
    legacy = units_sha256(project.store.units(), legacy=True)
    project.update(data=data.model_copy(update={"units_sha256": legacy}))
    project.save()
    engine(project, FakeProvider(llm_answer)).run()
    return project


def test_rerun_of_a_legacy_project_keeps_its_identity_also_after_a_gold_update(tmp_path):
    """Befunde 1 und 2: alter Hash ließ sich nach update_gold nicht nachrechnen; die
    Rerun-Identität wich immer ab und alles wurde neu kodiert."""
    project = legacy_project(tmp_path)
    export_replication(project, tmp_path / "vorher.zip", include_texts=True)
    report = rerun(tmp_path / "vorher.zip", tmp_path / "r1",
                   provider_factory=lambda cfg: FakeProvider(llm_answer))  # fmt: skip
    assert report.models[0].same_identity

    path = write_xlsx(tmp_path / "neu.xlsx", HEADER, [list(r) for r in ROWS])
    project.update_gold(path, "Daten", ["A", "B"], None)
    export_replication(project, tmp_path / "nachher.zip", include_texts=True)
    report = rerun(tmp_path / "nachher.zip", tmp_path / "r2",
                   provider_factory=lambda cfg: FakeProvider(llm_answer))  # fmt: skip
    assert report.models[0].same_identity and report.items_identical == len(ROWS)
    project.close()


def test_reordered_rows_are_refused_and_the_old_copy_is_kept(tmp_path, monkeypatch):
    """Befunde 1 und 6: umsortierte Zeilen, nicht atomares Ersetzen, stille Verluste."""
    project = make_project(tmp_path, coders=("A", "B"))
    reordered = write_xlsx(tmp_path / "rev.xlsx", HEADER, [list(r) for r in reversed(ROWS)])
    with pytest.raises(ValueError, match="nicht umsortiert"):
        project.update_gold(reordered, "Daten", ["A", "B"], None)

    path = write_xlsx(tmp_path / "neu.xlsx", HEADER, [list(r) for r in ROWS])
    before = {u.unit_id: u.gold for u in project.store.units()}
    real = shutil.copyfile

    def locked(src, dst, *args, **kwargs):  # wie eine von Excel gesperrte Datenkopie
        if str(dst).endswith("data.xlsx"):
            raise PermissionError("gesperrt")
        return real(src, dst, *args, **kwargs)

    monkeypatch.setattr("llmanno.core.project.shutil.copyfile", locked)
    with pytest.raises(PermissionError):
        project.update_gold(path, "Daten", ["A", "C"], None)
    assert {u.unit_id: u.gold for u in project.store.units()} == before
    assert project.config.data.mapping.gold == ["A", "B"]
    monkeypatch.undo()

    project.update_gold(path, "Daten", ["A", "C"], None)
    entry = project.store.audit()[-1].details
    assert entry["dropped"] == ["B"] and entry["backup"].startswith("data.bis-")
    assert (project.folder / entry["backup"]).is_file()
    project.close()


def test_revision_sees_overwritten_errors_and_new_run_starts(tmp_path):
    """Befund 3: Fortsetzen ersetzt Fehler-Aufrufe, die Zahl der Aufrufe bleibt gleich."""
    project = make_project(tmp_path, run=False)
    store = project.store
    system, user = store.put_text("system"), store.put_text("user")

    def call(status, ts):
        return CallRecord(
            "U00", "m", 0, "h", 0, system, user, status,
            {"candidates": []} if status == "ok" else None,
            [], None, [], {}, {}, ts,
        )  # fmt: skip

    store.record_call(call("error", datetime(2026, 1, 1, tzinfo=UTC)))
    first = store.revision()
    store.record_call(call("ok", datetime(2026, 1, 2, tzinfo=UTC)))
    assert store.count_calls() == 1 and store.revision() != first
    second = store.revision()
    store.start_run("m", "h2", {})
    assert store.revision() != second
    project.close()


def test_a_panel_model_that_never_started_makes_the_panel_incomplete(tmp_path):
    """Befund 4: Abbruch beim ersten Modell, das zweite ohne Lauf: kein Flag."""
    project = make_project(tmp_path, run=False)
    first = project.config.panel[0]
    project.update(panel=[first])
    engine(project, FakeProvider(llm_answer)).run()
    project.update(panel=[first, ModelConfig(provider="ollama", model="fake2")])
    flagged = [r for r in results(project, IDS).values() if PANEL_INCOMPLETE in r.flags]
    assert len(flagged) == len(IDS)
    assert all(r.status == "ok" for r in flagged)
    project.close()


def test_report_keeps_the_largest_n_of_a_run(tmp_path):
    """Befund 5: N nach dem Lauf verkleinert, der Bericht nannte das kleinere."""
    project = make_project(tmp_path, run=False)
    coding = project.config.coding
    project.update(coding=coding.model_copy(update={"n_runs": 5, "adaptive": False}))
    provider = FakeProvider(llm_answer)
    provider.list_models = lambda: [ModelInfo("fake", digest="d1")]
    engine(project, provider).run(item_limit=3)
    project.update(coding=coding.model_copy(update={"n_runs": 3, "adaptive": False}))
    engine(project, provider).run(item_limit=3)
    (run,) = run_models(project)
    assert run.n_runs == 5
    project.close()


def test_excluded_models_and_stale_settings_are_visible(qtbot, tmp_path):
    """Roadmap 11: excluded und current las niemand; Stabilität nahm ausgeschlossene Modelle mit."""
    import openpyxl

    from llmanno.core.engine import basis_warnings
    from llmanno.core.evaluation import evaluate
    from llmanno.core.export.xlsx import export_xlsx
    from llmanno.gui.results_page import ResultsPage

    project = make_project(tmp_path, run=False)
    a, b = project.config.panel[0], ModelConfig(provider="ollama", model="fake2")
    provider = FakeProvider(llm_answer)
    provider.list_models = lambda: [ModelInfo(m, digest="d1") for m in ("fake", "fake2")]
    project.update(panel=[a, b])
    engine(project, provider).run()
    project.update(k=2)  # Einstellungen geändert: beide Läufe passen nicht mehr
    assert any("anderen Einstellungen" in w and "aktuelle Konfiguration" in w
               for w in basis_warnings(project))  # fmt: skip
    project.update(panel=[b])
    engine(project, provider).run()  # nur B neu mit k = 2
    project.update(panel=[a, b])
    (warning,) = basis_warnings(project)
    assert warning.startswith("ollama/fake: Der letzte Lauf hatte andere Einstellungen")

    report = evaluate(project, resamples=20)
    assert warning in report.warnings
    assert set(report.stability.intra_alpha) == {b.key}
    page = ResultsPage()
    qtbot.addWidget(page)
    page.set_project(project)
    assert warning in page.summary.text()
    assert {c.model_key for calls in page._calls.values() for c in calls} == {b.key}
    export_xlsx(project, tmp_path / "e.xlsx")
    info = dict(openpyxl.load_workbook(tmp_path / "e.xlsx")["Info"].values)
    assert '"ausgeschlossen": true' in info[f"Modell {a.key}"]
    project.close()


def test_measurement_conditions_reach_log_report_guide_and_environment(tmp_path):
    """Roadmap 12: Ollama-Version, Größe, Quantisierung und Ausgabeformat fehlten überall."""
    import zipfile

    from llmanno.core.export.guide import guide_rows
    from llmanno.core.export.replication import ROOT
    from llmanno.core.export.report import report_html

    project = make_project(tmp_path, run=False)
    provider = FakeProvider(llm_answer)
    details = {"parameter_size": "8.0B", "quantization_level": "Q4_K_M", "family": "llama"}
    provider.list_models = lambda: [ModelInfo("fake", digest="d1", details=details)]
    provider.version = lambda: "0.12.3"
    engine(project, provider).run(item_limit=5)
    start = next(e for e in project.store.audit() if e.event == "run_started").details
    assert start["server_version"] == "0.12.3"
    assert start["model_details"] == details

    html = report_html(project, None)
    assert "8.0B, Q4_K_M" in html and "Ollama 0.12.3" in html and "json_schema" in html
    rows = {topic: text for _, topic, text in guide_rows(project, None)}
    assert "8.0B, Q4_K_M" in rows["Modelle"] and "Ollama 0.12.3" in rows["Modelle"]
    assert "Ausgabeformat json_schema" in rows["Parameter"]
    export_replication(project, tmp_path / "p.zip")
    with zipfile.ZipFile(tmp_path / "p.zip") as archive:
        environment = archive.read(f"{ROOT}/environment.txt").decode("utf-8")
    assert "ollama/fake: Digest d1, 8.0B, Q4_K_M, Ollama 0.12.3, Ausgabeformat json_schema" in (
        environment
    )
    project.close()


def test_more_metrics_have_intervals_and_are_shown(qtbot, tmp_path):
    """Roadmap 13: Scott π, PABAK, MCC und Top-k ohne KI; Abstain-Rate nur im XLSX."""
    import openpyxl

    from llmanno.core.evaluation import evaluate
    from llmanno.core.export.evaluation import export_evaluation
    from llmanno.gui.evaluation_page import EvaluationPage

    project = make_project(tmp_path, coders=("A", "B"))
    report = evaluate(project, resamples=50)
    c = report.comparisons[0]
    for estimate in (c.agreement.scott_pi, c.agreement.brennan_prediger, c.mcc):
        assert estimate.low < estimate.value < estimate.high
    assert c.top_k[2].resamples == 50  # trifft hier immer: Intervall [1; 1]
    export_evaluation(report, tmp_path / "k.xlsx")
    rows = list(openpyxl.load_workbook(tmp_path / "k.xlsx")["Kennwerte"].values)
    for metric in ("Scotts Pi", "Brennan-Prediger", "MCC", "Top-2-Accuracy"):
        row = next(r for r in rows if r[1] == f"LLM vs. {c.reference}" and r[2] == metric)
        assert row[4] is not None and row[5] is not None, metric

    page = EvaluationPage()
    qtbot.addWidget(page)
    page.set_project(project)
    page._show(report, page._stamp())
    assert "Abstain-Rate" in page.view.toPlainText() and "Coverage" in page.view.toPlainText()
    project.close()


def test_exports_before_the_freeze_can_hide_the_test_set(qtbot, tmp_path, monkeypatch):
    """Roadmap 23: Ein XLSX-Export zählte still als Test-Einsicht; es gab kein „nur Dev“."""
    import openpyxl
    from PySide6.QtWidgets import QMessageBox

    from llmanno.core.export.xlsx import export_xlsx
    from llmanno.core.validation import TEST_EXPOSED, create_split
    from llmanno.gui.export_page import ExportPage

    project = make_project(tmp_path)
    parts = create_split(project)
    test = {i for i, p in parts.items() if p == "test"}
    export_xlsx(project, tmp_path / "dev.xlsx", only_dev=True)
    ids = {
        r[0] for r in list(openpyxl.load_workbook(tmp_path / "dev.xlsx")["Ergebnisse"].values)[1:]
    }
    runs = {r[0] for r in list(openpyxl.load_workbook(tmp_path / "dev.xlsx")["Läufe"].values)[1:]}
    assert test and not ids & test and not runs & test
    assert not any(e.event == TEST_EXPOSED for e in project.store.audit())

    page = ExportPage()
    qtbot.addWidget(page)
    page.set_project(project)
    assert page.dev_only.isEnabled() and page.only_dev
    asked = []
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a: asked.append(a) or QMessageBox.StandardButton.No
    )
    monkeypatch.setattr("llmanno.gui.export_page.QFileDialog.getSaveFileName", lambda *a: ("", ""))
    page._ask("_x.xlsx", "", page.export_to, True)
    assert asked == []  # nur Dev: keine Rückfrage
    page.dev_only.setChecked(False)
    page._ask("_x.xlsx", "", page.export_to, True)
    assert len(asked) == 1 and "Test-Einheiten" in asked[0][2]
    page.export_to(tmp_path / "alle.xlsx")
    qtbot.waitUntil(lambda: not page.busy, timeout=60000)
    events = [e.event for e in project.store.audit()]
    assert TEST_EXPOSED in events, (page.error.text(), page.status.text(), events[-4:])
    project.close()


def test_progress_counts_only_open_work_and_runs_through_all_variants(tmp_path):
    """Roadmap 26: nach dem Fortsetzen blieb der Balken stehen; bei der Sensitivität fing
    er je Variante bei 0 an."""
    from llmanno.core.sensitivity import run_sensitivity, sensitivity_items, variants_for

    project = make_project(tmp_path, run=False)
    coding = project.config.coding
    project.update(coding=coding.model_copy(update={"n_runs": 2, "adaptive": False}))
    provider = FakeProvider(llm_answer)
    engine(project, provider).run(item_limit=10)
    seen = []
    engine(project, provider).run(item_limit=25, on_progress=seen.append)
    assert seen[0].planned == 15 * 2 and seen[-1].done == seen[-1].planned == 30

    seen.clear()
    ids = sensitivity_items(project, limit=8)
    run_sensitivity(project, ids, provider_factory=lambda cfg: provider, on_progress=seen.append)
    done = [p.done for p in seen]
    assert done == sorted(done) and done[-1] == len(seen)
    assert seen[-1].planned == seen[-1].done == len(variants_for(project.config)) * len(ids)
    assert all(p.done <= p.planned for p in seen)
    project.close()


def test_model_probe_survives_close_and_project_switch(qtbot, tmp_path):
    """Roadmap 27: Schließen zerstörte den laufenden QThread; nach einem Projektwechsel schrieb
    die Probe in das neue Projekt."""
    import threading

    from llmanno.core.project import Project
    from llmanno.gui.main_window import MainWindow

    first = make_project(tmp_path / "a", run=False)
    second = make_project(tmp_path / "b", run=False)
    folders = first.folder, second.folder
    first.close(), second.close()
    window = MainWindow()
    qtbot.addWidget(window)
    window.set_project(Project.open(folders[0]))
    page = window.models_page
    release, results = threading.Event(), []
    page._start(lambda: release.wait(10) and "fertig", results.append)
    assert page.busy and not window.close()  # Schließen wartet auf die Probe

    window.set_project(Project.open(folders[1]))
    release.set()
    qtbot.waitUntil(lambda: not page.busy, timeout=10000)
    qtbot.wait(50)
    assert results == []  # gehörte zum vorigen Projekt
    page._start(lambda: "neu", results.append)
    qtbot.waitUntil(lambda: results == ["neu"], timeout=10000)
    window.set_project(None)


def test_light_backgrounds_carry_a_dark_text_color(qtbot, tmp_path, monkeypatch):
    """Roadmap 31: Im Dunkelmodus standen helle Hinterlegungen mit heller Schrift."""
    import re

    from llmanno.core.evaluation import evaluate
    from llmanno.gui.evaluation_page import EvaluationPage
    from llmanno.gui.results_page import ResultsPage

    project = make_project(tmp_path)
    results_page = ResultsPage()
    qtbot.addWidget(results_page)
    results_page.set_project(project)
    html = results_page.detail_html("U01")
    page = EvaluationPage()
    qtbot.addWidget(page)
    page.set_project(project)
    rendered = []
    monkeypatch.setattr(page.view, "setHtml", rendered.append)
    page._show(evaluate(project, resamples=20), page._stamp())
    styles = re.findall(r"style='([^']*background:[^']*)'", html + "".join(rendered))
    assert len(styles) > 3 and all("color:" in s for s in styles)
    project.close()


def test_environment_names_every_runtime_dependency(tmp_path):
    """Roadmap 36: In der Exe fehlten scipy, pandas, rapidfuzz und httpx in environment.txt."""
    import zipfile

    from llmanno.core.export.replication import ROOT

    project = make_project(tmp_path)
    export_replication(project, tmp_path / "p.zip")
    with zipfile.ZipFile(tmp_path / "p.zip") as archive:
        environment = archive.read(f"{ROOT}/environment.txt").decode("utf-8")
    runtime = environment.split("Laufzeit-Abhängigkeiten:")[1].split("\n\n")[0]
    for name in ("httpx", "scipy", "pandas", "rapidfuzz", "numpy", "pyside6-essentials"):
        assert f"\n{name}==" in runtime, name
    assert "nicht ermittelbar" not in runtime
    project.close()


def test_opening_refreshes_only_the_visible_tab_and_exports_run_in_background(qtbot, tmp_path):
    """Roadmap 28: Öffnen rechnete alle Tabs; Exporte blockierten das Fenster."""
    from llmanno.core.project import Project
    from llmanno.gui.main_window import MainWindow

    project = make_project(tmp_path)
    folder = project.folder
    project.close()
    window = MainWindow()
    qtbot.addWidget(window)
    window.set_project(Project.open(folder))
    assert window.results_page.table.rowCount() == 0  # noch nicht geladen
    window.tabs.setCurrentWidget(window.results_page)
    assert window.results_page.table.rowCount() == 60

    page = window.export_page
    window.tabs.setCurrentWidget(page)
    page.export_to(tmp_path / "e.xlsx")
    assert page.busy and not page.xlsx_button.isEnabled()
    assert not window.close()  # Schließen wartet auf den Export
    qtbot.waitUntil(lambda: not page.busy, timeout=60000)
    qtbot.waitUntil(lambda: "Gespeichert" in page.status.text())
    assert (tmp_path / "e.xlsx").is_file() and page.xlsx_button.isEnabled()
    window.set_project(None)
