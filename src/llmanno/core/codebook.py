"""Codebuch als Freitext laden und Code-Kandidaten für die Bestätigungstabelle vorschlagen.

Der Codebuch-Text selbst wird nie verändert; er geht verbatim in den Prompt.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from llmanno.core.config import CodebookConfig

# Benannte Gruppen: code (Pflicht), label (optional).
# Benannte Gruppe level (optional): nur die häufigste Ebene zählt, damit z. B. der Titel
# „# Codebuch: …“ nicht als Code durchgeht, wenn die Codes auf Ebene ### stehen.
PRESETS: dict[str, str] = {
    "markdown": (
        r"^(?P<level>#{1,6})\s+(?P<code>[A-Za-z0-9_.\-]+)\s*[—–:\-]+\s*(?P<label>.+?)\s*$"
    ),
    "prefix": (
        r"^(?:Code|Kategorie|Category)\s*[:\-]?\s*(?P<code>[A-Za-z0-9_.\-]+)"
        r"(?:\s*[—–:\-]+\s*(?P<label>.+?))?\s*$"
    ),
    "numbered": r"^(?P<code>\d+(?:\.\d+)*)\.?\s+(?P<label>[^\d\s].*?)\s*$",
}

_ID_INVALID = re.compile(r"[^A-Za-z0-9_.\-]+")


@dataclass(frozen=True)
class CodebookText:
    text: str
    encoding: str


@dataclass(frozen=True)
class Suggestion:
    id: str
    label: str
    line: int | None  # Zeile im Codebuch; None = nur aus den Gold-Daten bekannt
    source: str  # Preset-Name, "custom" oder "gold"


# Dateien, die kein Text sind: Word/ODT (ZIP), altes Word, PDF.
_NOT_TEXT = (b"PK\x03\x04", b"\xd0\xcf\x11\xe0", b"%PDF")


def load_codebook(path: Path) -> CodebookText:
    raw = path.read_bytes()
    if raw.startswith(_NOT_TEXT):
        raise ValueError(
            "Das Codebuch ist keine Textdatei (etwa Word, ODT oder PDF). Bitte als reinen Text "
            "speichern (.txt oder .md, UTF-8)."
        )
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):  # UTF-16 mit BOM (Windows-Editor)
        text, encoding = raw.decode("utf-16"), "utf-16"
    else:
        try:
            # utf-8-sig entfernt ein BOM, wie es Windows-Editoren gern schreiben.
            text, encoding = raw.decode("utf-8-sig"), "utf-8"
        except UnicodeDecodeError:
            try:
                text, encoding = raw.decode("cp1252"), "cp1252"
            except UnicodeDecodeError as exc:
                raise ValueError(
                    "Die Kodierung des Codebuchs ist unbekannt. Bitte als UTF-8 speichern."
                ) from exc
    if "\x00" in text:
        raise ValueError(
            "Das Codebuch enthält Nullzeichen (vermutlich UTF-16 ohne Kennung). Bitte als "
            "UTF-8 speichern."
        )
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        raise ValueError("Das Codebuch ist leer.")
    return CodebookText(text, encoding)


def suggest(text: str, pattern: str) -> list[Suggestion]:
    """Findet Codes zeilenweise per Preset-Name oder eigenem Regex mit Gruppe `code`."""
    regex = re.compile(PRESETS.get(pattern, pattern), re.MULTILINE)
    if "code" not in regex.groupindex:
        raise ValueError("Der Ausdruck braucht eine benannte Gruppe (?P<code>…).")
    source = pattern if pattern in PRESETS else "custom"
    matches = [
        (number, match)
        for number, line in enumerate(text.split("\n"), start=1)
        if (match := regex.match(line.strip()))
    ]
    if "level" in regex.groupindex and matches:
        levels = Counter(m.group("level") for _, m in matches)
        top = max(levels, key=lambda lv: (levels[lv], len(lv)))  # Gleichstand: tiefere Ebene
        matches = [(n, m) for n, m in matches if m.group("level") == top]
    found: dict[str, Suggestion] = {}
    for number, match in matches:
        code_id = normalize_id(match.group("code"))
        label = (match.groupdict().get("label") or "").strip()
        if code_id and code_id not in found:
            found[code_id] = Suggestion(code_id, label, number, source)
    return list(found.values())


def suggest_auto(text: str) -> list[Suggestion]:
    """Nimmt das Preset mit den meisten Treffern; bei weniger als zwei Treffern keins."""
    best = max((suggest(text, name) for name in PRESETS), key=len)
    return best if len(best) >= 2 else []


def gold_suggestions(
    values: Iterable[object], known: set[str], separator: str | None
) -> list[Suggestion]:
    """Werte aus den Gold-Spalten, die noch nicht in der Code-Liste stehen.

    `separator` wie gold.separator bei Multi-Label; None (Single-Label) teilt nicht."""
    found: dict[str, Suggestion] = {}
    for value in values:
        if value is None:
            continue
        for part in str(value).split(separator) if separator else [str(value)]:
            code_id = normalize_id(part)
            if code_id and code_id not in known and code_id not in found:
                found[code_id] = Suggestion(code_id, "", None, "gold")
    return sorted(found.values(), key=lambda s: s.id)


# Codes, die nach einer eigenen Restkategorie aussehen (Kürzel oder Label am Anfang).
# Das ganze Kürzel oder Label muss passen: „Andere Perspektive“ oder „Mischform“ sind Codes.
_RESIDUAL = re.compile(
    r"(sonstig\w*( \S+)?|anderes|andere|rest(kategorie)?|kein(e|er)? (code|kategorie|zuordnung)"
    r"|nicht (kodierbar|zuzuordnen|zuordenbar)|unklar(es)?|others?|none|n/?a|misc(ellaneous)?)",
    re.IGNORECASE,
)


def code_list_issues(codebook: CodebookConfig, text: str) -> list[str]:
    """Hinweise zur Code-Liste, gemessen am Codebuch-Text, der im Prompt steht."""
    issues = []
    for code in codebook.active_codes():
        mentioned = re.search(rf"(?<![\w-]){re.escape(code.id)}(?![\w-])", text) is not None
        if not mentioned and not code.label:
            issues.append(
                f"„{code.id}“ hat kein Label und steht nicht im Codebuch: Im Prompt steht nur "
                f"„- {code.id}“, das Modell weiß nicht, was der Code bedeutet."
            )
        elif not mentioned:
            issues.append(
                f"„{code.id}“ steht nicht im Codebuch; das Modell kennt nur das Label "
                f"„{code.label}“."
            )
    if codebook.abstain:
        residual = [
            c.id for c in codebook.active_codes()
            if _RESIDUAL.fullmatch(c.id.replace("_", " ").strip())
            or _RESIDUAL.fullmatch(c.label.strip(" .:!?"))
        ]  # fmt: skip
        if residual:
            issues.append(
                f"{', '.join(f'„{c}“' for c in residual)} sieht nach einer eigenen "
                f"Restkategorie aus und konkurriert mit „{codebook.abstain_id}“. Entweder die "
                "eingebaute Restkategorie ausschalten oder den Code deaktivieren."
            )
    return issues


def normalize_id(raw: str) -> str:
    return _ID_INVALID.sub("_", raw.strip()).strip("_")
