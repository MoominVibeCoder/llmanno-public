"""Shaffers ρ: Gilt das κ eines Test-Sets auch für alle Einheiten?

Nachbau von `calcRho` aus dem R-Paket rhoR (Eagan, Rogers, Pozen, Marquart & Shaffer; Shaffer
2017, *Quantitative Ethnography*) für einen Code als ja/nein, Gold als erste Kodierung. Es
entstehen 800 simulierte Grundgesamtheiten mit 10.000 Einheiten, deren κ unter der Schwelle liegt
(gleichverteilt zwischen 0,40 und der Schwelle, Präzision zwischen 0,6 und 1, Basisrate wie im
Test-Set); aus jeder wird ein Test-Set so groß wie das beobachtete gezogen. ρ ist der Anteil
dieser Test-Sets, deren κ das beobachtete erreicht; liegt das beobachtete κ unter ihrem
Mittel, ist ρ = 1. ρ < 0,05 spricht dafür, dass das κ aller Einheiten über der Schwelle liegt.

Die Fehlerrate von 5 % gilt gemittelt über κ zwischen 0,40 und der Schwelle; ρ ist kein Test
gegen die Schwelle selbst. Liegt das wahre κ knapp darunter, sagt ρ < 0,05 deutlich öfter
fälschlich „darüber“, und zwar umso öfter, je größer das Test-Set (Simulation an der Schwelle:
rund 10–20 % bei 50 Einheiten, 30–50 % bei 500). Vorausgesetzt sind unabhängig gezogene
Einheiten. Mehr als 10.000 Test-Einheiten gehen nicht (wie in rhoR).
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

THRESHOLD = 0.65  # übliche Schwelle in der Quantitative Ethnography (rhoR-Default: 0,9)
KAPPA_MIN = 0.4
PRECISION_MIN, PRECISION_MAX = 0.6, 1.0
POPULATION = 10_000
REPLICATES = 800


def _kappa(tp, fn, fp, tn):
    """Cohens κ für 2×2-Tabellen (auch elementweise); NaN, wenn nur eine Kategorie vorkommt."""
    n = tp + fn + fp + tn
    p_o = (tp + tn) / n
    p_e = ((tp + fn) * (tp + fp) + (fp + tn) * (fn + tn)) / n**2
    with np.errstate(divide="ignore", invalid="ignore"):
        return (p_o - p_e) / (1 - p_e)


def _precisions(baserate: float, kappas: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Präzision je κ; unmögliche Kombinationen ziehen aus dem möglichen Bereich (genPKcombo)."""
    grid = np.linspace(PRECISION_MIN, PRECISION_MAX, 10_000)
    precision = rng.choice(grid, size=len(kappas))
    lowest = (2 * baserate * kappas - 2 * baserate - kappas) / (kappas - 2)
    for i in np.flatnonzero(precision <= lowest):
        precision[i] = rng.choice(grid[grid > lowest[i]])
    return precision


def rho(
    gold: Sequence[bool], pred: Sequence[bool], *, threshold: float = THRESHOLD, seed: int = 0
) -> float | None:
    """ρ für einen Code; None, wenn das Gold nur eine Kategorie kennt oder das Test-Set
    größer als die simulierte Grundgesamtheit ist."""
    if not KAPPA_MIN < threshold < 1:
        raise ValueError(f"Die Schwelle muss zwischen {KAPPA_MIN} und 1 liegen.")
    g, p = np.asarray(gold, dtype=bool), np.asarray(pred, dtype=bool)
    n = len(g)
    if n == 0 or n > POPULATION or g.all() or not g.any():
        return None
    observed = _kappa(np.sum(g & p), np.sum(g & ~p), np.sum(~g & p), np.sum(~g & ~p))
    baserate = g.mean()
    rng = np.random.default_rng(seed)
    kappas = rng.choice(np.linspace(KAPPA_MIN, threshold, 100_000), size=REPLICATES)
    precision = _precisions(baserate, kappas, rng)
    recall = (
        kappas * precision / (2 * precision - 2 * baserate - kappas + 2 * baserate * kappas)
    )  # getR
    # prset: die vier Felder der simulierten Grundgesamtheit
    gold1 = max(round(baserate * POPULATION), 1)
    gold0 = POPULATION - gold1
    tp = np.minimum(np.maximum(np.round(gold1 * recall), 1), gold1)
    fp = np.minimum(gold0, np.maximum(np.round(tp * (1 - precision) / precision), 1))
    cells = [tp, gold1 - tp, fp, gold0 - fp]
    # Test-Set ohne Zurücklegen: multivariat hypergeometrisch, Feld für Feld
    drawn, left, remaining = [], np.full(REPLICATES, n), np.full(REPLICATES, POPULATION)
    for cell in cells[:-1]:
        remaining = remaining - cell
        x = rng.hypergeometric(cell.astype(np.int64), remaining.astype(np.int64), left)
        drawn.append(x)
        left = left - x
    drawn.append(left)
    # calcKappa in rhoR: nur Treffer oder nur gemeinsame Nein zählen als κ = 1
    simulated = np.where((drawn[0] == n) | (drawn[3] == n), 1.0, _kappa(*drawn))
    if observed < np.nanmean(simulated):
        return 1.0
    return float(np.sum(simulated >= observed) / REPLICATES)
