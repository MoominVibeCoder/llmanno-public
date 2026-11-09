"""Stille Zeichen- und Importfehler (Roadmap 16)."""

import unicodedata

import pytest
from helpers import write_xlsx

from llmanno.core.codebook import load_codebook
from llmanno.core.config import ColumnMapping
from llmanno.core.data_import import import_units, read_sheet
from llmanno.core.evidence import locate


def test_decomposed_umlauts_are_found():
    text = unicodedata.normalize("NFD", "Die Bärin läuft über die Straße.")
    found = locate("Bärin läuft", text)
    assert found.status == "normalized"
    assert unicodedata.normalize("NFC", text[found.start : found.end]) == "Bärin läuft"
    back = locate(unicodedata.normalize("NFD", "über"), "Sie geht über die Brücke.")
    assert back.status == "normalized" and back.start == 9 and back.end == 13


def test_codebook_encodings_and_wrong_formats(tmp_path):
    path = tmp_path / "cb.txt"
    path.write_bytes("### K1 — Größe\n".encode("utf-16"))
    loaded = load_codebook(path)
    assert loaded.text == "### K1 — Größe\n" and loaded.encoding == "utf-16"
    path.write_bytes("### K1\n".encode("utf-16-le"))  # ohne BOM: Nullzeichen
    with pytest.raises(ValueError, match="Nullzeichen"):
        load_codebook(path)
    path.write_bytes(b"PK\x03\x04 word/document.xml")
    with pytest.raises(ValueError, match="keine Textdatei"):
        load_codebook(path)
    path.write_bytes(b"### K1 \x81\x8d")  # weder UTF-8 noch cp1252
    with pytest.raises(ValueError, match="Kodierung des Codebuchs ist unbekannt"):
        load_codebook(path)


def test_ambiguous_order_is_reported(tmp_path):
    rows = [[1, "T1", 1.2, "a"], [2, "T1", 1.10, "b"], [3, "T1", 1.1, "c"], [4, "T2", 1, "d"]]
    path = write_xlsx(tmp_path / "d.xlsx", ["ID", "G", "Nr", "Text"], rows)
    mapping = ColumnMapping(id="ID", text=["Text"], group="G", order="Nr")
    messages = [i.message for i in import_units(read_sheet(path), mapping).issues]
    assert any("Dezimalzahlen (z. B. 1.2)" in m for m in messages)
    assert any("1 Reihenfolge-Werte kommen in einer Gruppe mehrfach vor (z. B. 1.1 in „T1“)" in m
               for m in messages)  # fmt: skip
    clean = [[1, "T1", 1, "a"], [2, "T1", 2, "b"]]
    path = write_xlsx(tmp_path / "e.xlsx", ["ID", "G", "Nr", "Text"], clean)
    assert import_units(read_sheet(path), mapping).issues == []


def test_code_list_is_checked_against_the_codebook(qtbot, tmp_path):
    """Roadmap 17: „- 3“ ohne Bedeutung im Prompt, eigene Restkategorie neben KEIN_CODE."""
    from llmanno.core.codebook import code_list_issues
    from llmanno.core.config import Code, CodebookConfig

    text = "### K1 — Frage\nEine Frage.\n\n### SONST — Sonstiges\nAlles andere.\n"
    base = {"source_name": "c", "sha256": "x", "encoding": "utf-8"}
    codes = [Code(id="K1", label="Frage"), Code(id="3"), Code(id="K9", label="Lob"),
             Code(id="SONST", label="Sonstiges")]  # fmt: skip
    issues = code_list_issues(CodebookConfig(**base, codes=codes), text)
    assert any(i.startswith("„3“ hat kein Label und steht nicht im Codebuch") for i in issues)
    assert any(i.startswith("„K9“ steht nicht im Codebuch") for i in issues)
    assert any("„SONST“ sieht nach einer eigenen Restkategorie aus" in i for i in issues)
    assert not any("„K1“" in i for i in issues)
    clean = CodebookConfig(**base, codes=codes[:1] + codes[3:], abstain=False)
    assert code_list_issues(clean, text) == []

    from test_evaluation import make_project

    from llmanno.gui.codebook_page import CodebookPage

    project = make_project(tmp_path, run=False)
    page = CodebookPage()
    qtbot.addWidget(page)
    page.set_project(project)
    assert page.issues.text() == ""
    page._insert_row({"id": "X7"})
    page._apply()
    assert "„X7“ hat kein Label" in page.issues.text()
    project.close()
