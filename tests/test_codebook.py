import pytest

from llmanno.core.codebook import gold_suggestions, load_codebook, suggest, suggest_auto

MARKDOWN = """# Codebuch Unterrichtsgespräch

### K1 — Frage der Lehrkraft
Definition: …

### K2 – Schülerantwort
Ankerbeispiel: „Ein Teil vom Ganzen.“

### K3: Rückmeldung
"""


def test_markdown_preset():
    found = suggest(MARKDOWN, "markdown")
    assert [(s.id, s.label, s.line) for s in found] == [
        ("K1", "Frage der Lehrkraft", 3),
        ("K2", "Schülerantwort", 6),
        ("K3", "Rückmeldung", 9),
    ]


def test_prefix_and_numbered_presets():
    text = "Kategorie: A1 – Lob\nCode B2\n\n1 Einleitung\n1.2 Begründung\n"
    assert [(s.id, s.label) for s in suggest(text, "prefix")] == [("A1", "Lob"), ("B2", "")]
    assert [s.id for s in suggest(text, "numbered")] == ["1", "1.2"]


def test_custom_regex_needs_code_group():
    assert [s.id for s in suggest("<<X1>> Name", r"^<<(?P<code>\w+)>>")] == ["X1"]
    with pytest.raises(ValueError, match="code"):
        suggest("x", r"^(\w+)")


def test_auto_picks_best_preset_and_needs_two_hits():
    assert [s.id for s in suggest_auto(MARKDOWN)] == ["K1", "K2", "K3"]
    assert suggest_auto("### K1 — nur einer\n") == []


def test_duplicates_keep_first_occurrence():
    found = suggest("### K1 — erst\n### K1 — wieder\n", "markdown")
    assert [(s.id, s.label) for s in found] == [("K1", "erst")]


def test_gold_suggestions_split_and_skip_known():
    found = gold_suggestions(["K1", "K2; K4", None, "K 5"], known={"K1", "K2"}, separator=";")
    assert [s.id for s in found] == ["K4", "K_5"]


def test_load_codebook_handles_bom_crlf_and_cp1252(tmp_path):
    utf8 = tmp_path / "a.txt"
    utf8.write_bytes("﻿### K1 — Größe\r\nText\r\n".encode())
    loaded = load_codebook(utf8)
    assert (loaded.text, loaded.encoding) == ("### K1 — Größe\nText\n", "utf-8")

    ansi = tmp_path / "b.txt"
    ansi.write_bytes("Größe".encode("cp1252"))
    assert load_codebook(ansi).encoding == "cp1252"
    assert load_codebook(ansi).text == "Größe"


def test_load_empty_codebook_fails(tmp_path):
    path = tmp_path / "leer.txt"
    path.write_text("  \n")
    with pytest.raises(ValueError, match="leer"):
        load_codebook(path)


def test_markdown_keeps_only_the_most_common_heading_level():
    text = (
        "# Codebuch: Titel\n\n## Teil A: Lehrkraft\n### K1 — Frage\n### K2 — Lob\n### K3 — Kritik\n"
    )
    assert [s.id for s in suggest(text, "markdown")] == ["K1", "K2", "K3"]
    assert [s.id for s in suggest_auto(text)] == ["K1", "K2", "K3"]
