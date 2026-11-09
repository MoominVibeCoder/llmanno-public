"""Punkt 20: mehrere Codesysteme je Projekt, je eigener Aufruf."""

import zipfile

import pytest
from helpers import FakeProvider, engine
from test_evaluation import CODES, ROWS, llm_answer, make_project

from llmanno.core import codesystems
from llmanno.core.config import Code, ModelConfig, UnitSettings
from llmanno.core.evaluation import evaluate
from llmanno.core.project import Project


def _codebook(tmp_path):
    path = tmp_path / "rm.txt"
    path.write_text(
        "### K1 — RMX Bezug\n### K2 — RMX kein Bezug\n### K3 — unklar\n", encoding="utf-8"
    )
    return path


def _add(main, tmp_path, name="Reference Move", gold=("C",)):
    sub = codesystems.create(main, name, _codebook(tmp_path), list(gold))
    sub.update(
        codebook=sub.config.codebook.model_copy(update={"codes": [Code(id=c) for c in CODES]})
    )
    sub.save()
    return sub


def test_codesystem_codes_with_its_own_codebook_and_gold(tmp_path):
    main = make_project(tmp_path)
    sub = _add(main, tmp_path)
    assert codesystems.names(main) == ["Reference Move"] and codesystems.is_codesystem(sub)
    assert sub.config.data.units_sha256 == main.config.data.units_sha256
    assert sub.config.data.mapping.gold == ["C"] and sub.config.panel == main.config.panel
    before = main.store.count_calls()
    provider = FakeProvider(llm_answer)
    engine(sub, provider).run()
    assert all("RMX" in r.messages[0]["content"] for r in provider.requests)  # eigenes Codebuch
    assert sub.store.count_calls() == len(provider.requests) > 0
    assert main.store.count_calls() == before  # eigene Aufrufe, eigener Store
    assert evaluate(sub, resamples=20).comparisons  # eigene Auswertung gegen Gold C
    assert any(e.event == codesystems.CODESYSTEM_ADDED for e in main.store.audit())
    sub.close()
    main.close()


def test_sync_takes_over_panel_units_language_and_names(tmp_path):
    main = make_project(tmp_path, run=False)
    sub = _add(main, tmp_path)
    sub.update(coding=sub.config.coding.model_copy(update={"n_runs": 1}))  # bleibt eigen
    sub.save()
    main.update(
        prompt_language="en",
        panel=[*main.config.panel, ModelConfig(provider="ollama", model="zwei")],
        coding=main.config.coding.model_copy(update={"units": UnitSettings(mode="isolated")}),
    )
    main.save()
    main.set_pseudonyms([("Wort1", "Person 1")])
    assert codesystems.sync(main, sub) == []
    assert sub.config.prompt_language == "en" and len(sub.config.panel) == 2
    assert sub.config.coding.units.mode == "isolated" and sub.config.coding.n_runs == 1
    assert sub.pseudonym_pairs == [("Wort1", "Person 1")] and sub.pseudonymizer()
    main.set_pseudonyms([])
    codesystems.sync(main, sub)
    assert sub.pseudonymizer() is None
    sub.close()
    main.close()


def test_gold_added_to_the_main_data_reaches_the_codesystem(tmp_path):
    from helpers import write_xlsx

    main = make_project(tmp_path, run=False)
    sub = _add(main, tmp_path, gold=())
    header = ["ID", "Transkript", "Nr", "Text", "A", "B", "C", "RM"]
    data = write_xlsx(tmp_path / "neu.xlsx", header, [[*r, "K2"] for r in ROWS])
    main.update_gold(data, "Daten", ["A", "B"], None)
    sub.update(
        data=sub.config.data.model_copy(
            update={"mapping": sub.config.data.mapping.model_copy(update={"gold": ["RM"]})}
        )
    )
    sub.save()
    codesystems.sync(main, sub)
    assert {u.gold.get("RM") for u in sub.store.units()} == {"K2"}
    sub.close()
    main.close()


def test_open_all_and_name_rules(tmp_path):
    main = make_project(tmp_path, run=False)
    _add(main, tmp_path).close()
    with pytest.raises(ValueError, match="gibt es schon"):
        codesystems.create(main, "reference move", _codebook(tmp_path), ["C"])
    with pytest.raises(ValueError, match="1–40 Zeichen"):
        codesystems.create(main, "../raus", _codebook(tmp_path), ["C"])
    with pytest.raises(ValueError, match="Spalten fehlen"):
        codesystems.create(main, "Kaputt", _codebook(tmp_path), ["gibt es nicht"])
    assert codesystems.names(main) == ["Reference Move"]  # Fehlversuch hinterlässt nichts
    opened, notes = codesystems.open_all(main)
    assert notes == []
    assert list(opened) == ["Reference Move"]
    for project in opened.values():
        project.close()
    main.close()


@pytest.fixture
def window(qtbot, tmp_path):
    from llmanno.gui.main_window import MainWindow

    main = make_project(tmp_path)
    sub = _add(main, tmp_path)
    engine(sub, FakeProvider(llm_answer)).run()
    sub.close()
    folder = main.folder
    main.close()
    window = MainWindow()
    qtbot.addWidget(window)
    window.set_project(Project.open(folder))
    yield window
    window.set_project(None)


def test_switching_code_systems_rebinds_only_their_tabs(window):
    assert [window.system_combo.itemText(i) for i in range(window.system_combo.count())] == [
        "Hauptprojekt", "Reference Move",
    ]  # fmt: skip
    window.system_combo.setCurrentIndex(1)
    sub = window.codesystems["Reference Move"]
    assert window.active is sub
    for page in window.system_pages:
        assert page.project is sub
    assert window.data_page.project is window.project is window.models_page.project
    assert window.run_page.project is window.project
    window.system_combo.setCurrentIndex(0)
    assert window.codebook_page.project is window.project


def test_one_xlsx_holds_all_code_systems(qtbot, window, tmp_path):
    import openpyxl

    page = window.export_page
    page.refresh()
    assert page.all_systems.isVisibleTo(page)
    page.export_to(tmp_path / "alle.xlsx")
    qtbot.waitUntil(lambda: not page.busy, timeout=60000)
    sheets = openpyxl.load_workbook(tmp_path / "alle.xlsx", read_only=True).sheetnames
    assert sheets[:2] == ["Ergebnisse", "Läufe"]
    assert "Ergebnisse – Reference Move" in sheets and "Codes – Reference Move" in sheets


def test_add_code_system_from_the_window(window, tmp_path, monkeypatch):
    from llmanno.gui.codesystem_dialog import CodesystemDialog

    def fill(dialog):
        dialog.name.setText("Zweite Dimension")
        dialog.codebook = _codebook(tmp_path)
        return True

    monkeypatch.setattr(CodesystemDialog, "exec", fill)
    monkeypatch.setattr(CodesystemDialog, "gold_columns", lambda self: ["B"])
    window.add_codesystem()
    assert list(window.codesystems) == ["Reference Move", "Zweite Dimension"]
    assert window.active is window.codesystems["Zweite Dimension"]
    assert window.tabs.currentWidget() is window.codebook_page
    # Wie beim Laden im Tab Codebuch: die Codes sind gleich vorgeschlagen (noch ungespeichert).
    assert window.active.config.codebook.codes and window.isWindowModified()
    window.save_project()


def test_one_run_codes_every_code_system(qtbot, window, monkeypatch):
    from llmanno.core import engine as engine_module

    sub = window.codesystems["Reference Move"]
    prompts = []

    def provider(cfg):
        def answer(request, n):
            prompts.append(request.messages[0]["content"])
            return llm_answer(request, n)

        return FakeProvider(answer)

    monkeypatch.setattr(engine_module, "make_provider", provider)
    for project, seed in ((window.project, 7), (sub, 3)):  # beide brauchen einen neuen Lauf
        project.update(coding=project.config.coding.model_copy(update={"base_seed": seed}))
    window.run_page.start(pilot=True)
    qtbot.waitUntil(lambda: not window.run_page.running, timeout=30000)
    assert window.run_page.error.text() == ""
    assert any("RMX" in p for p in prompts) and any("RMX" not in p for p in prompts)
    log = [window.run_page.log.item(i).text() for i in range(window.run_page.log.count())]
    assert any("2 Codesysteme" in line for line in log)
    assert sub.config.coding.base_seed == 3  # eigene Prompt-Parameter bleiben beim Abgleich


def test_split_is_shared_when_the_same_units_have_gold(tmp_path):
    from llmanno.core.validation import create_split

    main = make_project(tmp_path, run=False)
    main_parts = create_split(main)
    same = _add(main, tmp_path, "Gleich", gold=("C",))  # Gold für dieselben Einheiten
    assert create_split(same) == main_parts
    assert [
        e.details["from_main_project"] for e in same.store.audit() if e.event == "split_created"
    ] == [True]
    same.close()
    main.close()


def test_shared_settings_are_locked_in_the_prompt_tab_of_a_code_system(window):
    window.system_combo.setCurrentIndex(1)
    page = window.prompt_page
    page.refresh()
    assert not page.unit_mode.isEnabled() and not page.names_button.isEnabled()
    assert "Hauptprojekt" in page.shared_hint.text()
    assert page.n_runs.isEnabled()  # eigene Prompt-Parameter bleiben einstellbar
    window.system_combo.setCurrentIndex(0)
    page.refresh()
    assert page.unit_mode.isEnabled() and page.shared_hint.text() == ""


def test_gold_columns_of_a_code_system_can_change(window, monkeypatch):
    from llmanno.gui.codesystem_dialog import CodesystemDialog

    window.system_combo.setCurrentIndex(1)
    assert window.gold_system_button.isEnabled()
    monkeypatch.setattr(CodesystemDialog, "exec", lambda self: True)
    monkeypatch.setattr(CodesystemDialog, "gold_columns", lambda self: ["B", "C"])
    window.edit_codesystem_gold()
    sub = window.codesystems["Reference Move"]
    assert sub.config.data.mapping.gold == ["B", "C"]
    assert window.project.config.data.mapping.gold == ["A", "B"]  # Hauptprojekt unberührt


# --- Befunde des Reviews zu Punkt 20 -------------------------------------------------------------


def test_xlsx_with_all_systems_asks_for_test_exposure_of_each(qtbot, window, monkeypatch):
    """Review 1: Gewählt ist ein Codesystem ohne Split, das Hauptprojekt hat Test-Einheiten."""
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    from llmanno.core.validation import create_split

    create_split(window.project)
    window.system_combo.setCurrentIndex(1)
    page = window.export_page
    page.refresh()
    assert page.dev_only.isEnabled()  # Test-Einheiten des Hauptprojekts gehen mit
    asked = []
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a: asked.append(a[2]) or QMessageBox.StandardButton.No
    )
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: ("", ""))
    page.dev_only.setChecked(False)
    page.xlsx_button.click()
    assert asked and "Eval:" in asked[0]


def test_sheet_names_are_valid_and_unique(tmp_path):
    import openpyxl

    from llmanno.core.export.xlsx import sheet_title

    workbook = openpyxl.Workbook()
    for title in ["Ergebnisse – Demo: Unterricht [1]/2", "Ergebnisse – Demo: Unterricht [1]/3"]:
        workbook.create_sheet(sheet_title(workbook, title))
    names = workbook.sheetnames[1:]
    assert len(set(names)) == 2 and all(len(n) <= 31 and ":" not in n for n in names)


def test_unfinished_code_system_is_skipped_not_blocking(qtbot, window, tmp_path, monkeypatch):
    """Review 3: Ein neues Codesystem ohne bestätigte Codes hält den Lauf nicht auf."""
    from llmanno.core import engine as engine_module

    window.codesystems["Leer"] = codesystems.create(window.project, "Leer", _codebook(tmp_path), [])
    window.codesystems["Leer"].update(
        codebook=window.codesystems["Leer"].config.codebook.model_copy(update={"codes": []})
    )
    monkeypatch.setattr(engine_module, "make_provider", lambda cfg: FakeProvider(llm_answer))
    main = window.project
    main.update(coding=main.config.coding.model_copy(update={"base_seed": 9}))
    window.run_page.start(pilot=True)
    qtbot.waitUntil(lambda: not window.run_page.running, timeout=30000)
    log = [window.run_page.log.item(i).text() for i in range(window.run_page.log.count())]
    assert any("„Leer“ übersprungen" in line for line in log)
    assert any(line.split("  ", 1)[-1].startswith("Fertig") for line in log)


def test_missing_gold_columns_keep_the_code_systems_gold(tmp_path):
    """Review 5: Gold-Nachtrag im Hauptprojekt ohne die Spalte des Codesystems."""
    from helpers import write_xlsx

    main = make_project(tmp_path, run=False)
    sub = _add(main, tmp_path, gold=("C",))
    before = {u.unit_id: u.gold.get("C") for u in sub.store.units()}
    data = write_xlsx(tmp_path / "ab.xlsx", ["ID", "Transkript", "Nr", "Text", "A", "B"],
                      [r[:6] for r in ROWS])  # fmt: skip
    main.update_gold(data, "Daten", ["A", "B"], None)
    notes = codesystems.sync(main, sub)
    assert notes and "fehlen seine Gold-Spalten (C)" in notes[0]
    assert sub.config.data.mapping.gold == ["C"]
    assert {u.unit_id: u.gold.get("C") for u in sub.store.units()} == before
    sub.close()
    main.close()


def test_switching_does_not_save_and_names_are_checked(window):
    """Review 8 und 15."""
    import json

    sub = window.codesystems["Reference Move"]
    on_disk = json.loads((sub.folder / "project.json").read_text(encoding="utf-8"))
    window.project.update(prompt_language="en")  # ungespeichert im Hauptprojekt
    window.system_combo.setCurrentIndex(1)
    assert sub.config.prompt_language == "en"
    assert json.loads((sub.folder / "project.json").read_text(encoding="utf-8")) == on_disk
    for bad in ("RM.", "nul", "Eval"):
        with pytest.raises(ValueError):
            codesystems.check_name(window.project, bad)
    window.save_project()  # sonst fragt das Schließen nach


def test_one_qdpx_holds_all_code_systems(tmp_path):
    """Gemeinsamer REFI-QDA-Export: Quellen einmal, je Codesystem ein eigener Code-Zweig."""
    from lxml import etree
    from test_export_refi import NS, SCHEMAS, read_qdpx

    from llmanno.core.export.refi import EVIDENCE_BRANCH, LLM_USER, export_qdpx
    from llmanno.core.review import add_review

    main = make_project(tmp_path)
    sub = _add(main, tmp_path)
    engine(sub, FakeProvider(llm_answer)).run()
    add_review(sub, "U05", "Ana", "blind", ["K3"])
    summary = export_qdpx(main, tmp_path / "alle.qdpx", others={"Reference Move": sub})
    root, texts, selections = read_qdpx(tmp_path / "alle.qdpx")

    assert summary.sources == len(texts) == 3
    top = root.findall("q:CodeBook/q:Codes/q:Code", NS)
    assert [c.get("name") for c in top] == ["Eval", "Reference Move"]
    assert {c.get("isCodable") for c in top} == {"false"}
    inner = [c.get("name") for c in top[1].findall("q:Code", NS)]
    assert inner[:3] == ["K1", "K2", "K3"] and inner[-1] == EVIDENCE_BRANCH
    assert [u.get("name") for u in root.iterfind("q:Users/q:User", NS)] == [
        LLM_USER, "A", "B", "C", "Ana (Review blind)",
    ]  # fmt: skip
    llm = [s for s in selections if s[0] == LLM_USER and "(Evidenz)" not in s[1][0]]
    assert len(llm) == 2 * len(ROWS)  # je Einheit eine Kodierung je Codesystem
    guids = [e.get("guid") for e in root.iter() if e.get("guid")]
    assert len(set(guids)) == len(guids)
    notes = [n.get("name") for n in root.iterfind("q:Notes/q:Note", NS)]
    assert {"Codebuch Eval (verbatim)", "Codebuch Reference Move (verbatim)"} <= set(notes)
    schema = etree.XMLSchema(etree.parse(str(SCHEMAS / "Project-mrt2019.xsd")))
    with zipfile.ZipFile(tmp_path / "alle.qdpx") as archive:
        assert schema.validate(etree.fromstring(archive.read("project.qde"))), schema.error_log
    for project in (main, sub):
        assert project.store.audit()[-1].details["format"] == "qdpx"
    sub.close()
    main.close()


def test_qdpx_of_code_systems_with_other_units_is_refused(tmp_path):
    from llmanno.core.export.refi import export_qdpx

    for folder in ("a", "b"):
        (tmp_path / folder).mkdir()
    main = make_project(tmp_path / "a")
    other = make_project(tmp_path / "b", run=False)
    with other.store._con as db:
        db.execute("UPDATE units SET text = 'anders' WHERE unit_id = 'U00'")
    with pytest.raises(ValueError, match="andere Daten"):
        export_qdpx(main, tmp_path / "x.qdpx", others={"Anders": other})
    assert not (tmp_path / "x.qdpx").exists()
    other.close()
    main.close()


def test_combined_qdpx_is_the_same_from_either_system(tmp_path):
    """Review: Kennungen und Reihenfolge hängen nicht vom gewählten Codesystem ab."""
    from test_export_refi import NS, read_qdpx

    from llmanno.core.export.refi import export_qdpx

    main = make_project(tmp_path)
    sub = _add(main, tmp_path)
    engine(sub, FakeProvider(llm_answer)).run()
    export_qdpx(main, tmp_path / "a.qdpx", others={"Reference Move": sub})
    export_qdpx(sub, tmp_path / "b.qdpx", others={"Eval": main})
    roots = [read_qdpx(tmp_path / n)[0] for n in ("a.qdpx", "b.qdpx")]
    guids = [sorted(e.get("guid") for e in r.iter() if e.get("guid")) for r in roots]
    assert guids[0] == guids[1]
    tops = [[c.get("name") for c in r.findall("q:CodeBook/q:Codes/q:Code", NS)] for r in roots]
    assert tops == [["Eval", "Reference Move"]] * 2
    with pytest.raises(ValueError, match="Zwei Codesysteme"):
        export_qdpx(main, tmp_path / "c.qdpx", others={"Eval": sub})
    sub.close()
    main.close()


def test_unfinished_code_system_is_named_and_skipped_in_the_tab(qtbot, window, tmp_path):
    """Review: Ein neues Codesystem ohne bestätigte Codes blockiert den Export nicht."""
    from llmanno.core.export.refi import export_qdpx

    empty = codesystems.create(window.project, "Leer", _codebook(tmp_path), [])
    empty.update(codebook=empty.config.codebook.model_copy(update={"codes": []}))
    with pytest.raises(ValueError, match="Codesystem „Leer“"):
        export_qdpx(window.project, tmp_path / "x.qdpx", others={"Leer": empty})
    window.codesystems["Leer"] = empty
    page = window.export_page
    page.refresh()
    page.export_qdpx_to(tmp_path / "alle.qdpx")
    qtbot.waitUntil(lambda: not page.busy, timeout=60000)
    assert not page.error.text() and "ohne Leer" in page.status.text()


def test_export_page_writes_one_qdpx_for_all_systems(qtbot, window, tmp_path):
    from test_export_refi import NS, read_qdpx

    page = window.export_page
    page.refresh()
    page.export_qdpx_to(tmp_path / "alle.qdpx")
    qtbot.waitUntil(lambda: not page.busy, timeout=60000)
    assert not page.error.text()
    root = read_qdpx(tmp_path / "alle.qdpx")[0]
    top = [c.get("name") for c in root.findall("q:CodeBook/q:Codes/q:Code", NS)]
    assert top == ["Eval", "Reference Move"]
    page.all_systems.setChecked(False)
    page.export_qdpx_to(tmp_path / "eins.qdpx")
    qtbot.waitUntil(lambda: not page.busy, timeout=60000)
    top = [c.get("name") for c in read_qdpx(tmp_path / "eins.qdpx")[0].findall(
        "q:CodeBook/q:Codes/q:Code", NS)]  # fmt: skip
    assert top[:3] == ["K1", "K2", "K3"]


def test_a_locked_code_system_does_not_hide_the_others(tmp_path):
    """Review 12: alles oder nichts beim Öffnen."""
    from llmanno.core import lock

    main = make_project(tmp_path, run=False)
    _add(main, tmp_path, "Eins").close()
    _add(main, tmp_path, "Zwei").close()
    (main.folder / codesystems.SUBDIR / "Eins" / lock.LOCK_FILE).write_text(
        '{"pid": 999999, "host": "anderer-rechner", "since": "2026-01-01"}', encoding="utf-8"
    )
    opened, notes = codesystems.open_all(main)
    assert list(opened) == ["Zwei"] and "„Eins“ nicht geöffnet" in notes[0]
    for project in opened.values():
        project.close()
    main.close()
