"""Beispiele aus Dev im Prompt (Few-Shot), Backlog v2: abschaltbar, standardmäßig aus."""

import json
import zipfile

import pytest
from helpers import FakeProvider, engine
from test_evaluation import CODES, TRUTH, llm_answer, make_project

from llmanno.core.engine import fingerprint, prepare_prompts
from llmanno.core.evaluation import evaluate
from llmanno.core.examples import draw_examples, example_ids
from llmanno.core.prompt import render_system
from llmanno.core.validation import create_split, freeze


@pytest.fixture
def project(tmp_path):
    project = make_project(tmp_path)
    yield project
    project.close()


def _with_examples(project, per_code=1):
    parts = create_split(project)
    examples = draw_examples(project, per_code)
    project.update(coding=project.config.coding.model_copy(update={"examples": examples}))
    return parts, examples


def test_off_by_default_and_only_from_dev(project):
    assert project.config.coding.examples == []
    with pytest.raises(ValueError, match="eine Aufteilung erzeugen"):
        draw_examples(project, 1)
    parts, examples = _with_examples(project, 2)
    assert len(examples) == 2 * len(CODES)
    assert all(parts[e.item_id] == "dev" for e in examples)
    assert all(e.codes == [TRUTH[e.item_id]] for e in examples)  # Referenz: Coder A
    assert draw_examples(project, 2) == examples  # fester Seed


def test_examples_in_the_system_prompt_and_identity(project):
    before = fingerprint(project)
    _, examples = _with_examples(project)
    assert fingerprint(project) != before  # gehört zum Instrument
    spec, items = prepare_prompts(project)
    text = {i.item_id: i.target for i in items}
    system = render_system(spec, 1)
    first = examples[0]
    assert "Beispiele aus denselben Daten" in system and text[first.item_id] in system
    assert f'<beispiel codes="{first.codes[0]}">' in system
    hidden = render_system(spec, 1, example_texts=False)
    assert text[first.item_id] not in hidden and f"Einheit {first.item_id}" in hidden
    project.set_pseudonyms([(f"Wort{int(first.item_id[1:])}", "Person 1")])
    spec, _ = prepare_prompts(project, sending=True)
    assert "Person 1" in render_system(spec, 1) and text[first.item_id] not in render_system(
        spec, 1
    )


def test_example_units_are_left_out_of_every_evaluation(project, monkeypatch):
    from llmanno.core import review

    monkeypatch.setattr(review, "MIN_CALIBRATION", 10)
    parts, examples = _with_examples(project)
    shown = example_ids(project.config)
    report = evaluate(project, subset="dev", resamples=20)
    assert report.n_items == sum(p == "dev" for p in parts.values()) - len(shown)
    assert report.n_examples == len(shown)
    assert any("Beispiel-Einheiten" in str(w) for w in report.warnings)

    from llmanno.core.review import gate_calibration
    from llmanno.core.sensitivity import sensitivity_items

    assert not shown & set(sensitivity_items(project))
    assert gate_calibration(project).n == sum(p == "dev" for p in parts.values()) - len(shown)


def test_run_sends_the_examples(project):
    _with_examples(project)
    provider = FakeProvider(llm_answer)
    project.update(coding=project.config.coding.model_copy(update={"base_seed": 5}))
    engine(project, provider).run()
    assert all("<beispiele>" in r.messages[0]["content"] for r in provider.requests)


def test_missing_example_unit_is_reported(project):
    _with_examples(project)
    coding = project.config.coding
    broken = [coding.examples[0].model_copy(update={"item_id": "fehlt"})]
    project.update(coding=coding.model_copy(update={"examples": broken}))
    with pytest.raises(ValueError, match="passen nicht mehr"):
        prepare_prompts(project)


def test_exports_name_the_examples_without_their_texts(project, tmp_path):
    from llmanno.core.export.prereg import preregistration
    from llmanno.core.export.replication import ROOT, export_replication
    from llmanno.core.export.report import report_html

    _, examples = _with_examples(project)
    spec, items = prepare_prompts(project)
    text = {i.item_id: i.target for i in items}[examples[0].item_id]
    prereg = preregistration(project, "de")
    assert "Few-Shot" in prereg and examples[0].item_id in prereg
    assert text not in prereg.split("Beispiel einer User-Nachricht")[0]  # nur die ID im System
    assert "Few-Shot" not in report_html(project, None)  # der Bericht folgt dem Lauf
    engine(project, FakeProvider(llm_answer)).run()
    html = report_html(project, None)
    assert "Few-Shot" in html and text not in html.split("Beispiel einer")[0]
    export_replication(project, tmp_path / "ohne.zip")
    with zipfile.ZipFile(tmp_path / "ohne.zip") as archive:
        assert text not in archive.read(f"{ROOT}/prompts/system_lauf0.txt").decode("utf-8")
    export_replication(project, tmp_path / "mit.zip", include_texts=True)
    with zipfile.ZipFile(tmp_path / "mit.zip") as archive:
        assert text in archive.read(f"{ROOT}/prompts/system_lauf0.txt").decode("utf-8")
        config = json.loads(archive.read(f"{ROOT}/projekt/project.json"))
    assert config["coding"]["examples"][0]["item_id"] == examples[0].item_id


def test_pseudonymized_package_hides_example_ids_and_rerun_restores_them(project, tmp_path):
    import re

    from llmanno.core.export.replication import export_replication
    from llmanno.rerun import rerun

    _with_examples(project)
    engine(project, FakeProvider(llm_answer)).run()
    path = tmp_path / "pseudo.zip"
    export_replication(project, path, pseudonymize_ids=True)
    with zipfile.ZipFile(path) as archive:
        text = "".join(archive.read(n).decode("utf-8", "ignore") for n in archive.namelist())
    assert not re.search(r"\bU\d\d\b", text)
    report = rerun(
        path, tmp_path / "r", data=tmp_path / "d.xlsx",
        provider_factory=lambda cfg: FakeProvider(llm_answer),
    )  # fmt: skip
    assert report.models[0].identical == report.models[0].compared > 0


def test_data_exports_mark_the_example_units(project, tmp_path):
    import openpyxl

    from llmanno.core.export.stats import build_frame
    from llmanno.core.export.xlsx import export_xlsx

    frame, _, _ = build_frame(project, "rds")
    assert "beispiel" not in frame.columns  # ohne Beispiele wie bisher
    _, examples = _with_examples(project)
    frame, _, _ = build_frame(project, "rds")
    marked = set(frame.loc[frame["beispiel"] == 1, "item_id"])
    assert marked == {e.item_id for e in examples}
    export_xlsx(project, tmp_path / "e.xlsx")
    rows = list(openpyxl.load_workbook(tmp_path / "e.xlsx", read_only=True)["Ergebnisse"].values)
    assert rows[0][-1] == "beispiel_im_prompt"
    assert {r[0] for r in rows[1:] if r[-1] == 1} == {e.item_id for e in examples}


def test_new_wave_and_code_system_start_without_examples(project, tmp_path):
    from llmanno.core import codesystems
    from llmanno.core.transfer import transfer_instrument

    _with_examples(project)
    project.save()
    wave = transfer_instrument(project, tmp_path / "welle2", project.data_path, name="Welle 2")
    assert wave.config.coding.examples == []
    wave.close()
    codebook = tmp_path / "rm.txt"
    codebook.write_text("### K1 — x\n### K2 — y\n", encoding="utf-8")
    sub = codesystems.create(project, "Zweites", codebook, ["C"])
    assert sub.config.coding.examples == []
    sub.close()


def test_frozen_instrument_keeps_its_examples(project):
    _with_examples(project)
    freeze(project, {})
    from llmanno.core.validation import freeze_intact

    assert freeze_intact(project)
    project.update(coding=project.config.coding.model_copy(update={"examples": []}))
    assert not freeze_intact(project)


def test_prompt_tab_draws_and_resets(qtbot, project):
    from llmanno.gui.prompt_page import PromptPage

    create_split(project)
    page = PromptPage()
    qtbot.addWidget(page)
    page.set_project(project)
    page.refresh()
    assert "aus:" in page.examples_state.text()
    page.examples_button.click()
    assert len(project.config.coding.examples) == len(CODES)
    assert "<beispiele>" in page.system_view.toPlainText()
    page.unit_mode.setCurrentIndex(page.unit_mode.findData("holistic"))
    assert project.config.coding.examples == []
    assert "neu auswählen" in page.examples_state.text()


# --- Befunde des Reviews ---------------------------------------------------------------------


def test_removing_examples_after_a_run_keeps_them_out_of_the_metrics(project):
    """Review 1: Ausschluss nach den Beispielen des ausgewerteten Laufs, nicht der Einstellung."""
    _, examples = _with_examples(project)
    engine(project, FakeProvider(llm_answer)).run()
    project.update(coding=project.config.coding.model_copy(update={"examples": []}))
    report = evaluate(project, subset="dev", resamples=20)
    assert report.n_examples == len(examples)
    from llmanno.core.review import build_queue

    queue = {e.item_id for e in build_queue(project)}  # Review 6: nicht in die Queue
    assert not queue & {e.item_id for e in examples}


def test_resplit_and_stale_examples_are_refused(project):
    """Review 2, 3, 9."""
    _with_examples(project)
    with pytest.raises(ValueError, match="Erst die Beispiele"):
        create_split(project)
    codebook = project.config.codebook
    codes = [c.model_copy(update={"active": c.id != "K3"}) for c in codebook.codes]
    project.update(codebook=codebook.model_copy(update={"codes": codes}))
    with pytest.raises(ValueError, match="entfernen oder neu auswählen"):
        prepare_prompts(project)


def test_test_evaluation_names_no_examples_and_transfer_note(project, tmp_path):
    """Review 8 und 10."""
    from llmanno.core.transfer import transfer_instrument, transfer_note

    _, examples = _with_examples(project)
    engine(project, FakeProvider(llm_answer)).run()
    freeze(project, {})
    assert evaluate(project, subset="test", resamples=20).n_examples == 0
    project.save()
    wave = transfer_instrument(project, tmp_path / "welle2", project.data_path, name="Welle 2")
    note = transfer_note(wave)
    assert "seither unverändert" in note and f"{len(examples)} Beispiele" in note
    wave.close()


def test_code_system_drops_examples_when_the_main_units_change(project, tmp_path):
    """Review 4."""
    from llmanno.core import codesystems
    from llmanno.core.config import Code, UnitSettings

    create_split(project)
    codebook = tmp_path / "rm.txt"
    codebook.write_text("### K1 — x\n### K2 — y\n### K3 — z\n", encoding="utf-8")
    sub = codesystems.create(project, "Zweites", codebook, ["C"])
    sub.update(
        codebook=sub.config.codebook.model_copy(update={"codes": [Code(id=c) for c in CODES]})
    )
    create_split(sub)
    examples = draw_examples(sub, 1)
    sub.update(coding=sub.config.coding.model_copy(update={"examples": examples}))
    project.update(
        coding=project.config.coding.model_copy(update={"units": UnitSettings(mode="holistic")})
    )
    notes = codesystems.sync(project, sub)
    assert sub.config.coding.examples == [] and any("Beispiele" in n for n in notes)
    sub.close()


def test_guide_dsl_and_package_hashes_with_examples(project, tmp_path):
    """Review 5, 7, 11."""
    import pandas as pd

    from llmanno.core.export.guide import guide_markdown
    from llmanno.core.export.replication import ROOT, export_replication
    from llmanno.core.export.stats import _dsl_hint

    _with_examples(project)
    engine(project, FakeProvider(llm_answer)).run()
    assert "nur ihre IDs" in guide_markdown(project, None, "de")
    hint = _dsl_hint(pd.DataFrame({"dsl_review_K1": [1], "beispiel": [0]}))
    assert "subset(daten, beispiel == 0)" in hint
    export_replication(project, tmp_path / "p.zip")
    with zipfile.ZipFile(tmp_path / "p.zip") as archive:
        calls = archive.read(f"{ROOT}/calls.jsonl").decode("utf-8").splitlines()
    assert all(json.loads(line)["system_sha"] is None for line in calls)
