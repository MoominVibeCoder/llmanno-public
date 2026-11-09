import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from helpers import FakeProvider, engine, write_xlsx
from test_evaluation import ROWS, TRUTH, llm_answer, make_project

from llmanno.core.config import Code, ColumnMapping, ModelConfig, UnitSettings
from llmanno.core.export.refi import EVIDENCE_BRANCH, LLM_USER, export_qdc, export_qdpx
from llmanno.core.project import Project
from llmanno.core.review import add_review

NS = {"q": "urn:QDA-XML:project:1.0"}


def read_qdpx(path):
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("project.qde"))
        texts = {
            name.removeprefix("Sources/"): archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.startswith("Sources/")
        }
    users = {u.get("guid"): u.get("name") for u in root.iterfind("q:Users/q:User", NS)}
    codes = {c.get("guid"): c.get("name") for c in root.iterfind(".//q:Code", NS)}
    selections = []  # (Person, Codes, ausgewählter Text, Element)
    for source in root.iterfind("q:Sources/q:TextSource", NS):
        text = texts[source.get("plainTextPath").removeprefix("internal://")]
        for sel in source.iterfind("q:PlainTextSelection", NS):
            start, end = int(sel.get("startPosition")), int(sel.get("endPosition"))
            refs = [codes[r.get("targetGUID")] for r in sel.iterfind("q:Coding/q:CodeRef", NS)]
            selections.append((users[sel.get("creatingUser")], refs, text[start:end], sel))
    return root, texts, selections


@pytest.fixture
def project(tmp_path):
    project = make_project(tmp_path)
    yield project
    project.close()


def test_qdpx_has_sources_users_codes_and_exact_spans(project, tmp_path):
    add_review(project, "U05", "Ana", "blind", ["K3"])
    summary = export_qdpx(project, tmp_path / "p.qdpx")
    root, texts, selections = read_qdpx(tmp_path / "p.qdpx")

    assert summary.sources == len(texts) == 3  # eine Quelle je Transkript
    assert "[U00] Wort0 Text K1\n" in next(iter(texts.values()))
    assert "\r" not in "".join(texts.values())
    assert [u.get("name") for u in root.iterfind("q:Users/q:User", NS)] == [
        LLM_USER, "A", "B", "Ana (Review blind)",
    ]  # fmt: skip
    top = [c.get("name") for c in root.iterfind("q:CodeBook/q:Codes/q:Code", NS)]
    assert top[:3] == ["K1", "K2", "K3"] and top[-1] == EVIDENCE_BRANCH
    branch = root.find(f"q:CodeBook/q:Codes/q:Code[@name='{EVIDENCE_BRANCH}']", NS)
    assert branch.get("isCodable") == "false" and branch.find("q:Code", NS).get("name") == (
        "K1 (Evidenz)"
    )

    text_of = {r[0]: r[3] for r in ROWS}
    llm = [s for s in selections if s[0] == LLM_USER and "(Evidenz)" not in s[1][0]]
    evidence = [s for s in selections if s[0] == LLM_USER and "(Evidenz)" in s[1][0]]
    assert len(llm) == len(ROWS) and len(evidence) >= len(ROWS)
    for _, codes, selected, element in llm:
        assert len(codes) == 1  # Single-Label: der finale Code
        assert selected == text_of[element.get("name")]
        assert element.find("q:NoteRef", NS) is not None
    assert {sel for _, _, sel, _ in evidence} >= {"Wort0", "Wort1"}
    gold_a = [s for s in selections if s[0] == "A"]
    assert len(gold_a) == len(ROWS)
    assert all(codes == [TRUTH[el.get("name")]] for _, codes, _, el in gold_a)
    assert [s[1] for s in selections if s[0] == "Ana (Review blind)"] == [["K3"]]

    notes = list(root.iterfind("q:Notes/q:Note", NS))
    assert notes[0].get("name") == "Codebuch (verbatim)"
    memo = notes[1].find("q:PlainTextContent", NS).text
    assert "p_sc" in memo and "keine Erklärung" in memo
    assert root.find("q:Description", NS) is not None
    assert project.store.audit()[-1].details["format"] == "qdpx"


def test_guids_are_stable_across_exports(project, tmp_path):
    export_qdpx(project, tmp_path / "a.qdpx")
    export_qdpx(project, tmp_path / "b.qdpx")
    guids = [
        sorted(e.get("guid") for e in read_qdpx(tmp_path / n)[0].iter() if e.get("guid"))
        for n in ("a.qdpx", "b.qdpx")
    ]
    assert guids[0] == guids[1] and len(set(guids[0])) == len(guids[0])


def _small_project(tmp_path, rows, mode, answer):
    project = Project.create(tmp_path / "s", name="Klein")
    data = write_xlsx(tmp_path / "s.xlsx", ["ID", "Gruppe", "Nr", "Sprecher", "Text"], rows)
    project.import_data(
        data, "Daten",
        ColumnMapping(id="ID", text=["Text"], group="Gruppe", order="Nr", speaker="Sprecher"),
    )  # fmt: skip
    codebook = tmp_path / "cb.txt"
    codebook.write_text("### K1 — a\n### K2 — b\n", encoding="utf-8")
    project.load_codebook(codebook)
    project.update(
        codebook=project.config.codebook.model_copy(
            update={"codes": [Code(id="K1"), Code(id="K2")]}
        ),
        coding=project.config.coding.model_copy(update={"units": UnitSettings(mode=mode)}),
        panel=[ModelConfig(provider="ollama", model="fake")],
    )
    project.save()
    engine(project, FakeProvider(lambda r, n: answer)).run()
    return project


def _answer(word):
    candidate = {"evidence": [word], "code": "K1", "confidence": 90}
    return json.dumps({"rationale": "r", "candidates": [candidate]})


def test_offsets_count_unicode_characters(tmp_path):
    rows = [["1", "G", 1, "L", "Äh 😀 ein Bruch, oder?"], ["2", "G", 2, "S", "Ja 👍 Bruch"]]
    project = _small_project(tmp_path, rows, "isolated", _answer("Bruch"))
    export_qdpx(project, tmp_path / "e.qdpx")
    _, texts, selections = read_qdpx(tmp_path / "e.qdpx")
    assert next(iter(texts.values())).startswith("[1] L: Äh 😀 ein Bruch, oder?\n[2] S: ")
    evidence = [s[2] for s in selections if s[1] == ["K1 (Evidenz)"]]
    units = [s[2] for s in selections if s[1] == ["K1"]]
    assert evidence == ["Bruch", "Bruch"]
    assert units == ["Äh 😀 ein Bruch, oder?", "Ja 👍 Bruch"]
    project.close()


def test_holistic_unit_spans_the_group_and_maps_evidence_across_lines(tmp_path):
    rows = [["1", "G", 1, "L", "Was ist ein Bruch?"], ["2", "G", 2, "S", "Ein Teil vom Ganzen."]]
    project = _small_project(tmp_path, rows, "holistic", _answer("Teil"))
    export_qdpx(project, tmp_path / "h.qdpx")
    _, _, selections = read_qdpx(tmp_path / "h.qdpx")
    unit = next(s for s in selections if s[1] == ["K1"])
    assert unit[2] == "Was ist ein Bruch?\n[2] S: Ein Teil vom Ganzen."
    assert [s[2] for s in selections if s[1] == ["K1 (Evidenz)"]] == ["Teil"]
    project.close()


def test_qdc_contains_only_the_codebook(project, tmp_path):
    export_qdc(project, tmp_path / "c.qdc")
    root = ET.parse(tmp_path / "c.qdc").getroot()
    assert root.tag == "{urn:QDA-XML:codebook:1.0}CodeBook"
    names = [c.get("name") for c in root.iter("{urn:QDA-XML:codebook:1.0}Code")]
    assert names[:3] == ["K1", "K2", "K3"] and "K1 (Evidenz)" in names


def test_export_page_writes_qdpx_and_qdc(qtbot, project, tmp_path):
    from llmanno.gui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_project(Project.open(project.folder))
    page = window.export_page
    page.refresh()
    assert page.qdpx_button.isEnabled() and page.qdc_button.isEnabled()
    page.export_qdpx_to(tmp_path / "g.qdpx")
    qtbot.waitUntil(lambda: not page.busy, timeout=60000)
    page.export_qdc_to(tmp_path / "g.qdc")
    qtbot.waitUntil(lambda: not page.busy, timeout=60000)
    assert not page.error.text() and "g.qdc" in page.status.text()
    assert zipfile.is_zipfile(tmp_path / "g.qdpx") and (tmp_path / "g.qdc").is_file()


SCHEMAS = Path(__file__).parent / "data" / "refi"


@pytest.mark.parametrize("variant", ["single", "multi", "holistic"])
def test_exports_validate_against_the_official_schema(tmp_path, variant):
    from lxml import etree

    mode = "holistic" if variant == "holistic" else "context"
    project = make_project(tmp_path, mode=mode, run=False)
    if variant == "multi":
        project.update(mode="multi")
        project.save()
    # Holistisch gibt es keine Zeilen-ID im Prompt; dann eine feste Antwort.
    script = llm_answer if variant != "holistic" else (lambda r, n: _answer("Text"))
    engine(project, FakeProvider(script)).run()
    first = next(iter(results_ids(project)))
    add_review(project, first, "Ana", "blind", ["K1"])
    export_qdpx(project, tmp_path / "p.qdpx")
    export_qdc(project, tmp_path / "c.qdc")
    project_schema = etree.XMLSchema(etree.parse(str(SCHEMAS / "Project-mrt2019.xsd")))
    codebook_schema = etree.XMLSchema(etree.parse(str(SCHEMAS / "Codebook-mrt2019.xsd")))
    with zipfile.ZipFile(tmp_path / "p.qdpx") as archive:
        document = etree.fromstring(archive.read("project.qde"))
    assert project_schema.validate(document), project_schema.error_log
    assert codebook_schema.validate(etree.parse(str(tmp_path / "c.qdc"))), codebook_schema.error_log
    project.close()


def results_ids(project):
    from llmanno.core.engine import prepare_prompts

    return [i.item_id for i in prepare_prompts(project)[1]]
