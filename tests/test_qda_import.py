"""Roadmap 42: Gold aus einem QDA-Programm (REFI-QDA) zurück ins Projekt."""

import re
import zipfile
from xml.etree import ElementTree as ET

import pytest
from test_evaluation import TRUTH, make_project

from llmanno.core.engine import fingerprint
from llmanno.core.evaluation import evaluate
from llmanno.core.export.refi import NS, export_qdpx
from llmanno.core.qda_import import add_qda_gold, default_coders, read_qdpx

CODED = [f"U{i:02d}" for i in range(10)]


def _tag(name):
    return f"{{{NS}}}{name}"


def _code_as_qda(exported, out, wrong=("U00", "K3")):
    """Wie ein QDA-Programm: Zeilenenden \\r\\n, neue Person „Mara“ kodiert U00–U09 (U00 mit
    falschem Code), dazu ein unbekannter Code und eine Kodierung im Evidenz-Zweig."""
    with zipfile.ZipFile(exported) as archive:
        files = {n: archive.read(n) for n in archive.namelist()}
    ET.register_namespace("", NS)
    root = ET.fromstring(files["project.qde"])
    ET.SubElement(root.find(_tag("Users")), _tag("User"), guid="mara", name="Mara")
    codes = {c.get("name"): c.get("guid") for c in root.iter(_tag("Code"))}
    container = root.find(_tag("CodeBook")).find(_tag("Codes"))
    ET.SubElement(container, _tag("Code"), guid="sonst", name="Sonstiges", isCodable="true")
    for source in root.iter(_tag("TextSource")):
        name = source.get("plainTextPath").removeprefix("internal://")
        text = files[f"Sources/{name}"].decode("utf-8").replace("\n", "\r\n")
        files[f"Sources/{name}"] = text.encode("utf-8")
        markers = [(m.start(), m.group(1)) for m in re.finditer(r"^\[([^\]]+)\] ", text, re.M)]
        for i, (start, unit) in enumerate(markers):
            if unit not in CODED:
                continue
            end = markers[i + 1][0] - 2 if i + 1 < len(markers) else len(text)
            code = wrong[1] if unit == wrong[0] else TRUTH[unit]
            for target in (codes[code], "sonst", codes[f"{code} (Evidenz)"]):
                selection = ET.SubElement(
                    source, _tag("PlainTextSelection"), guid=f"s-{unit}-{target}",
                    startPosition=str(start + 3), endPosition=str(end), creatingUser="mara",
                )  # fmt: skip
                coding = ET.SubElement(selection, _tag("Coding"), guid=f"c-{unit}-{target}",
                                       creatingUser="mara")  # fmt: skip
                ET.SubElement(coding, _tag("CodeRef"), targetGUID=target)
    files["project.qde"] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    with zipfile.ZipFile(out, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return out


@pytest.fixture
def project(tmp_path):
    project = make_project(tmp_path)
    yield project
    project.close()


def test_gold_from_a_qda_program(project, tmp_path):
    exported = tmp_path / "export.qdpx"
    export_qdpx(project, exported)
    coded = _code_as_qda(exported, tmp_path / "kodiert.qdpx")
    before = fingerprint(project)

    qda = read_qdpx(coded, project)
    assert qda.units_found == 60 and qda.unknown_codes == {"Sonstiges": 10}
    assert {"LLM-Panel", "A", "B", "Mara"} <= set(qda.coders)
    assert default_coders(qda, project) == ["Mara"]
    labels = qda.coders["Mara"].labels(multi=False, separator=";")
    assert labels == {u: ("K3" if u == "U00" else TRUTH[u]) for u in CODED}

    add_qda_gold(project, qda, ["Mara"], tmp_path / "mit_qda.xlsx")
    assert project.config.data.mapping.gold == ["A", "B", "Mara (QDA)"]
    gold = {u.unit_id: u.gold.get("Mara (QDA)") for u in project.store.units()}
    assert gold["U01"] == TRUTH["U01"] and gold["U00"] == "K3" and gold["U30"] is None
    assert fingerprint(project) == before  # Gold gehört nicht zum Instrument
    comparison = next(c for c in evaluate(project, resamples=20).comparisons
                      if c.reference == "Mara (QDA)")  # fmt: skip
    assert comparison.agreement.n == 10


def test_foreign_qda_projects_are_refused(project, tmp_path):
    foreign = tmp_path / "fremd.qdpx"
    with zipfile.ZipFile(foreign, "w") as archive:
        archive.writestr("project.qde", f"<Project xmlns='{NS}'><Sources/></Project>")
    with pytest.raises(ValueError, match="Marker"):
        read_qdpx(foreign, project)
    broken = tmp_path / "kaputt.qdpx"
    broken.write_bytes(b"kein zip")
    with pytest.raises(ValueError, match="kein REFI-QDA-Projekt"):
        read_qdpx(broken, project)


def test_data_page_takes_qda_gold(qtbot, project, tmp_path):
    from PySide6.QtCore import Qt

    from llmanno.gui.data_page import CoderDialog, DataPage

    exported = tmp_path / "export.qdpx"
    export_qdpx(project, exported)
    qda = read_qdpx(_code_as_qda(exported, tmp_path / "kodiert.qdpx"), project)
    dialog = CoderDialog(qda, default_coders(qda, project))
    qtbot.addWidget(dialog)
    assert dialog.chosen() == ["Mara"]
    dialog.list.item(list(qda.coders).index("B")).setCheckState(Qt.CheckState.Checked)
    assert set(dialog.chosen()) == {"Mara", "B"}

    page = DataPage()
    qtbot.addWidget(page)
    page.set_project(project)
    assert page.qda_button.isEnabled()
    page.add_qda(qda, ["Mara"])
    assert not page.error.text()
    assert "Mara" in page.report.item(0).text() and "Sonstiges (10×)" in page.report.item(1).text()
    assert "Mara (QDA)" in project.config.data.mapping.gold
    assert list((project.folder / "exports").glob("daten_mit_qda_*.xlsx"))
