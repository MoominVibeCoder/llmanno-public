"""Analyseeinheiten bilden: isoliert, mit Kontextfenster oder holistisch pro Gruppe."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from llmanno.core.config import UnitSettings
from llmanno.core.store import Unit


@dataclass(frozen=True)
class Line:
    unit_id: str
    speaker: str | None
    text: str


@dataclass(frozen=True)
class Item:
    """Eine Kodiereinheit. `lines` wird kodiert, `before`/`after` sind nur Kontext."""

    item_id: str
    lines: tuple[Line, ...]
    before: tuple[Line, ...] = ()
    after: tuple[Line, ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def target(self) -> str:
        """Der Text, in dem Signalwörter wörtlich stehen müssen."""
        return "\n".join(line.text for line in self.lines)


_NO = {"", "0", "nein", "no", "n", "false", "falsch", "-"}


def is_target(unit: Unit, column: str | None) -> bool:
    """Wird die Zeile kodiert? Ohne Zielspalte immer; sonst nur bei „ja“."""
    if column is None:
        return True
    value = unit.source.get(column)
    return value is not None and value is not False and str(value).strip().casefold() not in _NO


def build_items(units: list[Unit], settings: UnitSettings) -> list[Item]:
    column = settings.target_column
    if settings.mode == "isolated":
        return [Item(u.unit_id, (_line(u),), meta=u.meta) for u in units if is_target(u, column)]

    sequences = group_sequences(units)
    if settings.mode == "holistic":
        if any(u.group is None for u in units):
            raise ValueError(
                "Der holistische Modus braucht eine Gruppen-Spalte, und jede Zeile muss eine "
                "Gruppe haben."
            )
        # Metadaten der ersten Zeile gelten für die ganze Gruppe.
        return [
            Item(group, tuple(_line(u) for u in seq), meta=seq[0].meta)
            for group, seq in sequences.items()
        ]

    items: dict[str, Item] = {}
    for seq in sequences.values():
        for index, unit in enumerate(seq):
            before = seq[max(0, index - settings.n_before) : index]
            after = seq[index + 1 : index + 1 + settings.n_after]
            items[unit.unit_id] = Item(
                unit.unit_id,
                (_line(unit),),
                tuple(_line(u) for u in before),
                tuple(_line(u) for u in after),
                unit.meta,
            )
    # Kontextzeilen bleiben im Fenster, auch wenn sie selbst nicht kodiert werden.
    return [items[u.unit_id] for u in units if is_target(u, column)]


def group_sequences(units: list[Unit]) -> dict[str | None, list[Unit]]:
    """Zeilen je Gruppe in Reihenfolge; Gruppen in der Reihenfolge ihres ersten Auftretens."""
    groups: dict[str | None, list[Unit]] = {}
    for unit in sorted(units, key=lambda u: u.row):
        groups.setdefault(unit.group, []).append(unit)
    return {g: sorted(seq, key=lambda u: (u.order, u.row)) for g, seq in groups.items()}


def _line(unit: Unit) -> Line:
    return Line(unit.unit_id, unit.speaker, unit.text)
