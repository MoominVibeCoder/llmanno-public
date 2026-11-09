"""Signalwörter im Zieltext verorten: exakt, normalisiert, unscharf oder nicht belegt."""

from __future__ import annotations

import unicodedata
from dataclasses import asdict, dataclass
from typing import Any, Literal

from rapidfuzz import fuzz

Status = Literal["exact", "normalized", "fuzzy", "missing"]

FUZZY_MIN_SCORE = 90.0
FUZZY_MIN_LENGTH = 4  # kürzere Wörter unscharf zu suchen findet fast immer irgendetwas

# Typografische Varianten, die Modelle beim Kopieren gern vereinheitlichen.
_EQUIVALENTS = str.maketrans(
    {
        "‘": "'", "’": "'", "‚": "'", "‛": "'", "´": "'", "`": "'",
        "“": '"', "”": '"', "„": '"', "‟": '"', "«": '"', "»": '"',
        "–": "-", "—": "-", "‑": "-", "‒": "-", "−": "-",
        " ": " ", " ": " ",
    }
)  # fmt: skip
_STRIP = " \t\n\"'.,;:!?…()[]„“”‚‘’«»"


@dataclass(frozen=True)
class Evidence:
    text: str  # wie vom Modell geliefert
    status: Status
    start: int | None = None  # Zeichen-Offsets im Zieltext, Ende exklusiv
    end: int | None = None
    ambiguous: bool = False  # kommt mehrfach vor; markiert ist das erste Vorkommen

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def locate(needle: str, haystack: str) -> Evidence:
    core = needle.strip(_STRIP)
    if not core:
        return Evidence(needle, "missing")

    start = _find_bounded(haystack, core)
    if start != -1:
        end = start + len(core)
        ambiguous = _find_bounded(haystack, core, start + 1) != -1
        return Evidence(needle, "exact", start, end, ambiguous)

    norm_hay, index = _normalize(haystack)
    norm_needle, _ = _normalize(core)
    if not norm_needle:
        return Evidence(needle, "missing")
    pos = _find_bounded(norm_hay, norm_needle)
    if pos != -1:
        start, end = index[pos], index[pos + len(norm_needle) - 1] + 1
        ambiguous = _find_bounded(norm_hay, norm_needle, pos + 1) != -1
        return Evidence(needle, "normalized", start, end, ambiguous)

    # Nur innerhalb eines Wortes gefunden („Teil“ in „Gegenteil“): kein wörtlicher Beleg,
    # aber eine Annäherung; markiert wird das ganze Wort.
    pos = norm_hay.find(norm_needle)
    if pos != -1:
        start, end = index[pos], index[pos + len(norm_needle) - 1] + 1
        return Evidence(needle, "fuzzy", *_expand_to_words(haystack, start, end))

    # Ist die Nadel länger als der Text, vertauscht rapidfuzz die Rollen, und die Offsets
    # bezögen sich auf die Nadel. Dann gibt es ohnehin keinen sinnvollen Treffer.
    if FUZZY_MIN_LENGTH <= len(norm_needle) <= len(norm_hay):
        match = fuzz.partial_ratio_alignment(norm_needle, norm_hay)
        if (
            match is not None
            and match.score >= FUZZY_MIN_SCORE
            and match.dest_end > match.dest_start
        ):
            start, end = index[match.dest_start], index[match.dest_end - 1] + 1
            return Evidence(needle, "fuzzy", *_expand_to_words(haystack, start, end))
    return Evidence(needle, "missing")


def _find_bounded(text: str, needle: str, start: int = 0) -> int:
    """Erstes Vorkommen, das an Wortgrenzen beginnt und endet (sofern die Nadel dort mit
    einem Buchstaben oder einer Ziffer beginnt bzw. endet)."""
    while (pos := text.find(needle, start)) != -1:
        end = pos + len(needle)
        left_ok = pos == 0 or not (_word(needle[0]) and _word(text[pos - 1]))
        right_ok = end == len(text) or not (_word(needle[-1]) and _word(text[end]))
        if left_ok and right_ok:
            return pos
        start = pos + 1
    return -1


def _word(char: str) -> bool:
    """Teil eines Wortes; ein Trema nach der Zerlegung (NFD) gehört zum Buchstaben davor."""
    return char.isalnum() or unicodedata.combining(char) > 0


def _expand_to_words(text: str, start: int, end: int) -> tuple[int, int]:
    """Unscharfe Treffer bis zu den Wortgrenzen erweitern, damit kein Wort abgeschnitten wird."""
    while start > 0 and _word(text[start - 1]):
        start -= 1
    while end < len(text) and _word(text[end]):
        end += 1
    return start, end


def _normalize(text: str) -> tuple[str, list[int]]:
    """Kleinschreibung, Unicode-Form, typografische Varianten und Leerraum vereinheitlichen.

    Umlaute werden zerlegt (NFD): Ein Text aus macOS oder einem PDF mit „a“ + Trema findet
    dann das „ä“ des Modells und umgekehrt.

    Liefert zusätzlich für jedes Zeichen des Ergebnisses seinen Index im Original, damit
    Treffer auf Offsets im Originaltext zurückgerechnet werden können.
    """
    out: list[str] = []
    index: list[int] = []
    previous_space = True  # führenden Leerraum verwerfen
    for position, char in enumerate(text.translate(_EQUIVALENTS)):
        if char.isspace():
            if previous_space:
                continue
            char, previous_space = " ", True
        else:
            previous_space = False
        for folded in char.casefold():  # ß -> ss: ein Zeichen kann zwei werden
            for part in unicodedata.normalize("NFD", folded):
                out.append(part)
                index.append(position)
    if out and out[-1] == " ":
        out.pop()
        index.pop()
    return "".join(out), index
