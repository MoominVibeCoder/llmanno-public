"""Namen vor dem Senden ersetzen (Pseudonymisierung).

Die Liste (Original → Ersatz) liegt als `pseudonyme.tsv` im Projektordner, nicht in
project.json: Die steht im Replikationspaket. In der Konfiguration steht nur eine Prüfsumme
(`coding.pseudonyms_sha256`); sie gehört zur Lauf-Identität. Die Datei trägt einen zufälligen
Schlüssel, damit sich die Namen nicht aus der Prüfsumme erraten lassen. Frühere Fassungen
bleiben in `pseudonyme_alt/` (für den Rerun älterer Pakete).

Ersetzt wird beim Erzeugen des Prompts, in Text, Sprecher und Metadaten, als ganzes Wort mit
Groß- und Kleinschreibung, auch mit Genitiv-s („Lenas“ → „Person 1s“), in beiden Unicode-
Formen (NFC/NFD) und bei Namen aus mehreren Wörtern mit beliebigem Leerraum dazwischen.
Signalwörter verortet die Engine im ersetzten Text und rechnet die Stellen auf den Originaltext
zurück. Codebuch und IDs bleiben unverändert.
"""

from __future__ import annotations

import re
import secrets
import unicodedata
from dataclasses import dataclass, replace
from functools import cached_property
from pathlib import Path

from llmanno.core.evidence import Evidence, locate
from llmanno.core.store import sha256_text
from llmanno.core.units import Item, Line

PSEUDONYM_FILE = "pseudonyme.tsv"
HISTORY_DIR = "pseudonyme_alt"
_KEY_LINE = "# llmanno-Pseudonyme, Schlüssel "
_HEADER = "Original\tErsatz"
SHORT = 2  # Sprecherwerte bis zu dieser Länge sind meist Rollen („I“, „B“, „L“), keine Namen


def normalize_name(name: str) -> str:
    return unicodedata.normalize("NFC", " ".join(name.split()))


def _name_pattern(name: str) -> str:
    forms = {unicodedata.normalize(form, name) for form in ("NFC", "NFD")}
    return "|".join(r"\s+".join(map(re.escape, f.split())) for f in sorted(forms))


@dataclass(frozen=True)
class Pseudonymizer:
    pairs: tuple[tuple[str, str], ...]

    @cached_property
    def _map(self) -> dict[str, str]:
        return {normalize_name(o): r for o, r in self.pairs}

    def _compiled(self, flags: int = 0) -> re.Pattern[str]:
        names = sorted(self._map, key=len, reverse=True)
        if not names:
            return re.compile(r"(?!)()()")  # trifft nie
        alternatives = "|".join(_name_pattern(n) for n in names)
        return re.compile(r"(?<!\w)(" + alternatives + r")(s?)(?!\w)", flags)

    @cached_property
    def _pattern(self) -> re.Pattern[str]:
        return self._compiled()

    def _new(self, match: re.Match[str]) -> str:
        return self._map[normalize_name(match[1])] + match[2]

    def apply(self, text: str) -> str:
        return self._pattern.sub(self._new, text)

    def item(self, item: Item) -> Item:
        def line(ln: Line) -> Line:
            speaker = self.apply(ln.speaker) if ln.speaker else ln.speaker
            return replace(ln, speaker=speaker, text=self.apply(ln.text))

        return replace(
            item,
            lines=tuple(map(line, item.lines)),
            before=tuple(map(line, item.before)),
            after=tuple(map(line, item.after)),
            meta={k: self.apply(v) if isinstance(v, str) else v for k, v in item.meta.items()},
        )

    def locate(self, needle: str, target: str) -> Evidence:
        """Signalwort im ersetzten Text suchen (das Modell kennt nur den), Stellen im Original."""
        regions: list[tuple[int, int, int, int]] = []  # Original von–bis, Ersatz von–bis
        parts: list[str] = []
        last = shift = 0
        for m in self._pattern.finditer(target):
            new = self._new(m)
            parts += [target[last : m.start()], new]
            start = m.start() + shift
            regions.append((m.start(), m.end(), start, start + len(new)))
            shift += len(new) - (m.end() - m.start())
            last = m.end()
        evidence = locate(needle, "".join(parts) + target[last:])
        if evidence.start is None or evidence.end is None:
            return evidence
        return replace(
            evidence,
            start=_to_original(regions, evidence.start, end=False),
            end=_to_original(regions, evidence.end, end=True),
        )

    def counts(self, texts: list[str], *, ignore_case: bool = False) -> dict[str, int]:
        """Treffer je Original (normalisiert). `ignore_case`: auch anders geschriebene."""
        found = dict.fromkeys(self._map, 0)
        pattern = self._compiled(re.IGNORECASE) if ignore_case else self._pattern
        folded = {k.casefold(): k for k in self._map}
        for text in texts:
            for m in pattern.finditer(text):
                key = normalize_name(m[1])
                found[key if key in found else folded[key.casefold()]] += 1
        return found


def _to_original(regions: list[tuple[int, int, int, int]], offset: int, *, end: bool) -> int:
    """Offset im ersetzten Text → Offset im Original; innerhalb eines Ersatzes dessen Rand."""
    delta = 0
    for o_start, o_end, n_start, n_end in regions:
        if offset < n_start or (end and offset == n_start):
            break
        if offset < n_end or (end and offset == n_end):
            return o_end if end else o_start
        delta = o_end - n_end
    return offset + delta


def check_pairs(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Bereinigt und prüft die Liste; Fehler als Satz."""
    cleaned = [(normalize_name(o), " ".join(r.split())) for o, r in pairs if o.strip() or r.strip()]
    for original, replacement in cleaned:
        if not original or not replacement:
            entry = original or replacement
            raise ValueError(f"Jeder Eintrag braucht Original und Ersatz: „{entry}“.")
        if original == replacement:
            raise ValueError(f"„{original}“ wird durch sich selbst ersetzt.")
    originals = [o for o, _ in cleaned]
    duplicates = sorted({o for o in originals if originals.count(o) > 1})
    if duplicates:
        raise ValueError(f"Doppelt in der Liste: {', '.join(duplicates)}.")
    names = Pseudonymizer(tuple(cleaned))
    for original, replacement in cleaned:
        if hit := [o for o, n in names.counts([replacement]).items() if n]:
            raise ValueError(
                f"Der Ersatz für „{original}“ enthält selbst einen Namen der Liste "
                f"(„{hit[0]}“); er ginge so an das Modell."
            )
    return cleaned


def next_pseudonyms(pairs: list[tuple[str, str]], n: int) -> list[str]:
    """Freie Ersatznamen „Person 1“, „Person 2“ … nach den schon vergebenen."""
    used = {r for _, r in pairs}
    out: list[str] = []
    number = 1
    while len(out) < n:
        if (name := f"Person {number}") not in used:
            out.append(name)
        number += 1
    return out


def write_pairs(folder: Path, pairs: list[tuple[str, str]]) -> str | None:
    """Schreibt die Liste (Schlüssel bleibt erhalten) und liefert ihre Prüfsumme; leere Liste:
    keine Datei, None. Die bisherige Fassung kommt nach pseudonyme_alt/."""
    path = folder / PSEUDONYM_FILE
    previous = file_sha(path)
    key = _key(path, folder) or secrets.token_hex(16)
    rows = [f"{_KEY_LINE}{key}", _HEADER, *(f"{o}\t{r}" for o, r in pairs)]
    text = "\n".join(rows) + "\n" if pairs else None
    if previous is not None and (text is None or sha256_text(text) != previous):
        (folder / HISTORY_DIR).mkdir(exist_ok=True)
        path.replace(folder / HISTORY_DIR / f"{previous[:12]}.tsv")
    if text is None:
        return None
    path.write_text(text, encoding="utf-8", newline="\n")
    return sha256_text(text)


def is_list_file(path: Path) -> bool:
    try:
        with path.open(encoding="utf-8") as handle:
            return handle.readline().startswith(_KEY_LINE)
    except (OSError, UnicodeDecodeError):
        return False


def read_pairs(path: Path) -> list[tuple[str, str]]:
    """Schlüssel und Kopf stehen in Zeile 1 und 2; alles danach sind Einträge."""
    rows = _read(path).splitlines()
    pairs = []
    for row in rows[2:] if rows and rows[0].startswith(_KEY_LINE) else rows:
        if row.strip():
            original, _, replacement = row.partition("\t")
            pairs.append((original, replacement))
    return pairs


def file_sha(path: Path) -> str | None:
    return sha256_text(_read(path)) if path.is_file() else None


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"{path.name} ist nicht als UTF-8 lesbar (etwa in Excel gespeichert). Die Liste im "
            "Tab Prompt & Einheiten neu anlegen."
        ) from exc


def _key(path: Path, folder: Path) -> str | None:
    """Schlüssel der aktuellen oder, nach dem Leeren, der zuletzt abgelegten Fassung."""
    candidates = [path] if path.is_file() else []
    history = folder / HISTORY_DIR
    if history.is_dir():
        candidates += sorted(history.glob("*.tsv"), key=lambda p: p.stat().st_mtime, reverse=True)
    for candidate in candidates:
        first = _read(candidate).partition("\n")[0]
        if first.startswith(_KEY_LINE):
            return first.removeprefix(_KEY_LINE)
    return None
