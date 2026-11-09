"""Kalibrierung: passt die Konfidenz zur tatsächlichen Trefferquote?

Dazu, ob sie Fehler überhaupt erkennt (AUROC, AURC), und wie gut sie nach einer isotonen
Rekalibrierung wäre. Verbalisierte Konfidenz ist oft schlecht kalibriert; nach ihr zu
filtern verzerrt nachgelagerte Schätzungen (Wang, Deng & Yang 2026).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Bin:
    low: float
    high: float
    n: int
    mean_confidence: float
    accuracy: float


@dataclass(frozen=True)
class CalibrationReport:
    ece: float  # Expected Calibration Error (Guo et al. 2017)
    brier: float  # mittlere quadrierte Abweichung von Konfidenz und Treffer (Top-1)
    auroc: float  # trennt die Konfidenz richtige von falschen Kodierungen? 0.5 = nein
    bins: list[Bin]
    n: int
    # Fläche unter der Risk-Coverage-Kurve: mittlere Fehlerrate, wenn man die Einheiten nach
    # absteigender Konfidenz annimmt. Kleiner ist besser; Basis ist die Fehlerrate.
    aurc: float = float("nan")
    ece_recalibrated: float = float("nan")  # nach isotoner Rekalibrierung, kreuzvalidiert
    # Risk-Coverage-Kurve als (Abdeckung, Fehlerrate), höchstens CURVE_POINTS Punkte
    curve: tuple[tuple[float, float], ...] = ()


CURVE_POINTS = 100


def calibration_report(
    confidence: Sequence[float], correct: Sequence[bool], n_bins: int = 10
) -> CalibrationReport:
    conf = np.clip(np.asarray(confidence, dtype=float), 0, 1)
    hit = np.asarray(correct, dtype=float)
    n = len(conf)
    if n == 0:
        return CalibrationReport(float("nan"), float("nan"), float("nan"), [], 0)
    bins, ece = _bins(conf, hit, n_bins)
    risks = risk_coverage(conf, hit)
    steps = sorted({round(j) for j in np.linspace(1, n, min(n, CURVE_POINTS))})
    return CalibrationReport(
        ece,
        float(np.mean((conf - hit) ** 2)),
        auroc(conf, hit),
        bins,
        n,
        float(np.mean(risks)),
        _bins(cross_fitted_isotonic(conf, hit), hit, n_bins)[1] if n >= 20 else float("nan"),
        tuple((j / n, risks[j - 1]) for j in steps),
    )


def _bins(conf: np.ndarray, hit: np.ndarray, n_bins: int) -> tuple[list[Bin], float]:
    n = len(conf)
    edges = np.linspace(0, 1, n_bins + 1)
    # Gleich breite Klassen; 1.0 gehört in die letzte Klasse.
    which = np.minimum(np.digitize(conf, edges[1:-1], right=True), n_bins - 1)
    bins, ece = [], 0.0
    for b in range(n_bins):
        mask = which == b
        if not mask.any():
            continue
        mean_conf, acc = float(conf[mask].mean()), float(hit[mask].mean())
        bins.append(Bin(float(edges[b]), float(edges[b + 1]), int(mask.sum()), mean_conf, acc))
        ece += mask.sum() / n * abs(acc - mean_conf)
    return bins, float(ece)


def aurc(confidence: Sequence[float], correct: Sequence[float | bool]) -> float:
    """Area under the risk-coverage curve (Geifman & El-Yaniv 2017); Gleichstände gemittelt."""
    risks = risk_coverage(confidence, correct)
    return float(np.mean(risks)) if risks else float("nan")


def risk_coverage(confidence: Sequence[float], correct: Sequence[float | bool]) -> list[float]:
    """Fehlerrate unter den j sichersten Einheiten, j = 1 … n."""
    conf = np.asarray(confidence, dtype=float)
    err = 1 - np.asarray(correct, dtype=float)
    if not len(conf):
        return []
    # Gleich sichere Einheiten zählen als Block mit ihrer mittleren Fehlerrate.
    levels, inverse = np.unique(-conf, return_inverse=True)
    risks, taken, errors = [], 0, 0.0
    for level in range(len(levels)):
        block = err[inverse == level]
        for j in range(1, len(block) + 1):
            risks.append((errors + j * block.mean()) / (taken + j))
        taken, errors = taken + len(block), errors + block.sum()
    return [float(r) for r in risks]


def isotonic_fit(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Isotone Regression (pool adjacent violators): Stützstellen und monotone Werte."""
    xs, inverse, counts = np.unique(x, return_inverse=True, return_counts=True)
    means = np.bincount(inverse, weights=y) / counts
    values: list[float] = []
    weights: list[float] = []
    ends: list[int] = []
    for i, (m, w) in enumerate(zip(means, counts, strict=True)):
        values.append(float(m))
        weights.append(float(w))
        ends.append(i)
        while len(values) > 1 and values[-2] > values[-1]:
            w2 = weights[-2] + weights[-1]
            values[-2:] = [(values[-2] * weights[-2] + values[-1] * weights[-1]) / w2]
            weights[-2:] = [w2]
            ends[-2:] = [ends[-1]]
    fitted = np.empty(len(xs))
    start = 0
    for value, end in zip(values, ends, strict=True):
        fitted[start : end + 1] = value
        start = end + 1
    return xs, fitted


def isotonic_predict(xs: np.ndarray, fitted: np.ndarray, x: np.ndarray) -> np.ndarray:
    return np.interp(x, xs, fitted)  # außerhalb: Randwerte


def cross_fitted_isotonic(
    conf: np.ndarray, hit: np.ndarray, folds: int = 5, seed: int = 0
) -> np.ndarray:
    """Rekalibrierte Konfidenz je Einheit, jeweils ohne die eigene Falte gelernt."""
    order = np.random.default_rng(seed).permutation(len(conf))
    out = np.empty(len(conf))
    for fold in range(folds):
        test = order[fold::folds]
        train = np.setdiff1d(order, test)
        xs, fitted = isotonic_fit(conf[train], hit[train])
        out[test] = isotonic_predict(xs, fitted, conf[test])
    return out


def auroc(score: Sequence[float], positive: Sequence[float | bool]) -> float:
    """Fläche unter der ROC-Kurve als Mann-Whitney-Wahrscheinlichkeit; Gleichstände zählen halb."""
    s = np.asarray(score, dtype=float)
    y = np.asarray(positive, dtype=bool)
    pos, neg = s[y], s[~y]
    if not len(pos) or not len(neg):
        return float("nan")
    greater = (pos[:, None] > neg[None, :]).sum()
    ties = (pos[:, None] == neg[None, :]).sum()
    return float((greater + 0.5 * ties) / (len(pos) * len(neg)))
