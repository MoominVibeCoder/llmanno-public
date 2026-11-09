"""Stichprobenrechner für die Gold-Größe: Wie viele Test-Einheiten braucht ein κ-Intervall?

Großstichproben-Varianz von Cohens κ nach Fleiss, Cohen & Everitt (1969) für die Tabelle, die das
erwartete κ und die Code-Verteilung festlegen (LLM und Gold mit derselben Verteilung). Gilt für
unabhängige Einheiten; hängen Einheiten über Gruppen zusammen, braucht es mehr.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from scipy import stats


def kappa_table(shares: Sequence[float], kappa: float) -> np.ndarray:
    """Gemeinsame Verteilung zweier Kodierungen mit gleichen Rändern und dem gegebenen κ."""
    p = np.asarray(shares, dtype=float)
    p = p / p.sum()
    return (1 - kappa) * np.outer(p, p) + kappa * np.diag(p)


def kappa_variance(table: np.ndarray) -> float:
    """n · Var(κ̂) für eine Tabelle von Anteilen (Fleiss, Cohen & Everitt 1969)."""
    t = np.asarray(table, dtype=float)
    t = t / t.sum()
    row, col = t.sum(axis=1), t.sum(axis=0)
    p_o, p_e = float(np.trace(t)), float(row @ col)
    agree = np.sum(np.diag(t) * ((1 - p_e) - (col + row) * (1 - p_o)) ** 2)
    weighted = t * np.add.outer(col, row) ** 2  # p_ij (p_.i + p_j.)²
    disagree = (1 - p_o) ** 2 * (weighted.sum() - np.trace(weighted))
    return float((agree + disagree - (p_o * p_e - 2 * p_e + p_o) ** 2) / (1 - p_e) ** 4)


def _z(level: float) -> float:
    return float(stats.norm.ppf(0.5 + level / 2))


MIN_UNITS = 30  # darunter trägt die Großstichproben-Näherung nicht
# Erwartete Einheiten außerhalb der häufigsten Kategorie; weniger, und κ ist oft undefiniert.
MIN_OUTSIDE = 10


def units_needed(
    shares: Sequence[float], kappa: float, half_width: float, level: float = 0.95
) -> int:
    """Test-Einheiten, damit das Intervall um κ im Mittel etwa ±half_width breit ist (keine
    Obergrenze); mindestens MIN_UNITS und MIN_OUTSIDE außerhalb der häufigsten Kategorie."""
    variance = kappa_variance(kappa_table(shares, kappa))
    p = np.asarray(shares, dtype=float) / sum(shares)
    floor = max(MIN_UNITS, math.ceil(MIN_OUTSIDE / (1 - p.max())))
    return max(math.ceil(_z(level) ** 2 * variance / half_width**2), floor)


def expected_half_width(
    shares: Sequence[float], kappa: float, n: int, level: float = 0.95
) -> float:
    """Erwartete halbe Intervallbreite um κ bei n Test-Einheiten."""
    return _z(level) * math.sqrt(kappa_variance(kappa_table(shares, kappa)) / n)
