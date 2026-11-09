"""Projekt = Ordner mit project.json (Konfiguration) und store.sqlite (Zustand, Audit-Log)."""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from llmanno.core import codebook, data_import, lock, pseudonyms
from llmanno.core.config import (
    CodebookConfig,
    ColumnMapping,
    DataConfig,
    ProjectConfig,
    read_config,
)
from llmanno.core.store import Store, Unit, sha256_text
from llmanno.core.units import is_target

CONFIG_FILE = "project.json"
STORE_FILE = "store.sqlite"
CODEBOOK_FILE = "codebook.txt"
DATA_STEM = "data"
GOLD_UPDATED = "gold_updated"


class Project:
    def __init__(self, folder: Path, config: ProjectConfig, store: Store) -> None:
        self.folder = folder
        self.config = config
        self.store = store
        self._saved = config.model_dump(mode="json")
        self._locked = True  # von create/open gesetzt, close gibt die Sperre frei

    @classmethod
    def create(cls, folder: Path, name: str) -> Project:
        config = ProjectConfig(name=name)
        if (folder / CONFIG_FILE).is_file():
            raise FileExistsError(
                f"In {folder} liegt schon ein Projekt. Mit „Projekt öffnen…“ öffnen oder für ein "
                "neues Projekt einen leeren Ordner wählen."
            )
        if folder.exists() and any(folder.iterdir()):
            raise FileExistsError(
                f"Der Ordner {folder} ist nicht leer. Für ein neues Projekt einen leeren Ordner "
                "wählen oder im Auswahldialog einen neuen anlegen."
            )
        folder.mkdir(parents=True, exist_ok=True)
        lock.acquire(folder)
        try:
            project = cls(folder, config, Store.create(folder / STORE_FILE))
        except BaseException:
            lock.release(folder)
            raise
        try:
            project._write_config()
            project.store.log("project_created", config=project._saved)
        except BaseException:
            project.close()
            raise
        return project

    @classmethod
    def open(cls, folder: Path, *, force: bool = False) -> Project:
        """`force` hebt die Sperre eines anderen Programms auf (nach einem Absturz)."""
        config_path = folder / CONFIG_FILE
        if not config_path.is_file():
            raise FileNotFoundError(f"Kein Projekt in {folder} ({CONFIG_FILE} fehlt)")
        config = read_config(config_path.read_text(encoding="utf-8"), CONFIG_FILE)
        lock.acquire(folder, force=force)
        try:
            return cls(folder, config, Store.open(folder / STORE_FILE))
        except BaseException:
            lock.release(folder)
            raise

    @property
    def dirty(self) -> bool:
        return self.config.model_dump(mode="json") != self._saved

    def update(self, **changes: Any) -> None:
        """Ändert die Konfiguration im Speicher, validiert, speichert aber noch nicht."""
        self.config = ProjectConfig.model_validate({**self.config.model_dump(), **changes})

    def save(self) -> None:
        current = self.config.model_dump(mode="json")
        changes = {k: [self._saved[k], v] for k, v in current.items() if self._saved[k] != v}
        if not changes:
            return
        self._write_config()
        self.store.log("config_changed", changes=changes)
        self._saved = current

    def close(self) -> None:
        self.store.close()
        if self._locked:
            self._locked = False
            lock.release(self.folder)

    # --- Daten ---------------------------------------------------------------------------------

    @property
    def data_path(self) -> Path | None:
        if self.config.data is None:
            return None
        return self.folder / f"{DATA_STEM}{Path(self.config.data.source_name).suffix.lower()}"

    def import_data(
        self, path: Path, sheet: str, mapping: ColumnMapping
    ) -> data_import.ImportReport:
        """Liest die Daten, kopiert die Quelldatei ins Projekt und ersetzt alle Einheiten."""
        parsed = data_import.read_sheet(path, sheet)
        report = data_import.import_units(parsed, mapping)
        if not report.units:
            raise ValueError(
                "Keine gültige Einheit gefunden; nichts importiert. Meist ist eine falsche Text- "
                "oder ID-Spalte gewählt (leere Texte, doppelte IDs)."
            )
        self.store.replace_units(report.units)

        target = self.folder / f"{DATA_STEM}{path.suffix.lower()}"
        if path.resolve() != target.resolve():
            shutil.copyfile(path, target)
        self.update(
            data=DataConfig(
                source_name=path.name,
                sheet=parsed.name,
                mapping=mapping,
                file_sha256=data_import.file_sha256(target),
                units_sha256=data_import.units_sha256(report.units),
                n_units=len(report.units),
            )
        )
        self.save()
        self.store.log(
            "data_imported",
            source=path.name,
            sheet=parsed.name,
            n_units=len(report.units),
            n_skipped=report.n_skipped,
            n_warnings=sum(1 for i in report.issues if i.severity == "warning"),
            file_sha256=self.config.data.file_sha256 if self.config.data else None,
        )
        return report

    def update_gold(
        self,
        path: Path,
        sheet: str,
        gold: list[str],
        consensus: str | None,
        analysis: list[str] | None = None,
    ) -> data_import.ImportReport:
        """Gold nachtragen oder korrigieren, auch nach Kodierläufen und nach dem Freeze.

        Die Datei muss dieselben Einheiten in denselben Zeilen mit denselben Texten, Gruppen,
        Reihenfolgen und Metadaten enthalten; nur Gold darf sich ändern. Gold gehört nicht
        zum Instrument: Lauf-Identitäten und Freeze bleiben, die Änderung steht im Protokoll.
        Die bisherige Datenkopie bleibt als Sicherung im Projektordner."""
        data = self.config.data
        if data is None:
            raise ValueError("Es sind noch keine Daten importiert (Tab Daten).")
        parsed = data_import.read_sheet(path, sheet)
        chosen = data.mapping.analysis if analysis is None else analysis
        mapping = data.mapping.model_copy(
            update={
                "gold": gold,
                "consensus": consensus,
                "analysis": [c for c in chosen if c in parsed.header],
            }
        )
        optional = (*data.mapping.gold, data.mapping.consensus, *data.mapping.analysis)
        missing = [c for c in data.mapping.columns() if c not in parsed.header]
        missing = [c for c in missing if c not in optional]
        target = self.config.coding.units.target_column
        if target and target not in parsed.header:
            missing.append(target)
        if missing:
            raise ValueError(
                "Gold lässt sich nur in derselben Datei nachtragen; es fehlen die Spalten "
                + ", ".join(f"„{c}“" for c in missing)
                + "."
            )
        report = data_import.import_units(parsed, mapping)
        old = {u.unit_id: u for u in self.store.units()}
        new = {u.unit_id: u for u in report.units}

        def content(u: Unit) -> tuple:  # units_sha256 und die Auswahl „kodieren ja/nein“
            return (u.row, u.group, u.order, u.speaker, u.text, u.meta, is_target(u, target))

        differing = sorted(
            {i for i in old if i not in new or content(new[i]) != content(old[i])}
            | (new.keys() - old.keys())
        )
        if differing:
            raise ValueError(
                f"{len(differing)} Einheiten passen nicht zu den importierten (z. B. "
                f"„{differing[0]}“): Zeile, Text, Gruppe, Reihenfolge oder Metadaten weichen ab "
                "oder die Einheit fehlt. Gold lässt sich nur nachtragen, wenn die Texte gleich "
                "bleiben und die Zeilen nicht umsortiert sind."
            )
        changed = sum(new[i].gold != old[i].gold for i in old)
        before = [*data.mapping.gold, *([data.mapping.consensus] if data.mapping.consensus else [])]
        dropped = [c for c in before if c not in (*gold, consensus)]
        target = self.folder / f"{DATA_STEM}{path.suffix.lower()}"
        backup = None
        if path.resolve() != target.resolve():
            # Erst die Dateien: Scheitert das (etwa weil Excel sie offen hält), ist nichts
            # geändert. Die Kopie muss zur Zuordnung passen (Rerun, Paket).
            previous = self.data_path
            if previous is not None and previous.is_file():
                stamp = time.strftime("%Y%m%d-%H%M%S")
                backup = previous.with_name(f"{DATA_STEM}.bis-{stamp}{previous.suffix}")
                shutil.copyfile(previous, backup)
            shutil.copyfile(path, target)
        self.store.update_gold({i: (new[i].gold, new[i].source) for i in old})
        self.update(
            data=data.model_copy(
                update={
                    "mapping": mapping,
                    "source_name": path.name,
                    "sheet": parsed.name,
                    "file_sha256": data_import.file_sha256(target),
                    "content_sha256": data_import.units_sha256(list(old.values())),
                }
            )
        )
        self.save()
        self.store.log(
            GOLD_UPDATED,
            source=path.name,
            sheet=parsed.name,
            gold=gold,
            consensus=consensus,
            n_changed=changed,
            dropped=dropped,
            backup=backup.name if backup else None,
            file_sha256=data_import.file_sha256(target),
        )
        return report

    # --- Codebuch ------------------------------------------------------------------------------

    @property
    def codebook_text(self) -> str | None:
        path = self.folder / CODEBOOK_FILE
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def load_codebook(self, path: Path) -> None:
        """Übernimmt ein Codebuch verbatim. Eine schon vorhandene Code-Liste bleibt erhalten."""
        loaded = codebook.load_codebook(path)
        self.store.put_text(loaded.text)  # jede Fassung bleibt abrufbar (core.history)
        (self.folder / CODEBOOK_FILE).write_text(loaded.text, encoding="utf-8", newline="\n")
        previous = self.config.codebook
        fields = previous.model_dump() if previous else {}
        fields.update(
            source_name=path.name, sha256=sha256_text(loaded.text), encoding=loaded.encoding
        )
        self.update(codebook=CodebookConfig.model_validate(fields))
        self.save()
        self.store.log(
            "codebook_loaded",
            source=path.name,
            sha256=sha256_text(loaded.text),
            encoding=loaded.encoding,
            n_chars=len(loaded.text),
        )

    # --- Pseudonymisierung -----------------------------------------------------------------

    @property
    def pseudonym_pairs(self) -> list[tuple[str, str]]:
        path = self.folder / pseudonyms.PSEUDONYM_FILE
        return pseudonyms.read_pairs(path) if path.is_file() else []

    def set_pseudonyms(self, pairs: list[tuple[str, str]]) -> None:
        """Liste speichern; leer schaltet die Ersetzung ab. Ändert die Lauf-Identität."""
        cleaned = pseudonyms.check_pairs(pairs)
        sha = pseudonyms.write_pairs(self.folder, cleaned)
        self.update(coding=self.config.coding.model_copy(update={"pseudonyms_sha256": sha}))
        self.save()
        self.store.log("pseudonyms_set", n_entries=len(cleaned))  # Namen nie ins Protokoll

    def pseudonymizer_ready(self) -> bool:
        """Passt die Liste im Ordner zur Prüfsumme der Konfiguration?"""
        try:
            self.pseudonymizer()
        except ValueError:
            return False
        return True

    def pseudonymizer(self) -> pseudonyms.Pseudonymizer | None:
        """Die Ersetzung für den Prompt. Fehlt die Liste oder wurde sie außerhalb geändert,
        bricht das ab: Sonst gingen die Namen unbemerkt an den Anbieter."""
        expected = self.config.coding.pseudonyms_sha256
        if expected is None:
            return None
        path = self.folder / pseudonyms.PSEUDONYM_FILE
        if pseudonyms.file_sha(path) != expected:
            raise ValueError(
                f"Die Namensliste ({pseudonyms.PSEUDONYM_FILE}) fehlt im Projektordner oder "
                "wurde außerhalb von llmanno geändert. Bitte im Tab Prompt & Einheiten neu "
                "speichern."
            )
        return pseudonyms.Pseudonymizer(tuple(pseudonyms.read_pairs(path)))

    def _write_config(self) -> None:
        # Erst in eine Temp-Datei schreiben, dann ersetzen: ein Absturz hinterlässt nie halbes JSON.
        tmp = self.folder / f"{CONFIG_FILE}.tmp"
        tmp.write_text(self.config.model_dump_json(indent=2), encoding="utf-8")
        replace_file(tmp, self.folder / CONFIG_FILE)


def replace_file(source: Path, target: Path, attempts: int = 20) -> None:
    """os.replace mit Wiederholung: Unter Windows halten Virenscanner oder der Suchindex frisch
    geschriebene Dateien kurz offen, dann scheitert das Ersetzen mit PermissionError."""
    for attempt in range(attempts):
        try:
            source.replace(target)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.05 * (attempt + 1))
