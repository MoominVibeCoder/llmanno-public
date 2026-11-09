import re
import sqlite3

import openpyxl
import pytest
from helpers import FakeProvider, engine
from test_evaluation import CODES, TRUTH, WRONG, llm_answer, make_project

from llmanno.core import engine as engine_module
from llmanno.core.evaluation import evaluate
from llmanno.core.export.xlsx import export_xlsx
from llmanno.core.review import (
    RANDOM,
    REVIEW_ALL,
    REVIEW_RANDOM,
    add_review,
    build_queue,
    latest_reviews,
)
from llmanno.core.validation import freeze, freeze_intact

SHAKY = {f"U{i:02d}" for i in range(1, 10)} - WRONG  # hier schwankt das Modell


def shaky_answer(request, n):
    unit = re.search(r'<zieltext id="([^"]+)"', request.messages[1]["content"]).group(1)
    if unit in SHAKY and request.seed % 2 == 0:  # Lauf 2 und 4 weichen ab: p_sc 0,6
        other = CODES[(CODES.index(TRUTH[unit]) + 1) % 3]
        return f'{{"rationale": "r", "candidates": [{{"evidence": ["Text"], "code": "{other}", "confidence": 60}}]}}'  # noqa: E501
    return llm_answer(request, n)


@pytest.fixture
def project(tmp_path):
    project = make_project(tmp_path, run=False)
    engine(project, FakeProvider(shaky_answer)).run()
    review = project.config.review.model_copy(update={"random_share": 0.2, "tau": 0.7})
    project.update(review=review)
    yield project
    project.close()


def test_queue_combines_gate_and_logged_random_sample(project):
    queue = build_queue(project)
    drawn = [e for e in queue if RANDOM in e.reasons]
    gated = [e for e in queue if set(e.reasons) - {RANDOM}]
    assert len(drawn) == 12
    assert {e.item_id for e in gated} == SHAKY
    assert all("low_confidence" in e.reasons for e in gated)
    assert all(e.draw_probability == 1.0 for e in gated)
    assert all(e.draw_probability == pytest.approx(0.2) for e in drawn if e not in gated)
    assert [e.position for e in queue] == list(range(len(queue)))
    log = project.store.audit()[-1]
    assert log.event == "review_queue_created" and log.details["p_random"] == pytest.approx(0.2)
    assert log.details["n_gated"] == len(SHAKY)
    # Gleicher Seed, gleiche Stichprobe; anderer Seed, andere.
    assert {e.item_id for e in build_queue(project) if RANDOM in e.reasons} == {
        e.item_id for e in drawn
    }
    project.update(review=project.config.review.model_copy(update={"seed": 7}))
    assert {e.item_id for e in build_queue(project) if RANDOM in e.reasons} != {
        e.item_id for e in drawn
    }


def test_gate_criteria_can_be_switched_off(project):
    off = project.config.review.model_copy(
        update={"gate_low_confidence": False, "gate_dissent": False, "random_share": 0}
    )
    project.update(review=off)
    assert build_queue(project) == []


def test_reviews_are_validated_and_append_only(project):
    build_queue(project)
    with pytest.raises(ValueError, match="Namen"):
        add_review(project, "U01", " ", "blind", ["K1"])
    with pytest.raises(ValueError, match="genau einen"):
        add_review(project, "U01", "Ana", "blind", ["K1", "K2"])
    with pytest.raises(ValueError, match="Unbekannte"):
        add_review(project, "U01", "Ana", "blind", ["K9"])
    first = add_review(project, "U01", "Ana", "blind", ["K1"], note=" unsicher ")
    assert first.note == "unsicher" and "low_confidence" in first.reasons
    add_review(project, "U01", "Ana", "blind", ["K2"])
    add_review(project, "U01", "Ana", "assisted", ["K3"])
    assert len(project.store.reviews()) == 3
    with pytest.raises(ValueError, match="schon gesehen"):
        add_review(project, "U01", "Ana", "blind", ["K1"])
    current = latest_reviews(project)
    assert current[("U01", "ana", "blind")].codes == ["K2"] and len(current) == 2
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        project.store._con.execute("UPDATE reviews SET codes = '[]'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        project.store._con.execute("DELETE FROM reviews")


def test_blind_reviews_become_a_gold_layer(project, tmp_path):
    queue = build_queue(project)
    for entry in queue:
        add_review(project, entry.item_id, "Ana", "blind", [TRUTH[entry.item_id]])
        add_review(project, entry.item_id, "Ben", "assisted", ["K1"])  # zählt nie als Gold
    report = evaluate(project, resamples=20)
    by_name = {c.reference: c for c in report.comparisons}
    drawn = [e.item_id for e in queue if RANDOM in e.reasons]
    random_cmp = by_name[REVIEW_RANDOM]
    assert random_cmp.agreement.n == len(drawn)
    # Das LLM irrt genau bei WRONG; bei SHAKY gewinnt mit 3 von 5 Läufen der richtige Code.
    hits = sum(1 for i in drawn if i not in WRONG)
    assert random_cmp.accuracy.value == pytest.approx(hits / len(drawn))
    assert by_name[REVIEW_ALL].agreement.n == len(queue)
    assert any("nach unten verzerrt" in w for w in report.warnings)
    assert {"A", "B"} <= set(by_name)  # Gold aus den Daten bleibt daneben

    path = tmp_path / "e.xlsx"
    export_xlsx(project, path)
    rows = list(openpyxl.load_workbook(path)["Reviews"].values)
    assert len(rows) == 1 + 2 * len(queue)
    assert rows[2][2] == "LLM-gestützt korrigiert" and rows[1][7] == "ja"


def test_reviews_alone_are_enough_for_an_evaluation(tmp_path, monkeypatch):
    project = make_project(tmp_path, coders=(), run=True)
    project.update(review=project.config.review.model_copy(update={"random_share": 0.5}))
    for entry in build_queue(project):
        add_review(project, entry.item_id, "Ana", "blind", [TRUTH[entry.item_id]])
    report = evaluate(project, resamples=20)
    assert [c.reference for c in report.comparisons] == [REVIEW_RANDOM]
    assert report.comparisons[0].agreement.n == 30
    project.close()


def test_gate_rules_belong_to_the_frozen_instrument(project, monkeypatch):
    monkeypatch.setattr(engine_module, "make_provider", lambda cfg: FakeProvider(llm_answer))
    freeze(project)
    project.update(review=project.config.review.model_copy(update={"tau": 0.8}))
    assert freeze_intact(project) is False
