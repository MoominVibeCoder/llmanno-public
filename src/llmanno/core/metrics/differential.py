"""Differentielle Messfehler: hängt die Fehlerrate des LLM von einer Subgruppe ab?

Systematische Fehler je Gruppe verzerren jede nachgelagerte Analyse nach dieser Variable, auch
bei hoher Gesamtgüte (Egami et al. 2023; Ludwig et al. 2024). Getestet wird im linearen
Wahrscheinlichkeitsmodell Fehler ~ Ausprägung mit einem Wald-Test. Die Kovarianz ist
cluster-robust (CR1) über die Gruppen, sonst heteroskedastie-robust (HC1); die F-Verteilung mit
G − 1 bzw. n − k Nennerfreiheitsgraden hält das Niveau auch bei wenigen Clustern besser.

Die Kovarianz nutzt die Residuen unter H0 (gleiche Fehlerrate überall, wie beim Score-Test):
Mit den unrestringierten Residuen wäre sie bei vollständiger Trennung (eine Ausprägung 0 %,
eine andere 100 % Fehler) singulär und überginge gerade den stärksten Effekt. Ohne Cluster und
bei gleich großen Ausprägungen ist das der gepoolte Zwei-Stichproben-Test.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Hashable, Sequence
from dataclasses import dataclass

import numpy as np
from scipy import stats

MIN_LEVEL = 5  # Ausprägungen mit weniger Einheiten bleiben außen vor


@dataclass(frozen=True)
class DifferentialTest:
    variable: str
    levels: dict[str, tuple[int, float]]  # Ausprägung → (n, Fehlerrate)
    dropped: int  # Einheiten in Ausprägungen unter MIN_LEVEL
    statistic: float  # F
    df: tuple[int, int]
    p_value: float
    clustered: bool
    p_holm: float = math.nan


def error_rate_test(
    variable: str,
    errors: Sequence[bool],
    values: Sequence[str],
    clusters: Sequence[Hashable] | None = None,
) -> DifferentialTest | None:
    """None, wenn es weniger als zwei ausreichend große Ausprägungen oder zu wenige Cluster gibt."""
    counts = Counter(values)
    keep = sorted(v for v, n in counts.items() if n >= MIN_LEVEL)
    if len(keep) < 2:
        return None
    rows = [i for i, v in enumerate(values) if v in keep]
    y = np.array([float(errors[i]) for i in rows])
    level_of = [values[i] for i in rows]
    levels = {}
    for v in keep:
        members = [i for i in rows if values[i] == v]
        levels[v] = (len(members), sum(errors[i] for i in members) / len(members))
    dropped = len(values) - len(rows)
    x = np.column_stack(
        [np.ones(len(y))] + [[1.0 if lv == v else 0.0 for lv in level_of] for v in keep[1:]]
    )
    n, k = x.shape
    groups: dict[Hashable, list[int]] = defaultdict(list)
    if clusters is not None:
        for row, i in enumerate(rows):
            groups[clusters[i]].append(row)
        if len(groups) <= k:
            return None  # zu wenige Cluster für die Kovarianz
    denominator = len(groups) - 1 if clusters is not None else n - k
    if y.min() == y.max():  # überall gleiche Fehlerrate (meist: keine Fehler)
        return DifferentialTest(
            variable, levels, dropped, 0.0, (k - 1, denominator), 1.0, clusters is not None
        )

    bread = np.linalg.pinv(x.T @ x)
    resid = y - y.mean()  # unter H0
    if clusters is not None:
        scores = np.array([x[idx].T @ resid[idx] for idx in groups.values()])
        g = len(groups)
        factor = g / (g - 1) * (n - 1) / (n - k)
    else:
        scores = x * resid[:, None]
        factor = n / (n - k)
    cov = factor * bread @ (scores.T @ scores) @ bread
    beta = (bread @ x.T @ y)[1:]
    sub = cov[1:, 1:]
    rank = int(np.linalg.matrix_rank(sub))
    if rank == 0:
        return DifferentialTest(
            variable, levels, dropped, 0.0, (k - 1, denominator), 1.0, clusters is not None
        )
    f = float(beta @ np.linalg.pinv(sub) @ beta) / rank
    p = float(stats.f.sf(f, rank, denominator))
    return DifferentialTest(
        variable, levels, dropped, f, (rank, denominator), p, clusters is not None
    )


def holm(p_values: Sequence[float]) -> list[float]:
    """Holm-Korrektur, monoton und auf 1 begrenzt."""
    order = sorted(range(len(p_values)), key=lambda i: p_values[i])
    adjusted = [1.0] * len(p_values)
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(p_values) - rank) * p_values[i]))
        adjusted[i] = running
    return adjusted
