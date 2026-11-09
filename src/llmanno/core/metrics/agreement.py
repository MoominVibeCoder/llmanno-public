"""Übereinstimmungsmaße für k Kodierende mit fehlenden Werten.

Die Formeln folgen Gwet (2014) und Krippendorff (2011); die Tests prüfen gegen deren
veröffentlichte Beispiele (irrCAC-Datensatz cac.raw4raters, Krippendorffs Reliabilitätsdaten)
und gegen Cohens bzw. Fleiss' Rechenbeispiele.

`ratings` ist eine Matrix Einheiten × Kodierende; fehlende Werte sind None. Werte müssen
hashbar sein (Multi-Label: frozenset).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Hashable, Sequence
from typing import Literal

import numpy as np

Value = Hashable
Ratings = Sequence[Sequence[Value | None]]
Weights = Literal["linear", "quadratic"]


def _counts(ratings: Ratings, categories: Sequence[Value] | None) -> tuple[np.ndarray, list]:
    """Matrix Einheiten × Kategorien mit der Zahl der Kodierungen je Kategorie."""
    observed = sorted({v for row in ratings for v in row if v is not None}, key=repr)
    cats = list(categories) if categories is not None else observed
    unknown = set(observed) - set(cats)
    if unknown:
        raise ValueError(f"Werte außerhalb der Kategorien: {sorted(map(repr, unknown))}")
    index = {c: i for i, c in enumerate(cats)}
    counts = np.zeros((len(ratings), len(cats)))
    for u, row in enumerate(ratings):
        for value in row:
            if value is not None:
                counts[u, index[value]] += 1
    return counts, cats


def percent_agreement(ratings: Ratings) -> float:
    """Anteil übereinstimmender Kodiererpaare je Einheit, gemittelt über Einheiten mit ≥ 2
    Kodierungen (Gwet 2014)."""
    counts, _ = _counts(ratings, None)
    return _pa(counts)


def _pa(counts: np.ndarray) -> float:
    r = counts.sum(axis=1)
    pairable = r >= 2
    if not pairable.any():
        return float("nan")
    c, rr = counts[pairable], r[pairable]
    return float(np.mean((c * (c - 1)).sum(axis=1) / (rr * (rr - 1))))


def _class_shares(counts: np.ndarray) -> np.ndarray:
    """Kategorienanteile, gemittelt über alle Einheiten mit ≥ 1 Kodierung (wie irrCAC)."""
    r = counts.sum(axis=1)
    rated = r >= 1
    return (counts[rated] / r[rated, None]).mean(axis=0)


def _chance_corrected(pa: float, pe: float) -> float:
    return float("nan") if pe >= 1 else (pa - pe) / (1 - pe)


def gwet_ac1(ratings: Ratings, categories: Sequence[Value] | None = None) -> float:
    """Gwets AC1, robust gegen schiefe Prävalenz (Gwet 2008). `categories` = alle möglichen
    Codes; ohne Angabe nur die beobachteten."""
    counts, cats = _counts(ratings, categories)
    q = len(cats)
    if q < 2:
        return float("nan")
    pi = _class_shares(counts)
    pe = float((pi * (1 - pi)).sum() / (q - 1))
    return _chance_corrected(_pa(counts), pe)


def fleiss_kappa(ratings: Ratings) -> float:
    """Fleiss' Kappa, verallgemeinert für fehlende Werte (Gwet 2014, wie irrCAC)."""
    counts, _ = _counts(ratings, None)
    pi = _class_shares(counts)
    return _chance_corrected(_pa(counts), float((pi**2).sum()))


def brennan_prediger(ratings: Ratings, categories: Sequence[Value] | None = None) -> float:
    """Brennan-Prediger-Koeffizient; bei zwei Kategorien und zwei Kodierenden = PABAK."""
    counts, cats = _counts(ratings, categories)
    if len(cats) < 2:
        return float("nan")
    return _chance_corrected(_pa(counts), 1 / len(cats))


# --- Krippendorffs Alpha ---------------------------------------------------------------------


def masi_distance(a: frozenset, b: frozenset) -> float:
    """MASI-Distanz für Mengen von Codes (Passonneau 2006)."""
    if a == b:
        return 0.0
    union = a | b
    jaccard = len(a & b) / len(union) if union else 1.0
    if a <= b or b <= a:
        monotonicity = 2 / 3
    elif a & b:
        monotonicity = 1 / 3
    else:
        monotonicity = 0.0
    return 1 - jaccard * monotonicity


def krippendorff_alpha(
    ratings: Ratings,
    level: Literal["nominal", "ordinal", "interval", "ratio", "masi"] = "nominal",
    order: Sequence[Value] | None = None,
) -> float:
    """Krippendorffs Alpha über die Koinzidenzmatrix (Krippendorff 2011).

    `order` legt bei ordinalem Niveau die Rangfolge der Werte fest; ohne Angabe werden die
    Werte sortiert. Interval/ratio brauchen Zahlen, masi braucht frozensets.
    """
    pairable = [[v for v in row if v is not None] for row in ratings]
    pairable = [row for row in pairable if len(row) >= 2]
    if not pairable:
        return float("nan")
    values = (
        order if order is not None else sorted({v for row in pairable for v in row}, key=_sort_key)
    )
    index = {v: i for i, v in enumerate(values)}
    o = np.zeros((len(values), len(values)))
    for row in pairable:
        m = len(row)
        for i, a in enumerate(row):
            for j, b in enumerate(row):
                if i != j:
                    o[index[a], index[b]] += 1 / (m - 1)
    n_c = o.sum(axis=1)
    n = n_c.sum()
    delta = _delta(level, list(values), n_c)
    observed = (o * delta).sum()
    expected = (np.outer(n_c, n_c) * delta).sum() / (n - 1)
    return float("nan") if expected == 0 else float(1 - observed / expected)


def _sort_key(value: Value) -> tuple[int, object]:
    return (0, value) if isinstance(value, int | float) else (1, repr(value))


def _delta(level: str, values: list, n_c: np.ndarray) -> np.ndarray:
    k = len(values)
    delta = np.zeros((k, k))
    for i in range(k):
        for j in range(k):
            if i == j:
                continue
            a, b = values[i], values[j]
            if level == "nominal":
                delta[i, j] = 1.0
            elif level == "ordinal":
                low, high = min(i, j), max(i, j)
                delta[i, j] = (n_c[low : high + 1].sum() - (n_c[i] + n_c[j]) / 2) ** 2
            elif level == "interval":
                delta[i, j] = (a - b) ** 2
            elif level == "ratio":
                delta[i, j] = ((a - b) / (a + b)) ** 2 if a + b else 0.0
            elif level == "masi":
                delta[i, j] = masi_distance(a, b)
            else:
                raise ValueError(f"Unbekanntes Skalenniveau: {level}")
    return delta


# --- Zwei Kodierende -------------------------------------------------------------------------


def _paired(a: Sequence[Value | None], b: Sequence[Value | None]) -> list[tuple[Value, Value]]:
    if len(a) != len(b):
        raise ValueError("Beide Kodierungen brauchen dieselbe Länge.")
    return [(x, y) for x, y in zip(a, b, strict=True) if x is not None and y is not None]


def _table(
    pairs: list[tuple[Value, Value]], categories: Sequence[Value] | None
) -> tuple[np.ndarray, list]:
    cats = (
        list(categories)
        if categories is not None
        else sorted({v for p in pairs for v in p}, key=_sort_key)
    )
    index = {c: i for i, c in enumerate(cats)}
    table = np.zeros((len(cats), len(cats)))
    for x, y in pairs:
        table[index[x], index[y]] += 1
    return table, cats


def cohen_kappa(
    a: Sequence[Value | None],
    b: Sequence[Value | None],
    *,
    weights: Weights | None = None,
    categories: Sequence[Value] | None = None,
) -> float:
    """Cohens Kappa (1960), gewichtet nach Cohen (1968). Bei Gewichtung bestimmt die
    Reihenfolge von `categories` die Ordnung."""
    pairs = _paired(a, b)
    if not pairs:
        return float("nan")
    table, cats = _table(pairs, categories)
    p = table / table.sum()
    expected = np.outer(p.sum(axis=1), p.sum(axis=0))
    w = _agreement_weights(len(cats), weights)
    return _chance_corrected(float((w * p).sum()), float((w * expected).sum()))


def _agreement_weights(k: int, weights: Weights | None) -> np.ndarray:
    if weights is None or k < 2:
        return np.eye(k)
    i, j = np.indices((k, k))
    distance = np.abs(i - j) / (k - 1)
    return 1 - (distance if weights == "linear" else distance**2)


def scott_pi(a: Sequence[Value | None], b: Sequence[Value | None]) -> float:
    """Scotts Pi: wie Kappa, aber mit gemeinsamen Randverteilungen."""
    pairs = _paired(a, b)
    if not pairs:
        return float("nan")
    table, _ = _table(pairs, None)
    p = table / table.sum()
    pooled = (p.sum(axis=0) + p.sum(axis=1)) / 2
    return _chance_corrected(float(np.trace(p)), float((pooled**2).sum()))


def prevalence_bias_index(a: Sequence[bool], b: Sequence[bool]) -> tuple[float, float]:
    """Prävalenz- und Bias-Index für einen binären Code (Byrt, Bishop & Carlin 1993).

    Erklärt, warum Kappa bei schiefer Verteilung niedrig ausfallen kann, obwohl fast alle
    Einheiten übereinstimmen (Kappa-Paradox)."""
    pairs = _paired(list(a), list(b))
    n = len(pairs)
    if not n:
        return float("nan"), float("nan")
    yes_yes = sum(1 for x, y in pairs if x and y)
    no_no = sum(1 for x, y in pairs if not x and not y)
    yes_no = sum(1 for x, y in pairs if x and not y)
    no_yes = sum(1 for x, y in pairs if not x and y)
    return (yes_yes - no_no) / n, (yes_no - no_yes) / n


def majority(values: Sequence[Value | None]) -> Value | None:
    """Mehrheitswert; None bei Gleichstand oder ohne Werte."""
    counts = Counter(v for v in values if v is not None).most_common()
    if not counts or (len(counts) > 1 and counts[0][1] == counts[1][1]):
        return None
    return counts[0][0]


Statistic = Callable[[Ratings], float]
