"""Gold-Zuordnung vor der Evaluation: Codebuch-Tab, Split-Warnung, Trennzeichen (Roadmap 5)."""

from collections import Counter

import pytest
from helpers import write_xlsx

from llmanno.core.config import Code, ColumnMapping, ModelConfig
from llmanno.core.gold import gold_values
from llmanno.core.project import Project
from llmanno.core.validation import create_split, split_gaps

# Coder A kodiert mit Ziffern statt Kürzeln: „1“ und „2“ entsprechen keinem Code.
ROWS = [[f"U{i:02d}", f"Text {i}", "K1" if i % 3 else "1", "K1"] for i in range(24)]


@pytest.fixture
def project(tmp_path):
    project = Project.create(tmp_path / "p", name="Gold")
    data = write_xlsx(tmp_path / "d.xlsx", ["ID", "Text", "A", "B"], ROWS)
    project.import_data(data, "Daten", ColumnMapping(id="ID", text=["Text"], gold=["A"]))
    codebook = tmp_path / "cb.txt"
    codebook.write_text("### K1 — a\n### K2 — b\n", encoding="utf-8")
    project.load_codebook(codebook)
    project.update(
        codebook=project.config.codebook.model_copy(
            update={"codes": [Code(id="K1"), Code(id="K2")]}
        ),
        panel=[ModelConfig(provider="ollama", model="fake")],
    )
    project.save()
    yield project
    project.close()


def map_value(project, raw, code):
    gold = project.config.gold
    project.update(gold=gold.model_copy(update={"value_map": {**gold.value_map, raw: code}}))


def test_split_names_the_units_it_leaves_out(project):
    found, unmapped = gold_values(project.config, project.store.units())
    assert unmapped == Counter({"1": 8}) and found["K1"] == 16
    unmapped_items, outside, values = split_gaps(project)
    assert len(unmapped_items) == 8 and outside == [] and values == Counter({"1": 8})

    parts = create_split(project)
    assert len(parts) == 16  # die acht Einheiten mit „1“ fehlen
    map_value(project, "1", "K1")
    unmapped_items, outside, _ = split_gaps(project)
    assert unmapped_items == [] and len(outside) == 8
    assert len(create_split(project)) == 24


def test_codebook_tab_maps_gold_and_split_tab_warns(qtbot, project):
    from llmanno.gui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_project(Project.open(project.folder))
    box = window.codebook_page.gold_box
    window.codebook_page.refresh()
    assert box.table.rowCount() == 1 and box.table.item(0, 0).text() == "1"
    assert "1 Gold-Werte ohne Code" in box.info.text()

    window.split_page.refresh()
    assert "8 Einheiten haben nur Gold ohne Code („1“)" in window.split_page.split_warning.text()

    combo = box.table.cellWidget(0, 2)
    combo.setCurrentIndex(combo.findData("K1"))
    qtbot.waitUntil(lambda: "Alle Gold-Werte entsprechen einem Code" in box.info.text())
    assert window.project.config.gold.value_map == {"1": "K1"}
    assert box.table.rowCount() == 1  # die Zuordnung bleibt sichtbar und änderbar
    window.split_page.refresh()
    assert window.split_page.split_warning.text() == ""
    assert type(window.evaluation_page.gold_box) is type(box)
    window.set_project(None)


def test_codes_from_gold_use_the_separator(tmp_path):
    from llmanno.gui.codebook_page import CodebookPage

    project = Project.create(tmp_path / "m", name="Multi")
    rows = [["1", "Text", "K1, K2"], ["2", "Text", "K3"]]
    data = write_xlsx(tmp_path / "m.xlsx", ["ID", "Text", "A"], rows)
    project.import_data(data, "Daten", ColumnMapping(id="ID", text=["Text"], gold=["A"]))
    codebook = tmp_path / "cb.txt"
    codebook.write_text("### K1 — a\n", encoding="utf-8")
    project.load_codebook(codebook)
    project.update(mode="multi", gold=project.config.gold.model_copy(update={"separator": ","}))
    page = CodebookPage()
    page.set_project(project)
    page._suggest_from_gold()
    ids = sorted(page.table.item(r, 0).text() for r in range(page.table.rowCount()))
    assert ids == ["K1", "K2", "K3"]  # früher mit fest eingebautem „;“: „K1_K2“
    project.close()
