"""Codebuch-Historie: jede geladene Fassung mit Datum, Prüfsumme, Änderung und Läufen.

Die Dev-Schleife (Codebuch schärfen, kodieren, auswerten) soll im Bericht nachvollziehbar
sein; das schützt gegen den Vorwurf, das Instrument sei am Ergebnis ausgerichtet worden. Die
Texte liegen inhaltsadressiert im Store (`texts`), die Abfolge im Audit-Log.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from datetime import datetime

from llmanno.core.project import Project

CODEBOOK_LOADED = "codebook_loaded"


@dataclass(frozen=True)
class CodebookVersion:
    number: int
    loaded: datetime
    source: str
    sha256: str
    text: str | None  # None: vor dem Speichern der Fassungen geladen
    added: int | None  # Zeilen gegenüber der vorigen Fassung
    removed: int | None
    runs: int  # Lauf-Starts, solange diese Fassung galt


def codebook_history(project: Project) -> list[CodebookVersion]:
    audit = project.store.audit()
    loads = [e for e in audit if e.event == CODEBOOK_LOADED]
    versions: list[CodebookVersion] = []
    previous: str | None = None
    for number, entry in enumerate(loads, start=1):
        sha = entry.details.get("sha256", "")
        try:
            text = project.store.get_text(sha)
        except KeyError:
            text = None
        until = loads[number].ts if number < len(loads) else None
        runs = sum(
            1
            for e in audit
            if e.event == "run_started" and e.ts >= entry.ts and (until is None or e.ts < until)
        )
        added = removed = None
        if text is not None and previous is not None:
            diff = list(difflib.ndiff(previous.splitlines(), text.splitlines()))
            added = sum(line.startswith("+ ") for line in diff)
            removed = sum(line.startswith("- ") for line in diff)
        versions.append(
            CodebookVersion(
                number, entry.ts, entry.details.get("source", ""), sha, text, added, removed, runs
            )
        )
        previous = text if text is not None else previous
    return versions


def unified_diff(before: CodebookVersion, after: CodebookVersion) -> str:
    if before.text is None or after.text is None:
        return ""
    return "".join(
        difflib.unified_diff(
            before.text.splitlines(keepends=True),
            after.text.splitlines(keepends=True),
            fromfile=f"v{before.number} {before.source}",
            tofile=f"v{after.number} {after.source}",
        )
    )
