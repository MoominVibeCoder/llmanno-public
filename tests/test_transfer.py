"""Validiertes Instrument auf neue Daten übertragen (Roadmap 19)."""

from helpers import FakeProvider, write_xlsx
from test_evaluation import llm_answer, make_project

from llmanno.core.config import Code
from llmanno.core.engine import instrument_fingerprint
from llmanno.core.export.report import report_html
from llmanno.core.transfer import INSTRUMENT_TRANSFERRED, transfer_instrument
from llmanno.core.validation import create_split, freeze

HEADER = ["ID", "Transkript", "Nr", "Text"]  # neue Welle ohne Gold


def test_new_wave_gets_the_frozen_instrument(tmp_path, monkeypatch):
    from llmanno.core import engine as engine_module

    source = make_project(tmp_path / "alt", coders=("A", "B"))
    codes = [Code(id=c.id, label=f"Label {c.id}", parent="G") for c in source.config.codebook.codes]
    source.update(codebook=source.config.codebook.model_copy(update={"codes": codes}))
    source.save()
    monkeypatch.setattr(engine_module, "make_provider", lambda cfg: FakeProvider(llm_answer))
    create_split(source)
    state = freeze(source)

    rows = [[f"N{i:02d}", f"W{i // 10}", i % 10, f"Neuer Text {i}"] for i in range(30)]
    data = write_xlsx(tmp_path / "welle2.xlsx", HEADER, rows)
    project = transfer_instrument(source, tmp_path / "neu", data)
    cfg = project.config
    assert project.codebook_text == source.codebook_text
    assert cfg.codebook.codes == source.config.codebook.codes
    assert (cfg.coding, cfg.panel, cfg.review, cfg.mode, cfg.k) == (
        source.config.coding, source.config.panel, source.config.review,
        source.config.mode, source.config.k,
    )  # fmt: skip
    assert cfg.freeze is None and project.store.split() == {} and project.store.count_calls() == 0
    assert cfg.data.mapping.gold == [] and cfg.data.n_units == 30
    assert instrument_fingerprint(project) == instrument_fingerprint(source)
    entry = next(e for e in project.store.audit() if e.event == INSTRUMENT_TRANSFERRED)
    assert entry.details["source_instrument_hash"] == state.instrument_hash
    html = report_html(project, None)
    assert "übernommen (dort eingefroren" in html and "seither unverändert" in html

    project.update(coding=cfg.coding.model_copy(update={"max_rationale_words": 33}))
    assert "seither geändert" in report_html(project, None)
    project.close()
    source.close()


def test_menu_opens_the_new_project(qtbot, tmp_path, monkeypatch):
    from llmanno.core.project import Project
    from llmanno.gui.main_window import MainWindow

    source = make_project(tmp_path / "alt")
    folder = source.folder
    source.close()
    rows = [[f"N{i}", "W", i, f"Text {i}"] for i in range(5)]
    data = write_xlsx(tmp_path / "w2.xlsx", HEADER, rows)
    window = MainWindow()
    qtbot.addWidget(window)
    window.set_project(Project.open(folder))
    target = tmp_path / "neu"
    monkeypatch.setattr(
        "llmanno.gui.main_window.QFileDialog.getExistingDirectory", lambda *a: str(target)
    )
    monkeypatch.setattr(
        "llmanno.gui.main_window.QFileDialog.getOpenFileName", lambda *a: (str(data), "")
    )
    window.transfer_action.trigger()
    assert window.project is not None and window.project.folder == target
    assert window.project.config.data.n_units == 5
    window.set_project(None)
