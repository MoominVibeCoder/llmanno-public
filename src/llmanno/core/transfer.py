"""Validiertes Instrument auf neue Daten übertragen (neue Erhebungswelle).

Übernommen werden Codebuch (wörtlich), Code-Liste samt Gruppen, Prompt- und Kodierparameter,
Panel, Anbieter, Review-Regeln und Gold-Zuordnung; nicht Split, Freeze, Läufe und Reviews.
Das Protokoll hält den Fingerabdruck des Instruments ohne Daten fest, damit der Bericht sagen
kann, ob es unverändert blieb.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from llmanno.core import data_import
from llmanno.core.engine import instrument_fingerprint
from llmanno.core.i18n import t
from llmanno.core.project import CODEBOOK_FILE, Project
from llmanno.core.pseudonyms import PSEUDONYM_FILE
from llmanno.core.validation import freeze_intact

INSTRUMENT_TRANSFERRED = "instrument_transferred"


def transfer_instrument(
    source: Project, folder: Path, data: Path, *, name: str | None = None
) -> Project:
    cfg = source.config
    if cfg.data is None or cfg.codebook is None or source.codebook_text is None:
        raise ValueError("Das Quellprojekt hat noch keine Daten oder kein Codebuch.")
    sheets = data_import.sheet_names(data)
    sheet = cfg.data.sheet if cfg.data.sheet in sheets else sheets[0]
    header = set(data_import.read_sheet(data, sheet).header)
    target = cfg.coding.units.target_column
    if target and target not in header:
        raise ValueError(
            f"Die neuen Daten haben die Spalte „{target}“ nicht, die festlegt, welche Zeilen "
            "kodiert werden (Tab Prompt & Einheiten)."
        )
    # Gold und Analyse-Variablen sind optional; alles, was in den Prompt geht, muss da sein.
    mapping = cfg.data.mapping.model_copy(
        update={
            "gold": [c for c in cfg.data.mapping.gold if c in header],
            "consensus": cfg.data.mapping.consensus
            if cfg.data.mapping.consensus in header
            else None,
            "analysis": [c for c in cfg.data.mapping.analysis if c in header],
        }
    )
    project = Project.create(folder, name=name or f"{cfg.name} (neue Daten)")
    try:
        project.import_data(data, sheet, mapping)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / CODEBOOK_FILE
            path.write_text(source.codebook_text, encoding="utf-8", newline="\n")
            project.load_codebook(path)
        assert project.config.codebook is not None
        names = source.folder / PSEUDONYM_FILE
        if cfg.coding.pseudonyms_sha256 and names.is_file():  # Klasse bleibt oft dieselbe
            shutil.copyfile(names, project.folder / PSEUDONYM_FILE)
        kept = {"source_name", "encoding", "sha256"}
        codebook = cfg.codebook.model_copy(
            update={key: getattr(project.config.codebook, key) for key in kept}
        )
        project.update(
            prompt_language=cfg.prompt_language,
            mode=cfg.mode,
            k=cfg.k,
            codebook=codebook,
            # Beispiele sind Einheiten der alten Daten; der Bericht nennt das Instrument dann
            # als geändert.
            coding=cfg.coding.model_copy(update={"examples": []}),
            gold=cfg.gold,
            review=cfg.review,
            providers=cfg.providers,
            panel=cfg.panel,
        )
        project.save()
        project.store.log(
            INSTRUMENT_TRANSFERRED,
            source=cfg.name,
            source_folder=str(source.folder),
            instrument=instrument_fingerprint(source),
            source_instrument_hash=cfg.freeze.instrument_hash if cfg.freeze else None,
            source_freeze_intact=freeze_intact(source) if cfg.freeze else None,
            examples_dropped=len(cfg.coding.examples),
        )
    except BaseException:
        project.close()
        raise
    return project


def transfer_note(project: Project) -> str | None:
    """Satz für den Bericht: woher das Instrument stammt und ob es unverändert ist."""
    entry = next((e for e in project.store.audit() if e.event == INSTRUMENT_TRANSFERRED), None)
    if entry is None:
        return None
    frozen = entry.details.get("source_instrument_hash")
    source = entry.details.get("source", "?")
    origin = t(
        f"Instrument aus dem Projekt „{source}“ übernommen",
        f"Instrument taken over from the project “{source}”",
    )
    if frozen and entry.details.get("source_freeze_intact") is False:
        origin += t(
            f" (dort eingefroren mit Instrument-Hash {frozen}, der Freeze war aber gebrochen)",
            f" (frozen there with instrument hash {frozen}, but the freeze was broken)",
        )
    elif frozen:
        origin += t(
            f" (dort eingefroren, Instrument-Hash {frozen})",
            f" (frozen there, instrument hash {frozen})",
        )
    else:
        origin += t(" (dort nicht eingefroren)", " (not frozen there)")
    same = entry.details.get("instrument") == instrument_fingerprint(project)
    note = origin + (
        t("; seither unverändert.", "; unchanged since.")
        if same
        else t(
            "; seither geändert (Codebuch, Prompt, Parameter oder Panel).",
            "; changed since (codebook, prompt, parameters or panel).",
        )
    )
    if dropped := entry.details.get("examples_dropped"):
        note += t(
            f" Die {dropped} Beispiele aus Dev der Quelle gehören zu deren Daten und wurden "
            "nicht übernommen.",
            f" The {dropped} dev examples of the source belong to its data and were not "
            "taken over.",
        )
    return note
