"""Referenzkodierungen (Gold) aus den Daten lesen und den Codes des Codebuchs zuordnen.

Nicht zuordenbare Rohwerte werden gezählt und angezeigt statt still verworfen; die Zuordnung
lässt sich in project.json (gold.value_map) festlegen.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Hashable
from dataclasses import dataclass, field

from llmanno.core.codebook import normalize_id
from llmanno.core.config import ProjectConfig
from llmanno.core.store import Unit
from llmanno.core.units import Item

Label = Hashable  # Single-Label: Code-Kürzel; Multi-Label: frozenset von Kürzeln


@dataclass(frozen=True)
class GoldTable:
    coders: list[str]  # einzelne Kodierende (ohne Konsens)
    consensus: str | None  # Konsens-Spalte, falls zugeordnet
    labels: dict[str, dict[str, Label | None]]  # Spalte → item_id → Label
    unmapped: Counter[str] = field(default_factory=Counter)
    inconsistent: list[str] = field(default_factory=list)  # holistisch: Gruppe uneinheitlich

    @property
    def columns(self) -> list[str]:
        return [*self.coders, self.consensus] if self.consensus else list(self.coders)


def gold_columns(config: ProjectConfig) -> list[str]:
    mapping = config.data.mapping if config.data else None
    if mapping is None:
        return []
    return [*mapping.gold, *([mapping.consensus] if mapping.consensus else [])]


def gold_parts(raw: object, config: ProjectConfig) -> list[str]:
    """Die Teile eines Zellwerts, wie map_cell sie liest (Multi-Label: am Trennzeichen)."""
    if raw is None:
        return []
    text = str(raw)
    parts = [text] if config.mode == "single" else text.split(config.gold.separator)
    return [p.strip() for p in parts if p.strip()]


def gold_values(config: ProjectConfig, units: list[Unit]) -> tuple[Counter[str], Counter[str]]:
    """Alle Rohwerte der Gold-Spalten und die davon nicht zuordenbaren, je Teil gezählt."""
    found: Counter[str] = Counter()
    unmapped: Counter[str] = Counter()
    for unit in units:
        for column in gold_columns(config):
            raw = unit.gold.get(column)
            found.update(gold_parts(raw, config))
            map_cell(raw, config, unmapped)
    return found, unmapped


def map_cell(raw: object, config: ProjectConfig, unmapped: Counter[str]) -> Label | None:
    """Ein Zellwert → Code bzw. Code-Menge; None = fehlt oder nicht zuordenbar."""
    parts = gold_parts(raw, config)
    if not parts:
        return None
    codebook = config.codebook
    assert codebook is not None
    allowed = codebook.allowed_ids()
    lower = {code.casefold(): code for code in allowed}
    value_map = config.gold.value_map

    def one(part: str) -> str | None:
        if part in value_map:
            return value_map[part]
        for candidate in (part, normalize_id(part)):
            if candidate in allowed:
                return candidate
            if candidate.casefold() in lower:
                return lower[candidate.casefold()]
        unmapped[part] += 1
        return None

    if config.mode == "single":
        return one(parts[0])
    codes = {c for part in parts if (c := one(part)) is not None}
    return frozenset(codes) if codes else None


def build_gold(config: ProjectConfig, units: list[Unit], items: list[Item]) -> GoldTable:
    data = config.data
    if data is None:
        raise ValueError("Es sind noch keine Daten importiert (Tab Daten).")
    mapping = data.mapping
    columns = gold_columns(config)
    unmapped: Counter[str] = Counter()
    by_unit = {
        u.unit_id: {c: map_cell(u.gold.get(c), config, unmapped) for c in columns} for u in units
    }

    labels: dict[str, dict[str, Label | None]] = {c: {} for c in columns}
    inconsistent: list[str] = []
    for item in items:
        for column in columns:
            values = {by_unit[line.unit_id][column] for line in item.lines} - {None}
            if len(values) > 1:
                # Holistisch kodierte Gruppe mit uneinheitlichem Gold: nicht auswertbar.
                if item.item_id not in inconsistent:
                    inconsistent.append(item.item_id)
                labels[column][item.item_id] = None
            else:
                labels[column][item.item_id] = next(iter(values), None)
    return GoldTable(list(mapping.gold), mapping.consensus, labels, unmapped, inconsistent)
