"""Verschiebt das LLM die Häufigkeit der Codes? Verteilungsvergleich und Homogenitätstests."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Hashable, Sequence
from dataclasses import dataclass

import numpy as np
from scipy import stats

from llmanno.core.metrics.bootstrap import Estimate
from llmanno.core.metrics.classification import confusion_matrix


@dataclass(frozen=True)
class Share:
    gold: float
    llm: float

    @property
    def difference(self) -> float:
        return self.llm - self.gold


@dataclass(frozen=True)
class TestResult:
    statistic: float
    df: int | None
    p_value: float
    method: str


def shares(
    gold: Sequence[Hashable], pred: Sequence[Hashable], labels: Sequence[Hashable]
) -> dict[Hashable, Share]:
    n = len(gold)
    return {
        label: Share(sum(g == label for g in gold) / n, sum(p == label for p in pred) / n)
        if n
        else Share(0, 0)
        for label in labels
    }


def stuart_maxwell(
    gold: Sequence[Hashable], pred: Sequence[Hashable], labels: Sequence[Hashable]
) -> TestResult:
    """Test auf gleiche Randverteilungen zweier Kodierungen derselben Einheiten
    (Stuart 1955, Maxwell 1970). Kategorien ohne jede Nennung werden ausgelassen."""
    table = confusion_matrix(gold, pred, labels).astype(float)
    used = (table.sum(axis=0) + table.sum(axis=1)) > 0
    table = table[np.ix_(used, used)]
    k = len(table)
    if k < 2:
        return TestResult(float("nan"), None, float("nan"), "Stuart-Maxwell")
    d = (table.sum(axis=1) - table.sum(axis=0))[:-1]
    s = -(table + table.T)[:-1, :-1]
    np.fill_diagonal(s, (table.sum(axis=1) + table.sum(axis=0) - 2 * np.diag(table))[:-1])
    try:
        chi2 = float(d @ np.linalg.solve(s, d))
    except np.linalg.LinAlgError:
        chi2 = float(d @ np.linalg.pinv(s) @ d)
    return TestResult(chi2, k - 1, float(stats.chi2.sf(chi2, k - 1)), "Stuart-Maxwell")


def mcnemar(gold: Sequence[bool], pred: Sequence[bool]) -> TestResult:
    """McNemar-Test für einen binären Code; exakt (Binomial), wenn weniger als 25 Diskordanzen."""
    b = sum(1 for g, p in zip(gold, pred, strict=True) if g and not p)
    c = sum(1 for g, p in zip(gold, pred, strict=True) if p and not g)
    if b + c == 0:
        return TestResult(0.0, None, 1.0, "McNemar exakt")
    if b + c < 25:
        return TestResult(
            float(min(b, c)),
            None,
            float(stats.binomtest(min(b, c), b + c, 0.5).pvalue),
            "McNemar exakt",
        )
    chi2 = (abs(b - c) - 1) ** 2 / (b + c)
    return TestResult(
        float(chi2), 1, float(stats.chi2.sf(chi2, 1)), "McNemar mit Stetigkeitskorrektur"
    )


def ppi_mean(
    pred_unlabeled: Sequence[float],
    pred_labeled: Sequence[float],
    gold_labeled: Sequence[float],
    level: float = 0.95,
) -> Estimate:
    """Prediction-Powered Inference für einen Mittelwert (Angelopoulos et al. 2023, Satz 1).

    Mittel der LLM-Labels auf den ungelabelten Einheiten, korrigiert um den mittleren Fehler
    des LLM auf einer **zufällig** gezogenen, menschlich gelabelten Stichprobe. Unverzerrt,
    solange die Stichprobe zufällig ist; das KI ist asymptotisch (Normalapproximation)."""
    f_u = np.asarray(pred_unlabeled, dtype=float)
    rect = np.asarray(pred_labeled, dtype=float) - np.asarray(gold_labeled, dtype=float)
    big_n, n = len(f_u), len(rect)
    if big_n < 2 or n < 2:
        raise ValueError("PPI braucht mindestens zwei gelabelte und zwei ungelabelte Einheiten.")
    theta = f_u.mean() - rect.mean()
    se = math.sqrt(f_u.var(ddof=1) / big_n + rect.var(ddof=1) / n)
    z = stats.norm.ppf(0.5 + level / 2)
    return Estimate(float(theta), float(theta - z * se), float(theta + z * se), n, 0)


# Power-Tuning schätzt λ aus den Daten; bei wenigen zufällig gelabelten Einheiten verzerrt das
# die Schätzung und macht das KI zu schmal (Review, Simulation: n = 10 von 200, Abdeckung
# 0,89). Darunter gilt λ = 1, also klassisches PPI bzw. reine Gewichtung.
MIN_TUNING = 20


def _quantile(level: float, groups: int | None = None) -> float:
    """Normalquantil; cluster-robust mit t-Quantil und G − 1 Freiheitsgraden (wenige
    Transkripte halten mit dem Normalquantil das Niveau nicht)."""
    if groups is not None and groups > 1:
        return float(stats.t.ppf(0.5 + level / 2, groups - 1))
    return float(stats.norm.ppf(0.5 + level / 2))


def _cluster_variance(terms: dict[Hashable, float]) -> float:
    """Varianz einer Summe zentrierter Einflussterme, robust über Cluster (CR1)."""
    g = len(terms)
    return g / (g - 1) * sum(t * t for t in terms.values()) if g > 1 else float("nan")


def ppi_plus_mean(
    pred_unlabeled: Sequence[float],
    pred_labeled: Sequence[float],
    gold_labeled: Sequence[float],
    *,
    clusters_unlabeled: Sequence[Hashable] | None = None,
    clusters_labeled: Sequence[Hashable] | None = None,
    level: float = 0.95,
) -> tuple[Estimate, float]:
    """PPI++ für einen Mittelwert mit Power-Tuning (Angelopoulos, Duchi & Zrnic 2023).

    θ = λ·mean(f_unlabeled) + mean(y − λ·f_labeled). λ = 1 ist PPI, λ = 0 die Stichprobe
    allein; das geschätzte optimale λ macht das Intervall asymptotisch nie breiter als beide.
    Unter MIN_TUNING gelabelten Einheiten gilt λ = 1.
    Mit Clustern (Transkripte) ist die Varianz cluster-robust: Einheiten eines Transkripts
    hängen zusammen, auch wenn ein Teil gelabelt ist und ein Teil nicht. Liefert (Schätzung, λ).
    """
    f_u = np.asarray(pred_unlabeled, dtype=float)
    f_l = np.asarray(pred_labeled, dtype=float)
    y = np.asarray(gold_labeled, dtype=float)
    big_n, n = len(f_u), len(f_l)
    if big_n < 2 or n < 2:
        raise ValueError("PPI braucht mindestens zwei gelabelte und zwei ungelabelte Einheiten.")
    var_f = float(np.concatenate([f_u, f_l]).var(ddof=1))
    cov = float(np.cov(y, f_l, ddof=1)[0, 1])
    if n < MIN_TUNING:
        lam = 1.0
    else:
        lam = 0.0 if var_f == 0 else float(np.clip(cov / ((1 + n / big_n) * var_f), 0, 1))
    rect = y - lam * f_l
    theta = lam * float(f_u.mean()) + float(rect.mean())
    if clusters_unlabeled is not None and clusters_labeled is not None:
        terms: dict[Hashable, float] = defaultdict(float)
        for c, v in zip(clusters_unlabeled, lam * (f_u - f_u.mean()) / big_n, strict=True):
            terms[c] += float(v)
        for c, v in zip(clusters_labeled, (rect - rect.mean()) / n, strict=True):
            terms[c] += float(v)
        var, groups = _cluster_variance(terms), len(terms)
    else:
        var = lam**2 * float(f_u.var(ddof=1)) / big_n + float(rect.var(ddof=1)) / n
        groups = None
    half = _quantile(level, groups) * math.sqrt(var)
    return Estimate(theta, theta - half, theta + half, n, 0), lam


def weighted_mean(
    pred: Sequence[float],
    gold: Sequence[float | None],
    probability: Sequence[float | None],
    *,
    clusters: Sequence[Hashable] | None = None,
    level: float = 0.95,
) -> tuple[Estimate, float]:
    """Mittelwert aus LLM-Labels aller Einheiten und menschlichen Labels, die nicht zufällig,
    sondern mit bekannter, vom LLM abhängiger Wahrscheinlichkeit gezogen wurden
    (confidence-driven inference, Gligorić et al. 2025; active inference, Zrnic & Candès 2024).

    Je Einheit z = λ·f + ξ/π·(y − λ·f) mit ξ = 1, wenn ein menschliches Label vorliegt, und π
    der Ziehwahrscheinlichkeit (Gate 1, Zufallsstichprobe n/N). Unverzerrt für jedes feste λ,
    solange jede Einheit π > 0 hat und jede gezogene ein Label; λ minimiert die geschätzte
    Varianz (Power-Tuning, auf [0, 1] begrenzt), erst ab MIN_TUNING gelabelten Einheiten mit
    π < 1, sonst λ = 1. `gold` ist None, wo kein Label vorliegt; π zählt nur für gelabelte
    (ungelabelt ist z = λ·f). Das KI zielt auf die Prävalenz der Grundgesamtheit, aus der die
    Einheiten stammen (Paper-Konvention); für genau diese Einheiten ist es konservativ.
    Liefert (Schätzung, λ)."""
    f = np.asarray(pred, dtype=float)
    labeled = np.array([g is not None for g in gold])
    y = np.array([0.0 if g is None else float(g) for g in gold])
    pi = np.array([1.0 if p is None else float(p) for p in probability])
    big_n = len(f)
    if big_n < 2 or labeled.sum() < 2:
        raise ValueError("Die Schätzung braucht mindestens zwei gelabelte Einheiten.")
    if not ((pi[labeled] > 0) & (pi[labeled] <= 1)).all():
        raise ValueError("Jede gelabelte Einheit braucht eine Ziehwahrscheinlichkeit in (0, 1].")
    weight = np.where(labeled, 1 / pi, 0.0)
    a, b = weight * y, f * (1 - weight)  # z = a + λ·b
    var_b = float(b.var(ddof=1))
    if int((labeled & (pi < 1)).sum()) < MIN_TUNING:
        lam = 1.0
    else:
        lam = 0.0 if var_b == 0 else float(np.clip(-np.cov(a, b, ddof=1)[0, 1] / var_b, 0, 1))
    z = a + lam * b
    theta = float(z.mean())
    if clusters is not None:
        terms: dict[Hashable, float] = defaultdict(float)
        for c, v in zip(clusters, (z - theta) / big_n, strict=True):
            terms[c] += float(v)
        var, groups = _cluster_variance(terms), len(terms)
    else:
        var, groups = float(z.var(ddof=1)) / big_n, None
    half = _quantile(level, groups) * math.sqrt(var)
    return Estimate(theta, theta - half, theta + half, int(labeled.sum()), 0), lam


def classical_mean(
    values: Sequence[float],
    level: float = 0.95,
    clusters: Sequence[Hashable] | None = None,
) -> Estimate:
    """Mittelwert nur der gelabelten Stichprobe mit Normal-KI, als Vergleich zu PPI."""
    y = np.asarray(values, dtype=float)
    groups = None
    if clusters is not None and len(y) > 1:
        terms: dict[Hashable, float] = defaultdict(float)
        for c, v in zip(clusters, (y - y.mean()) / len(y), strict=True):
            terms[c] += float(v)
        se, groups = math.sqrt(_cluster_variance(terms)), len(terms)
    else:
        se = math.sqrt(y.var(ddof=1) / len(y)) if len(y) > 1 else float("nan")
    z = _quantile(level, groups)
    mean = float(y.mean()) if len(y) else float("nan")
    return Estimate(mean, mean - z * se, mean + z * se, len(y), 0)
