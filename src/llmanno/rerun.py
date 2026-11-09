"""Kodierung aus einem Replikationspaket neu rechnen und mit dem Original vergleichen.

    llmanno-rerun replikation.zip --out rerun [--data daten.xlsx] [--pseudonyme pseudonyme.tsv]
        [--consent]

Legt ein neues Projekt an, kodiert mit denselben Einstellungen und Seeds und vergleicht jede
Entscheidung mit dem Original. Gleiche Lauf-Identität (config_hash) heißt: gleiche Konfiguration
und gleicher Modell-Digest. Abweichungen trotz gleicher Identität zeigen, dass das Modell auch
mit Seed nicht deterministisch antwortet.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from llmanno.core.aggregate import aggregate, decision
from llmanno.core.config import ProjectConfig, read_config
from llmanno.core.data_import import units_sha256
from llmanno.core.engine import Engine, Progress, RunAborted, prepare_prompts, results
from llmanno.core.export.replication import ROOT, id_pseudonyms
from llmanno.core.metrics.agreement import cohen_kappa
from llmanno.core.project import CODEBOOK_FILE, CONFIG_FILE, Project
from llmanno.core.providers.base import Provider
from llmanno.core.pseudonyms import PSEUDONYM_FILE, file_sha
from llmanno.core.store import CallRecord


@dataclass(frozen=True)
class ModelRerun:
    key: str
    digest_original: str | None
    digest_rerun: str | None
    same_identity: bool  # gleicher config_hash
    compared: int  # Aufrufe, die in beiden Läufen gültig sind
    identical: int


@dataclass
class RerunReport:
    models: list[ModelRerun] = field(default_factory=list)
    items_compared: int = 0
    items_identical: int = 0
    final_kappa: float | None = None
    warnings: list[str] = field(default_factory=list)

    def markdown(self) -> str:
        lines = ["# Rerun-Vergleich", ""]
        lines += ["| Modell | Digest Original | Digest Rerun | gleiche Identität | gleich |"]
        lines += ["|---|---|---|---|---|"]
        for m in self.models:
            share = f"{m.identical}/{m.compared}" if m.compared else "–"
            lines.append(
                f"| {m.key} | {m.digest_original or '–'} | {m.digest_rerun or '–'} | "
                f"{'ja' if m.same_identity else 'nein'} | {share} |"
            )
        kappa = "–" if self.final_kappa is None else f"{self.final_kappa:.3f}"
        lines += [
            "",
            f"Finale Codes gleich: {self.items_identical} von {self.items_compared} Einheiten "
            f"(Cohens κ {kappa}).",
        ]
        lines += ["", *(f"- {w}" for w in self.warnings)] if self.warnings else []
        return "\n".join(lines) + "\n"


def _read(archive: zipfile.ZipFile, name: str) -> str:
    try:
        return archive.read(f"{ROOT}/{name}").decode("utf-8")
    except KeyError:
        raise ValueError(f"Kein vollständiges Replikationspaket: {name} fehlt.") from None


def _latest_runs(audit: list[dict]) -> dict[str, tuple[str, str | None]]:
    """Modell → (config_hash, Digest) des zuletzt gestarteten Laufs."""
    latest = {}
    for entry in audit:
        if entry["event"] == "run_started":
            latest[entry["model"]] = (entry["config_hash"], entry.get("digest"))
    return latest


def rerun(
    package: Path,
    out: Path,
    *,
    data: Path | None = None,
    name_list: Path | None = None,
    consent: bool = False,
    provider_factory: Callable[..., Provider] | None = None,
    on_progress: Callable[[Progress], None] | None = None,
) -> RerunReport:
    report = RerunReport()
    if not package.is_file():
        raise ValueError(f"{package} nicht gefunden.")
    if not zipfile.is_zipfile(package):
        raise ValueError(f"{package.name} ist kein ZIP-Archiv oder beschädigt.")
    with zipfile.ZipFile(package) as archive:
        original = read_config(_read(archive, f"projekt/{CONFIG_FILE}"), CONFIG_FILE)
        codebook_text = _read(archive, f"projekt/{CODEBOOK_FILE}")
        calls = [json.loads(line) for line in _read(archive, "calls.jsonl").splitlines() if line]
        audit = [json.loads(line) for line in _read(archive, "audit.jsonl").splitlines() if line]
        names = archive.namelist()
        manifest = json.loads(_read(archive, "paket.json")) if f"{ROOT}/paket.json" in names else {}
        packed = [n for n in names if n.startswith(f"{ROOT}/projekt/data.")]
        out.mkdir(parents=True, exist_ok=True)
        if data is None:
            if not packed:
                raise ValueError(
                    "Das Paket enthält keine Rohtexte. Die Originaldaten mit --data angeben."
                )
            data = out / Path(packed[0]).name
            data.write_bytes(archive.read(packed[0]))
        packed_names = f"{ROOT}/projekt/{PSEUDONYM_FILE}"
        if name_list is None and packed_names in archive.namelist():
            name_list = out / PSEUDONYM_FILE
            name_list.write_bytes(archive.read(packed_names))
    assert original.data is not None and original.codebook is not None
    if original.coding.pseudonyms_sha256:
        if name_list is None:
            raise ValueError(
                "Das Original hat Namen vor dem Senden ersetzt. Die Namensliste "
                f"({PSEUDONYM_FILE} aus dem Projektordner) mit --pseudonyme angeben."
            )
        if not name_list.is_file():
            raise ValueError(f"Namensliste {name_list} nicht gefunden.")
        if file_sha(name_list) != original.coding.pseudonyms_sha256:
            raise ValueError(
                "Die Namensliste weicht von der des Originals ab. Frühere Fassungen liegen im "
                "Projektordner unter pseudonyme_alt/. Ein Rerun wäre so nicht vergleichbar."
            )

    codebook = out / CODEBOOK_FILE
    codebook.write_text(codebook_text, encoding="utf-8", newline="\n")
    project = Project.create(out / "projekt", name=f"{original.name} (Rerun)")
    try:
        project.import_data(data, original.data.sheet, original.data.mapping)
        assert project.config.data is not None
        units = project.store.units()
        # Ältere Projekte rechneten die Prüfsumme mit Gold; nach Gold-Nachträgen steht die
        # Prüfsumme ohne Gold daneben. Ohne Reihenfolge-Spalte nahm der Import früher die
        # Zeilennummer als ganze Zahl.
        expected = {original.data.units_sha256, original.data.content_sha256} - {None}
        whole = [replace(u, order=int(u.order)) if u.order.is_integer() else u for u in units]
        found = {units_sha256(v, legacy=old) for v in (units, whole) for old in (False, True)}
        if not expected & found:
            raise ValueError(
                "Die Daten weichen vom Original ab (Einheiten-Hash). Ein Rerun wäre nicht "
                "vergleichbar."
            )
        # Dieselben Einheiten: dieselbe Lauf-Identität wie das Original, wie auch immer es
        # die Prüfsumme rechnete.
        project.update(
            data=project.config.data.model_copy(update={"units_sha256": original.data.units_sha256})
        )
        if project.config.data.file_sha256 != original.data.file_sha256:
            report.warnings.append(
                "Die Datei ist nicht byte-gleich, die Einheiten aber schon (z. B. neu gespeichert)."
            )
        project.load_codebook(codebook)
        assert project.config.codebook is not None
        kept = {"source_name", "encoding", "sha256"}
        codebook_cfg = original.codebook.model_copy(
            update={name: getattr(project.config.codebook, name) for name in kept}
        )
        alias: dict[str, str] = {}
        if manifest.get("ids_pseudonym"):  # Pseudonyme aus den Originaldaten nachrechnen
            holistic = original.coding.units.mode == "holistic"
            alias = id_pseudonyms(project.store.units(), holistic)[2]
        coding = original.coding
        if alias and coding.examples:  # Beispiele tragen im Paket Pseudonyme
            real = {v: k for k, v in alias.items()}
            examples = [e.model_copy(update={"item_id": real[e.item_id]}) for e in coding.examples]
            coding = coding.model_copy(update={"examples": examples})
        project.update(
            prompt_language=original.prompt_language,
            mode=original.mode,
            k=original.k,
            codebook=codebook_cfg,
            coding=coding,
            gold=original.gold,
            split=original.split,
            review=original.review,
            providers=original.providers,
            panel=original.panel,
        )
        if name_list is not None and original.coding.pseudonyms_sha256:
            shutil.copyfile(name_list, project.folder / PSEUDONYM_FILE)
        project.save()
        engine = Engine(project, provider_factory=provider_factory)
        engine.run(remote_consent=consent, on_progress=on_progress)
        _compare(project, original, calls, audit, report, alias)
    finally:
        project.close()
    (out / "rerun_vergleich.md").write_text(report.markdown(), encoding="utf-8")
    return report


def _compare(
    project: Project,
    original: ProjectConfig,
    calls: list[dict],
    audit: list[dict],
    report: RerunReport,
    alias: dict[str, str],
) -> None:
    """`alias`: echte Item-ID → Pseudonym im Paket (leer, wenn das Paket echte IDs trägt)."""
    before = _latest_runs(audit)
    after = _latest_runs([{"event": e.event, **e.details} for e in project.store.audit()])
    for model in original.panel:
        key = model.key
        old_hash, old_digest = before.get(key, (None, None))
        new_hash, new_digest = after.get(key, (None, None))
        old = {
            (c["item_id"], c["run"]): c["decision"]
            for c in calls
            if c["model_key"] == key and c["config_hash"] == old_hash and c["status"] == "ok"
        }
        new = {
            (alias.get(c.item_id, c.item_id), c.run): c.answer
            for c in project.store.calls(model_key=key, config_hash=new_hash, with_raw=False)
            if c.status == "ok" and c.answer
        }
        shared = old.keys() & new.keys()
        identical = sum(1 for k in shared if decision(new[k], original.mode) == old[k])
        report.models.append(
            ModelRerun(key, old_digest, new_digest, old_hash == new_hash, len(shared), identical)
        )
        if old_digest != new_digest:
            report.warnings.append(
                f"{key}: anderer Modell-Digest als im Original; das Modell-Build hat sich geändert."
            )
    _, items = prepare_prompts(project)  # holistisch sind die Einheiten Gruppen
    rerun_results = results(project, [i.item_id for i in items])
    original_final = _original_final(calls, before, original)
    pairs = [
        (original_final[alias.get(i, i)], ";".join(r.final))
        for i, r in rerun_results.items()
        if r.status == "ok" and original_final.get(alias.get(i, i))
    ]
    report.items_compared = len(pairs)
    report.items_identical = sum(1 for a, b in pairs if a == b)
    if pairs:
        report.final_kappa = cohen_kappa([a for a, _ in pairs], [b for _, b in pairs])


def _original_final(
    calls: list[dict], latest: dict[str, tuple[str, str | None]], original: ProjectConfig
) -> dict[str, str]:
    """Finale Codes des Originals, aus den Aufrufen neu aggregiert (wie im Programm)."""
    assert original.codebook is not None
    models = [m.key for m in original.panel]
    by_item: dict[str, list[CallRecord]] = {}
    for c in calls:
        if latest.get(c["model_key"], (None,))[0] != c["config_hash"] or c["status"] != "ok":
            continue
        answer = {"candidates": [{**cand, "evidence": []} for cand in c["candidates"]]}
        record = CallRecord(
            c["item_id"], c["model_key"], c["run"], c["config_hash"], c["seed"], c["system_sha"],
            c["user_sha"], c["status"], answer, [], None, c["flags"], c["request"], c["meta"],
            datetime.fromisoformat(c["ts"]),
        )  # fmt: skip
        by_item.setdefault(c["item_id"], []).append(record)
    abstain = original.codebook.abstain_id if original.codebook.abstain else None
    return {
        item: ";".join(
            aggregate(
                item,
                records,
                models=models,
                mode=original.mode,
                k=original.k,
                n_codes=len(original.codebook.allowed_ids()),
                abstain_id=abstain,
            ).final
        )  # fmt: skip
        for item, records in by_item.items()
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("package", type=Path)
    parser.add_argument("--out", type=Path, required=True, help="leerer Arbeitsordner")
    parser.add_argument("--data", type=Path, help="Originaldaten, falls nicht im Paket")
    parser.add_argument(
        "--pseudonyme", type=Path, help=f"Namensliste ({PSEUDONYM_FILE}), falls nicht im Paket"
    )
    parser.add_argument("--consent", action="store_true", help="Daten dürfen an externe Anbieter")
    args = parser.parse_args(argv)

    def progress(p: Progress) -> None:
        if p.done % 50 == 0 or p.done == p.planned:
            print(f"  {p.done}/{p.planned}", flush=True)

    try:
        report = rerun(
            args.package,
            args.out,
            data=args.data,
            name_list=args.pseudonyme,
            consent=args.consent,
            on_progress=progress,
        )
    except (ValueError, OSError, zipfile.BadZipFile, csv.Error, RunAborted) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1
    print(report.markdown())
    return 0


if __name__ == "__main__":
    sys.exit(main())
