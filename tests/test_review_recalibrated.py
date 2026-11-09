"""Gate-Schwelle auf der rekalibrierten Skala (Backlog v2)."""

import pytest
from helpers import FakeProvider, engine
from test_evaluation import WRONG, make_project
from test_review import SHAKY, shaky_answer

from llmanno.core import review as review_module
from llmanno.core.engine import fingerprint, instrument_fingerprint
from llmanno.core.review import RANDOM, build_queue, gate_calibration
from llmanno.core.validation import create_split


@pytest.fixture
def project(tmp_path):
    project = make_project(tmp_path, run=False)
    engine(project, FakeProvider(shaky_answer)).run()
    review = project.config.review.model_copy(update={"random_share": 0.2, "tau": 0.7})
    project.update(review=review)
    yield project
    project.close()


def _recalibrated(project, tau):
    update = {"calibration": gate_calibration(project), "tau": tau, "random_share": 0.0}
    project.update(review=project.config.review.model_copy(update=update))


def test_tau_applies_to_the_recalibrated_accuracy(project):
    """Unsichere Einheiten (p_sc 0,6) sind hier alle richtig, sichere zu 10 von 52 falsch:
    Rekalibriert ist die Self-Consistency nicht informativ, beide liegen bei 50/60."""
    assert {e.item_id for e in build_queue(project) if set(e.reasons) - {RANDOM}} == SHAKY
    before = fingerprint(project)
    _recalibrated(project, 0.8)
    assert fingerprint(project) != before  # die Abbildung gehört zum Instrument
    calibration = project.config.review.calibration
    assert calibration.n == 60 and calibration.xs == [0.6, 1.0] and len(WRONG) == 10
    assert calibration.fitted == pytest.approx([50 / 60] * 2)
    assert not [e for e in build_queue(project) if "low_confidence" in e.reasons]
    log = project.store.audit()[-1].details
    assert log["settings"]["calibration"]["n"] == 60
    project.update(review=project.config.review.model_copy(update={"tau": 0.9}))
    assert len([e for e in build_queue(project) if "low_confidence" in e.reasons]) == 60


def test_only_dev_gold_trains_the_calibration(project, monkeypatch):
    parts = create_split(project)
    assert gate_calibration(project).n == sum(p == "dev" for p in parts.values())
    monkeypatch.setattr(review_module, "MIN_CALIBRATION", 100)
    with pytest.raises(ValueError, match="mindestens 100 Einheiten des Übungsteils"):
        gate_calibration(project)


def test_mapping_is_fixed_and_travels_with_the_instrument(project, tmp_path):
    """Review: nicht bei jeder Queue neu gelernt; neue Welle übernimmt sie, Codesystem nicht."""
    from llmanno.core import codesystems
    from llmanno.core.transfer import transfer_instrument

    _recalibrated(project, 0.8)
    project.save()
    mapping = project.config.review.calibration
    with project.store._con as db:  # Gold ändert sich: die feste Abbildung bleibt
        db.execute("UPDATE units SET gold = '{}'")
    assert build_queue(project) is not None and project.config.review.calibration == mapping
    wave = transfer_instrument(project, tmp_path / "welle2", project.data_path, name="Welle 2")
    assert wave.config.review.calibration == mapping
    assert instrument_fingerprint(wave) == instrument_fingerprint(project)
    wave.close()
    codebook = tmp_path / "rm.txt"
    codebook.write_text("### K1 — x\n### K2 — y\n", encoding="utf-8")
    sub = codesystems.create(project, "Zweites", codebook, ["C"])
    assert sub.config.review.calibration is None  # eigenes Codebuch
    sub.close()


def test_random_only_queue_needs_no_calibration(project):
    """Review: Ohne Gate „geringe Konfidenz“ braucht es kein Gold für die Abbildung."""
    update = {"gate_low_confidence": False, "random_share": 0.2}
    project.update(review=project.config.review.model_copy(update=update))
    assert all(set(e.reasons) <= {RANDOM, "dissent", "abstain"} for e in build_queue(project))


def test_review_tab_switch(qtbot, project, monkeypatch):
    from llmanno.gui.review_page import ReviewPage

    page = ReviewPage()
    qtbot.addWidget(page)
    page.set_project(project)
    page.refresh()
    assert not page.recalibrated.isChecked()
    page.recalibrated.setChecked(True)
    assert project.config.review.calibration.n == 60
    assert "60 Einheiten" in page.calibration_info.text()
    page.recalibrated.setChecked(False)
    assert project.config.review.calibration is None
    monkeypatch.setattr(review_module, "MIN_CALIBRATION", 100)
    page.recalibrated.setChecked(True)
    assert not page.recalibrated.isChecked() and "mindestens 100" in page.calibration_error.text()
