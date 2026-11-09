"""Neue Spalten-Rollen: Analyse-Variable und „kodieren ja/nein“ (Roadmap 18)."""

import re

import pytest
from helpers import FakeProvider, engine, write_xlsx

from llmanno.core.config import Code, ColumnMapping, ModelConfig, UnitSettings
from llmanno.core.engine import fingerprint, prepare_prompts
from llmanno.core.evaluation import evaluate, subgroup_options
from llmanno.core.project import Project
from llmanno.core.prompt import render

CODES = ["K1", "K2"]
# Lehrkraft (L) nur Kontext, Schüler:innen (S) kodiert; Geschlecht als Analyse-Variable.
ROWS = [
    [f"U{i:02d}", f"T{i // 8}", i % 8, "L" if i % 2 == 0 else "S", f"Satz {i}.",
     "nein" if i % 2 == 0 else "ja", "w" if i % 4 < 2 else "m", CODES[i % 2]]
    for i in range(48)
]  # fmt: skip
HEADER = ["ID", "T", "Nr", "Wer", "Text", "Kodieren", "Geschlecht", "A"]


@pytest.fixture
def project(tmp_path):
    project = Project.create(tmp_path / "p", name="Rollen")
    data = write_xlsx(tmp_path / "d.xlsx", HEADER, ROWS)
    mapping = ColumnMapping(
        id="ID", text=["Text"], group="T", order="Nr", speaker="Wer", gold=["A"],
        analysis=["Geschlecht"],
    )  # fmt: skip
    project.import_data(data, "Daten", mapping)
    codebook = tmp_path / "cb.txt"
    codebook.write_text("### K1 — a\n### K2 — b\n", encoding="utf-8")
    project.load_codebook(codebook)
    project.update(
        codebook=project.config.codebook.model_copy(update={"codes": [Code(id=c) for c in CODES]}),
        panel=[ModelConfig(provider="ollama", model="fake")],
    )
    project.save()
    yield project
    project.close()


def answer(request, n):
    unit = re.search(r'<zieltext id="([^"]+)"', request.messages[1]["content"]).group(1)
    code = CODES[int(unit[1:]) % 2]
    return (
        '{"rationale": "r", "candidates": ['
        f'{{"evidence": ["Satz"], "code": "{code}", "confidence": 80}}]}}'
    )


def test_target_column_codes_only_marked_rows_and_keeps_context(project):
    before = fingerprint(project)
    coding = project.config.coding.model_copy(
        update={"units": UnitSettings(mode="context", target_column="Kodieren")}
    )
    project.update(coding=coding)
    assert fingerprint(project) != before  # gehört zum Instrument
    spec, items = prepare_prompts(project)
    assert len(items) == 24 and all(int(i.item_id[1:]) % 2 for i in items)
    user = render(spec, items[0], 0).user
    assert "Satz 0." in user  # die Lehrkraft-Zeile davor steht als Kontext im Prompt
    engine(project, FakeProvider(answer)).run()
    assert {c.item_id for c in project.store.calls()} == {i.item_id for i in items}


def test_analysis_variable_stays_out_of_the_prompt_but_drives_subgroups(project):
    spec, items = prepare_prompts(project)
    assert all("Geschlecht" not in render(spec, i, 0).user for i in items[:5])
    assert ("analysis:Geschlecht", "Geschlecht (nicht im Prompt)") in subgroup_options(
        project.config
    )
    engine(project, FakeProvider(answer)).run()
    report = evaluate(project, resamples=20, subgroup="analysis:Geschlecht")
    assert {r.value for r in report.comparisons[0].subgroups} == {"w", "m"}
    assert any(t.variable == "Geschlecht (nicht im Prompt)" for t in report.differential)


def test_gui_roles(qtbot, project):
    from llmanno.gui.data_page import DataPage
    from llmanno.gui.prompt_page import PromptPage

    data_page = DataPage()
    qtbot.addWidget(data_page)
    data_page.set_project(project)
    names = [data_page.analysis_list.item(i).text() for i in range(data_page.analysis_list.count())]
    assert "Geschlecht" in names and "Kodieren" in names
    item = data_page.analysis_list.item(names.index("Kodieren"))
    from PySide6.QtCore import Qt

    item.setCheckState(Qt.CheckState.Checked)
    assert set(project.config.data.mapping.analysis) == {"Kodieren", "Geschlecht"}

    prompt_page = PromptPage()
    qtbot.addWidget(prompt_page)
    prompt_page.set_project(project)
    prompt_page.target_column.setCurrentIndex(prompt_page.target_column.findData("Kodieren"))
    assert project.config.coding.units.target_column == "Kodieren"
