"""Mehrere Codesysteme je Projekt (Punkt 20), etwa Reference Move als eigene Dimension.

Jedes weitere Codesystem ist ein vollständiges Projekt im Unterordner `codesysteme/<Name>` des
Hauptprojekts. Es kodiert mit eigenem Codebuch in eigenen Aufrufen, die Messung ist also
dieselbe wie in einem Projekt mit nur diesem Codesystem. Gold-Spalten, Modus (ein oder mehrere
Codes), Prompt-Parameter, Ergebnisse, Reviews, Split und Freeze hat es für sich. Vom
Hauptprojekt übernimmt es die Daten, Anbieter und Panel, die Prompt-Sprache, die Einheiten
und die Namensliste; `sync` gleicht das vor jedem Lauf und beim Öffnen ab.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
from pathlib import Path
from typing import Literal

from llmanno.core import data_import
from llmanno.core.lock import ProjectLocked
from llmanno.core.project import CONFIG_FILE, STORE_FILE, Project
from llmanno.core.pseudonyms import HISTORY_DIR, PSEUDONYM_FILE, file_sha
from llmanno.core.store import Store

SUBDIR = "codesysteme"
CODESYSTEM_ADDED = "codesystem_added"
_NAME = re.compile(r"^\w[\w \-.]{0,38}\w$|^\w$")
# Windows legt Ordner mit diesen Namen nicht an.
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10))}
_RESERVED |= {f"lpt{i}" for i in range(1, 10)}


def names(main: Project) -> list[str]:
    folder = main.folder / SUBDIR
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.iterdir() if (p / CONFIG_FILE).is_file())


def is_codesystem(project: Project) -> bool:
    """Liegt das Projekt als weiteres Codesystem in einem Hauptprojekt?"""
    parent = project.folder.parent
    return parent.name == SUBDIR and (parent.parent / CONFIG_FILE).is_file()


def main_split(project: Project) -> dict[str, str] | None:
    """Split des Hauptprojekts, wenn `project` ein weiteres Codesystem ist."""
    if not is_codesystem(project):
        return None
    store = Store.open(project.folder.parent.parent / STORE_FILE)
    try:
        return store.split() or None
    finally:
        store.close()


def open_all(main: Project) -> tuple[dict[str, Project], list[str]]:
    """Weitere Codesysteme öffnen und abgleichen. Liefert die geöffneten und Hinweise; ein
    gesperrtes oder beschädigtes Codesystem fehlt dann, die übrigen nicht."""
    opened: dict[str, Project] = {}
    notes: list[str] = []
    for name in names(main):
        try:
            project = Project.open(main.folder / SUBDIR / name)
        except (OSError, ValueError, sqlite3.Error, ProjectLocked) as exc:
            notes.append(f"Codesystem „{name}“ nicht geöffnet: {exc}")
            continue
        try:
            notes += sync(main, project)
        except (OSError, ValueError, sqlite3.Error) as exc:
            notes.append(f"Codesystem „{name}“ nicht abgeglichen: {exc}")
        opened[name] = project
    return opened, notes


def check_name(main: Project, name: str) -> str:
    name = " ".join(name.split())
    if not _NAME.match(name) or name.casefold() in _RESERVED:
        raise ValueError(
            "Der Name braucht 1–40 Zeichen: Buchstaben, Ziffern, Leerzeichen, - _ oder Punkt, "
            "am Ende kein Punkt; Gerätenamen wie NUL gehen nicht."
        )
    if name.casefold() in {n.casefold() for n in names(main)}:
        raise ValueError(f"Ein Codesystem „{name}“ gibt es schon.")
    if name.casefold() == main.config.name.casefold():
        raise ValueError("Das Codesystem braucht einen anderen Namen als das Hauptprojekt.")
    return name


def create(
    main: Project,
    name: str,
    codebook: Path,
    gold: list[str],
    consensus: str | None = None,
    *,
    mode: Literal["single", "multi"] | None = None,
    k: int | None = None,
) -> Project:
    """Neues Codesystem mit eigenem Codebuch und eigenen Gold-Spalten aus den Daten des
    Hauptprojekts. Prompt-Parameter, Split- und Review-Regeln starten wie im Hauptprojekt;
    Modus und k ohne Angabe ebenso."""
    name = check_name(main, name)
    cfg = main.config
    if cfg.data is None or main.data_path is None or not main.data_path.is_file():
        raise ValueError(
            "Erst Daten importieren (Tab Daten); das Codesystem kodiert dieselben Einheiten."
        )
    if is_codesystem(main):
        raise ValueError("Weitere Codesysteme gehören ins Hauptprojekt.")
    sub = Project.create(main.folder / SUBDIR / name, name=name)
    try:
        mapping = cfg.data.mapping.model_copy(update={"gold": gold, "consensus": consensus})
        sub.import_data(main.data_path, cfg.data.sheet, mapping)
        sub.load_codebook(codebook)
        sub.update(
            mode=mode or cfg.mode,
            k=k or cfg.k,
            coding=cfg.coding.model_copy(update={"examples": []}),  # Beispiele: eigenes Gold
            gold=cfg.gold.model_copy(update={"value_map": {}}),  # Rohwerte gehören zum Codebuch
            split=cfg.split,
            # Die Gate-Abbildung ist auf das Codebuch des Hauptprojekts gelernt.
            review=cfg.review.model_copy(update={"calibration": None}),
        )
        _source_name(main, sub)
        sync(main, sub)
    except BaseException:
        sub.close()
        shutil.rmtree(sub.folder, ignore_errors=True)
        raise
    main.store.log(CODESYSTEM_ADDED, name=name, gold=gold, consensus=consensus)
    return sub


def sync(main: Project, sub: Project, *, save: bool = True) -> list[str]:
    """Übernimmt das Gemeinsame aus dem Hauptprojekt; liefert Hinweise, wo das nicht ging.
    `save=False` (Wechsel im Fenster): nur im Speicher, gespeichert wird mit „Speichern“ oder
    beim Laufstart. Daten und Namensliste liegen als Dateien vor und gelten sofort."""
    notes: list[str] = []
    cfg = main.config
    _sync_data(main, sub, notes)
    _sync_names(main, sub)
    coding = sub.config.coding.model_copy(
        update={"units": cfg.coding.units, "pseudonyms_sha256": cfg.coding.pseudonyms_sha256}
    )
    old = sub.config.coding.units
    if coding.examples and (old.mode, old.target_column) != (
        coding.units.mode,
        coding.units.target_column,
    ):
        coding = coding.model_copy(update={"examples": []})
        notes.append(
            f"Codesystem „{sub.config.name}“: Beispiele aus Dev entfernt, weil sich die "
            "Einheiten des Hauptprojekts geändert haben; neu ziehen."
        )
    sub.update(
        prompt_language=cfg.prompt_language,
        providers=cfg.providers,
        panel=cfg.panel,
        coding=coding,
    )
    if save:
        sub.save()
    return notes


def _sync_names(main: Project, sub: Project) -> None:
    source, target = main.folder / PSEUDONYM_FILE, sub.folder / PSEUDONYM_FILE
    wanted = main.config.coding.pseudonyms_sha256 if source.is_file() else None
    current = file_sha(target) if target.is_file() else None
    if current == wanted:
        return
    if current is not None:  # frühere Fassung aufheben, für den Rerun älterer Pakete
        (sub.folder / HISTORY_DIR).mkdir(exist_ok=True)
        target.replace(sub.folder / HISTORY_DIR / f"{current[:12]}.tsv")
    if wanted is not None:
        shutil.copyfile(source, target)


def _source_name(main: Project, sub: Project) -> None:
    """Originaler Dateiname wie im Hauptprojekt (für Paket-README und Rerun-Hinweis)."""
    data, own = main.config.data, sub.config.data
    if data is not None and own is not None and own.source_name != data.source_name:
        sub.update(data=own.model_copy(update={"source_name": data.source_name}))
        sub.save()


def _sync_data(main: Project, sub: Project, notes: list[str]) -> None:
    data, own = main.config.data, sub.config.data
    if data is None or own is None or main.data_path is None:
        return
    if own.file_sha256 == data.file_sha256 and own.units_sha256 == data.units_sha256:
        return
    name = sub.config.name
    sheet = set(data_import.read_sheet(main.data_path, data.sheet).header)
    missing = [c for c in (*own.mapping.gold, own.mapping.consensus) if c and c not in sheet]
    if missing:
        notes.append(
            f"Codesystem „{name}“: Den Daten des Hauptprojekts fehlen seine Gold-Spalten "
            f"({', '.join(missing)}); es behält den bisherigen Stand."
        )
        return
    if own.units_sha256 == data.units_sha256:  # nur Gold oder Datei geändert
        sub.update_gold(
            main.data_path,
            data.sheet,
            own.mapping.gold,
            own.mapping.consensus,
            data.mapping.analysis,
        )
    elif sub.store.count_calls() == 0:
        mapping = data.mapping.model_copy(
            update={"gold": own.mapping.gold, "consensus": own.mapping.consensus}
        )
        had_split = bool(sub.store.split())
        sub.import_data(main.data_path, data.sheet, mapping)
        if had_split:
            notes.append(
                f"Codesystem „{name}“: neue Daten übernommen; seinen Split bitte neu anlegen."
            )
    else:
        notes.append(
            f"Codesystem „{name}“: Die Daten des Hauptprojekts haben sich geändert, es hat aber "
            "schon Läufe. Es kodiert weiter die alten Daten."
        )
        return
    _source_name(main, sub)
