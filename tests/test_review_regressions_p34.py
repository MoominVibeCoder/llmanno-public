"""Regressionstests zu den Befunden des Code-Reviews von P3/P4 (je Befund ein Test)."""

import json
import zipfile
from xml.etree import ElementTree as ET

import pyreadr
import pytest
from helpers import FakeProvider, engine
from test_evaluation import TRUTH, llm_answer, make_project

from llmanno.core import engine as engine_module
from llmanno.core import evaluation as evaluation_module
from llmanno.core.evaluation import evaluate
from llmanno.core.export.refi import export_qdpx
from llmanno.core.export.replication import ROOT, export_replication
from llmanno.core.export.report import methods_paragraph
from llmanno.core.export.stats import export_stats
from llmanno.core.export.xlsx import export_xlsx
from llmanno.core.project import Project
from llmanno.core.review import RANDOM, add_review, build_queue
from llmanno.core.validation import (
    TEST_EXPOSED,
    count_test_evaluations,
    create_split,
    freeze,
)
from llmanno.gui.main_window import MainWindow
from llmanno.rerun import rerun


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(engine_module, "make_provider", lambda cfg: FakeProvider(llm_answer))
    project = make_project(tmp_path)
    yield project
    project.close()


def test_1_package_without_texts_drops_thinking_traces(project, tmp_path):
    meta = {"tokens_in": 1, "thinking": "Wort1 Text K2 heißt …", "repair": {"thinking": "Wort2"}}
    with project.store._con:
        project.store._con.execute("UPDATE calls SET meta = ?", (json.dumps(meta),))
    export_replication(project, tmp_path / "p.zip")
    calls = zipfile.ZipFile(tmp_path / "p.zip").read(f"{ROOT}/calls.jsonl").decode("utf-8")
    assert "Wort1 Text" not in calls and "thinking" not in calls
    assert json.loads(calls.splitlines()[0])["meta"] == {"tokens_in": 1, "repair": {}}


def _window(project):
    window = MainWindow()
    window.set_project(Project.open(project.folder))
    return window


def test_2_review_buttons_follow_a_mode_switch(project, qtbot):
    window = _window(project)
    page = window.review_page
    page.refresh()
    assert page.group.exclusive()
    window.project.update(mode="multi")
    page.refresh()
    assert not page.group.exclusive()
    assert all(type(b).__name__ == "QCheckBox" for b in page.group.buttons())
    window.project.save()
    window.set_project(None)


def test_5_gate_rules_are_locked_after_the_freeze(project, qtbot):
    freeze(project)
    window = _window(project)
    page = window.review_page
    page.refresh()
    assert not page.tau.isEnabled() and not page.random_share.isEnabled()
    assert page.queue_button.isEnabled()  # die Queue darf trotzdem entstehen
    window.set_project(None)


def test_4_dsl_uses_the_probability_of_the_current_queue(project, tmp_path):
    project.update(review=project.config.review.model_copy(update={"random_share": 0.25}))
    entry = next(e for e in build_queue(project) if e.reasons == [RANDOM])
    add_review(project, entry.item_id, "Ana", "blind", [TRUTH[entry.item_id]])
    # Neue Queue mit anderem Anteil: Es gilt das Design der aktuellen Queue (Review zu P4,
    # Simulation: Wahrscheinlichkeiten früherer Ziehungen verzerren die Gewichte).
    project.update(review=project.config.review.model_copy(update={"random_share": 0.5}))
    build_queue(project)
    path = tmp_path / "d.rds"
    export_stats(project, path, "rds")
    frame = pyreadr.read_r(str(path))[None].set_index("item_id")
    assert frame.loc[entry.item_id, "dsl_sample_prob"] == pytest.approx(0.5)
    script = (tmp_path / "d.R").read_text(encoding="utf-8")
    assert "keine blinde Review" in script
    report = evaluate(project, resamples=20)
    assert any("Zufallsstichprobe haben noch keine blinde Review" in w for w in report.warnings)

    with project.store._con:  # eine ungültige Einheit, damit das Gate etwas wählt
        project.store._con.execute(
            "UPDATE calls SET status = 'invalid', answer = 'null' WHERE item_id = 'U00'"
        )
    project.update(review=project.config.review.model_copy(update={"random_share": 0.0}))
    assert [e.item_id for e in build_queue(project)] == ["U00"]
    export_stats(project, path, "rds")
    frame = pyreadr.read_r(str(path))[None]
    assert not any(c.startswith("dsl_") for c in frame.columns)
    assert "Keine dsl-Spalten" in (tmp_path / "d.R").read_text(encoding="utf-8")


def test_6_rerun_compares_final_codes_in_holistic_mode(tmp_path):
    project = make_project(tmp_path, run=False, mode="holistic")

    def answer(request, n):
        return '{"rationale": "r", "candidates": [{"evidence": ["Text"], "code": "K1", "confidence": 70}]}'  # noqa: E501

    engine(project, FakeProvider(answer)).run()
    export_replication(project, tmp_path / "h.zip", include_texts=True)
    project.close()
    report = rerun(
        tmp_path / "h.zip", tmp_path / "r", provider_factory=lambda c: FakeProvider(answer)
    )
    assert report.items_compared == report.items_identical == 3  # drei Transkripte


def test_7_exports_with_test_results_before_the_freeze_are_reported(project, tmp_path):
    create_split(project)
    export_xlsx(project, tmp_path / "e.xlsx")
    assert project.store.audit()[-2].event == TEST_EXPOSED
    freeze(project)
    export_xlsx(project, tmp_path / "e2.xlsx")  # nach dem Freeze nicht mehr
    assert sum(1 for e in project.store.audit() if e.event == TEST_EXPOSED) == 1
    report = evaluate(project, subset="test", resamples=20)
    assert report.test_exposures == 1
    assert any("Test-Sperre umgangen" in w for w in report.warnings)


def test_8_refi_and_stats_skip_codes_dropped_from_the_codebook(project, tmp_path):
    codes = [c.model_copy(update={"active": c.id != "K3"}) for c in project.config.codebook.codes]
    project.update(codebook=project.config.codebook.model_copy(update={"codes": codes}))
    export_qdpx(project, tmp_path / "p.qdpx")
    root = ET.fromstring(zipfile.ZipFile(tmp_path / "p.qdpx").read("project.qde"))
    names = {c.get("name") for c in root.iter("{urn:QDA-XML:project:1.0}Code")}
    assert "K3" not in names
    export_stats(project, tmp_path / "s.sav", "sav")


def test_9_control_characters_do_not_break_the_xml(project, tmp_path):
    (project.folder / "codebook.txt").write_text("### K1 — a\x0c\n### K2 — b\n", encoding="utf-8")
    export_qdpx(project, tmp_path / "p.qdpx")
    ET.fromstring(zipfile.ZipFile(tmp_path / "p.qdpx").read("project.qde"))  # wohlgeformt


def test_10_methods_paragraph_reports_broken_freeze_and_dev_units(project):
    create_split(project)
    freeze(project)
    seed = project.config.coding.base_seed
    project.update(coding=project.config.coding.model_copy(update={"base_seed": seed + 1}))
    report = evaluate(project, subset="all", resamples=20)
    text = methods_paragraph(project, report, "de")
    assert "der Freeze ist gebrochen" in text and "einschließlich der" in text
    assert "vor der Auswertung" not in text


def test_11_failed_test_evaluations_are_not_counted(project, monkeypatch):
    create_split(project)
    freeze(project)

    def boom(*args, **kwargs):
        raise RuntimeError("abgebrochen")

    monkeypatch.setattr(evaluation_module, "_stability", boom)
    with pytest.raises(RuntimeError):
        evaluate(project, subset="test", resamples=20)
    assert count_test_evaluations(project) == 0


def test_12_window_stays_open_while_a_background_task_runs(project, qtbot, monkeypatch):
    window = _window(project)
    monkeypatch.setattr(type(window.split_page), "busy", property(lambda self: True))
    assert window.close() is False
    assert "Hintergrundaufgabe" in window.statusBar().currentMessage()
    monkeypatch.undo()
    window.set_project(None)
