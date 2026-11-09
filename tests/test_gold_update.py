"""Gold nachtragen nach Kodierläufen und Freeze (Roadmap 6)."""

import pytest
from helpers import FakeProvider, engine, write_xlsx
from test_evaluation import ROWS, llm_answer, make_project

from llmanno.core.config import ColumnMapping
from llmanno.core.data_import import import_units, read_sheet, units_sha256
from llmanno.core.engine import fingerprint
from llmanno.core.evaluation import evaluate
from llmanno.core.project import GOLD_UPDATED, Project
from llmanno.core.validation import create_split, freeze, freeze_intact

HEADER = ["ID", "Transkript", "Nr", "Text", "A", "B", "C"]


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    """Coder A kodiert, das LLM ist durch, das Instrument eingefroren."""
    from llmanno.core import engine as engine_module

    project = make_project(tmp_path, coders=("A",))
    monkeypatch.setattr(engine_module, "make_provider", lambda cfg: FakeProvider(llm_answer))
    create_split(project)
    freeze(project)
    yield project
    project.close()


def rows_with(changes):
    rows = [list(r) for r in ROWS]
    for (row, column), value in changes.items():
        rows[row][HEADER.index(column)] = value
    return rows


def test_coder_b_joins_after_the_freeze(frozen, tmp_path):
    before = (fingerprint(frozen), frozen.config.data.units_sha256, frozen.store.count_calls())
    rows = rows_with({(1, "A"): "K3"})  # A korrigiert eine Einheit, B kommt dazu
    frozen.update_gold(write_xlsx(tmp_path / "neu.xlsx", HEADER, rows), "Daten", ["A", "B"], None)

    assert frozen.config.data.mapping.gold == ["A", "B"] and frozen.config.data.source_name == (
        "neu.xlsx"
    )
    assert (fingerprint(frozen), frozen.config.data.units_sha256) == before[:2]
    assert freeze_intact(frozen)
    engine(frozen, FakeProvider(llm_answer)).run()
    assert frozen.store.count_calls() == before[2]  # nichts neu kodiert
    unit = next(u for u in frozen.store.units() if u.unit_id == "U01")
    assert unit.gold == {"A": "K3", "B": ROWS[1][5]} and unit.source["A"] == "K3"
    entry = next(e for e in frozen.store.audit() if e.event == GOLD_UPDATED)
    assert entry.details["gold"] == ["A", "B"] and entry.details["n_changed"] == len(ROWS)

    report = evaluate(frozen, resamples=20, subset="dev")
    assert {"A", "B"} <= {c.reference for c in report.comparisons}
    assert not any("nach der ersten Test-Auswertung" in w for w in report.warnings)


def test_changed_texts_or_missing_columns_are_refused(frozen, tmp_path):
    path = write_xlsx(tmp_path / "t.xlsx", HEADER, rows_with({(2, "Text"): "anders"}))
    with pytest.raises(ValueError, match="1 Einheiten passen nicht.*„U02“"):
        frozen.update_gold(path, "Daten", ["A", "B"], None)
    short = write_xlsx(tmp_path / "s.xlsx", ["ID", "A", "B"], [[r[0], r[4], r[5]] for r in ROWS])
    with pytest.raises(ValueError, match="es fehlen die Spalten „Transkript“, „Nr“, „Text“"):
        frozen.update_gold(short, "Daten", ["A", "B"], None)
    assert frozen.config.data.mapping.gold == ["A"]
    assert not any(e.event == GOLD_UPDATED for e in frozen.store.audit())


CHANGED = "Die Referenz auf dem Test-Set hat sich seit der ersten Test-Auswertung geändert"


def test_reference_changed_after_a_test_evaluation_is_reported(frozen, tmp_path):
    """Review-Befund: Auch Zuordnung und Trennzeichen ändern die Referenz, nicht nur Nachträge."""
    evaluate(frozen, resamples=20, subset="test")
    again = evaluate(frozen, resamples=20, subset="test")
    assert not any(w.startswith(CHANGED) for w in again.warnings)

    gold = frozen.config.gold
    frozen.update(gold=gold.model_copy(update={"value_map": {"K2": "K3"}}))
    assert any(w.startswith(CHANGED) for w in evaluate(frozen, resamples=20).warnings)
    frozen.update(gold=gold)  # zurück: dieselbe Referenz wie bei der ersten Test-Auswertung
    assert not any(w.startswith(CHANGED) for w in evaluate(frozen, resamples=20).warnings)

    path = write_xlsx(tmp_path / "neu.xlsx", HEADER, rows_with({(i, "A"): "K1" for i in range(60)}))
    frozen.update_gold(path, "Daten", ["A"], None)
    assert any(w.startswith(CHANGED) for w in evaluate(frozen, resamples=20, subset="dev").warnings)


def test_units_hash_leaves_out_gold_and_unmapped_columns(tmp_path):
    path = write_xlsx(tmp_path / "d.xlsx", HEADER, ROWS)
    sheet = read_sheet(path)

    def units(gold):
        return import_units(sheet, ColumnMapping(id="ID", text=["Text"], gold=gold)).units

    assert units_sha256(units(["A"])) == units_sha256(units(["A", "B"])) == units_sha256(units([]))
    assert units_sha256(units(["A"]), legacy=True) != units_sha256(units(["A", "B"]), legacy=True)


def test_data_tab_updates_gold_after_runs(qtbot, frozen, tmp_path):
    from PySide6.QtCore import Qt

    from llmanno.gui.common import checked_entries
    from llmanno.gui.data_page import DataPage

    path = write_xlsx(tmp_path / "neu.xlsx", HEADER, ROWS)
    project = Project.open(frozen.folder)
    page = DataPage()
    qtbot.addWidget(page)
    page.set_project(project)
    assert not page.import_button.isEnabled() and not page.gold_button.isEnabled()
    page.open_file(path)
    assert page.gold_button.isEnabled() and not page.import_button.isEnabled()
    assert not page.text_list.isEnabled() and page.gold_list.isEnabled()
    for row in range(page.gold_list.count()):
        item = page.gold_list.item(row)
        if item.text() == "B":
            item.setCheckState(Qt.CheckState.Checked)
    assert checked_entries(page.gold_list) == ["A", "B"]
    page.gold_button.click()
    assert page.error.text() == ""
    assert project.config.data.mapping.gold == ["A", "B"]
    assert "Gold aktualisiert" in page.report.item(0).text()
    project.close()
