"""Beispiele aus Dev für den Prompt (Few-Shot), standardmäßig aus.

Gezogen werden je Code bis zu n Dev-Einheiten mit Gold (Referenz des Splits), zufällig mit dem
Basis-Seed. Test-Einheiten nie, und ohne Split gar nicht: Die Beispiele kämen sonst aus den
Daten, an denen gemessen wird. Beispiel-Einheiten zählen in keiner Auswertung, denn das Modell
hat ihr Gold gesehen. Die gezogenen IDs und Codes stehen in CodingConfig.examples und gehören
damit zum Instrument.
"""

from __future__ import annotations

import random

from llmanno.core.config import Example, ProjectConfig
from llmanno.core.engine import result_basis
from llmanno.core.gold import build_gold
from llmanno.core.project import Project
from llmanno.core.units import build_items
from llmanno.core.validation import reference_labels

MAX_PER_CODE = 5


def example_ids(cfg: ProjectConfig) -> set[str]:
    return {e.item_id for e in cfg.coding.examples}


def seen_example_ids(project: Project) -> set[str]:
    """Einheiten, deren Gold im Prompt stand oder steht: die Beispiele der Läufe, auf denen die
    Ergebnisse beruhen, und die aktuell eingestellten. Wer nach einem Lauf Beispiele entfernt
    oder neu zieht, holt die gesehenen so nicht zurück in die Kennwerte."""
    runs = set(result_basis(project).runs.items())
    seen = example_ids(project.config)
    for entry in project.store.audit():
        details = entry.details
        if (
            entry.event == "run_started"
            and (details.get("model"), details.get("config_hash")) in runs
        ):
            seen |= {e["item_id"] for e in details.get("coding", {}).get("examples", [])}
    return seen


def draw_examples(project: Project, per_code: int) -> list[Example]:
    """Je Code bis zu `per_code` Beispiele; Mehrfachkodierung: Einheiten mit dem Code in der
    Referenz, eine Einheit nur einmal."""
    cfg = project.config
    assert cfg.codebook is not None
    parts = project.store.split()
    if not parts:
        raise ValueError(
            "Beispiele kommen nur aus dem Übungsteil (Dev): erst im Tab Splits & Freeze eine "
            "Aufteilung erzeugen."
        )
    if not 1 <= per_code <= MAX_PER_CODE:
        raise ValueError(f"Je Code 1 bis {MAX_PER_CODE} Beispiele.")
    items = build_items(project.store.units(), cfg.coding.units)
    dev = [i for i in items if parts.get(i.item_id) == "dev"]
    gold = build_gold(cfg, project.store.units(), dev)
    if not gold.columns:
        raise ValueError("Beispiele brauchen Gold in den Daten: im Tab Daten Gold-Spalten wählen.")
    labels = {
        i: label
        for i, label in reference_labels(project, gold, [i.item_id for i in dev]).items()
        if label is not None
    }
    rng = random.Random(cfg.coding.base_seed)
    chosen: dict[str, list[str]] = {}
    for code in cfg.codebook.allowed_ids():
        hits = sorted(
            i
            for i, label in labels.items()
            if i not in chosen and (code in label if cfg.mode == "multi" else label == code)
        )
        for item_id in rng.sample(hits, min(per_code, len(hits))):
            label = labels[item_id]
            chosen[item_id] = sorted(label) if cfg.mode == "multi" else [label]
    if not chosen:
        raise ValueError("Im Übungsteil (Dev) gibt es keine Einheit mit Gold für einen der Codes.")
    return [Example(item_id=i, codes=codes) for i, codes in chosen.items()]
