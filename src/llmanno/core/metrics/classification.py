"""Klassifikationsgüte des LLM gegen eine Referenzkodierung (Single- und Multi-Label).

Nicht definierte Quotienten (z. B. Precision eines nie vorhergesagten Codes) sind 0, wie
scikit-learn mit zero_division=0; die Stützung (support) zeigt, wo das zutrifft.
"""

from __future__ import annotations

from collections.abc import Hashable, Sequence
from dataclasses import dataclass, field

import numpy as np

from llmanno.core.metrics.agreement import cohen_kappa


@dataclass(frozen=True)
class ClassScores:
    precision: float
    recall: float
    f1: float
    support: int  # Häufigkeit in der Referenz
    predicted: int  # Häufigkeit in der LLM-Kodierung


@dataclass(frozen=True)
class SingleLabelReport:
    labels: list[Hashable]
    confusion: np.ndarray  # Zeilen = Referenz, Spalten = LLM
    per_class: dict[Hashable, ClassScores]
    accuracy: float
    balanced_accuracy: float
    macro_f1: float
    micro_f1: float
    weighted_f1: float
    mcc: float
    n: int


def _ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _f1(precision: float, recall: float) -> float:
    return _ratio(2 * precision * recall, precision + recall)


def confusion_matrix(
    gold: Sequence[Hashable], pred: Sequence[Hashable], labels: Sequence[Hashable]
) -> np.ndarray:
    index = {label: i for i, label in enumerate(labels)}
    matrix = np.zeros((len(labels), len(labels)), dtype=int)
    for g, p in zip(gold, pred, strict=True):
        matrix[index[g], index[p]] += 1
    return matrix


def single_label_report(
    gold: Sequence[Hashable], pred: Sequence[Hashable], labels: Sequence[Hashable]
) -> SingleLabelReport:
    matrix = confusion_matrix(gold, pred, labels)
    n = int(matrix.sum())
    true_pos = np.diag(matrix).astype(float)
    support = matrix.sum(axis=1)
    predicted = matrix.sum(axis=0)
    per_class = {}
    for i, label in enumerate(labels):
        precision = _ratio(true_pos[i], predicted[i])
        recall = _ratio(true_pos[i], support[i])
        per_class[label] = ClassScores(
            precision, recall, _f1(precision, recall), int(support[i]), int(predicted[i])
        )
    present = [per_class[label] for i, label in enumerate(labels) if support[i] > 0]
    weights = support / n if n else support
    return SingleLabelReport(
        labels=list(labels),
        confusion=matrix,
        per_class=per_class,
        accuracy=_ratio(true_pos.sum(), n),
        # Mittel der Recalls über die Codes, die in der Referenz vorkommen (wie scikit-learn).
        balanced_accuracy=float(np.mean([s.recall for s in present])) if present else 0.0,
        macro_f1=float(np.mean([per_class[label].f1 for label in labels])) if labels else 0.0,
        micro_f1=_ratio(true_pos.sum(), n),  # bei Single-Label identisch mit Accuracy
        weighted_f1=float(sum(per_class[label].f1 * weights[i] for i, label in enumerate(labels))),
        mcc=_mcc(matrix),
        n=n,
    )


def _mcc(matrix: np.ndarray) -> float:
    """Matthews-Korrelation für k Klassen (Gorodkin 2004)."""
    m = matrix.astype(float)
    s = m.sum()
    c = np.trace(m)
    t = m.sum(axis=1)
    p = m.sum(axis=0)
    denominator = np.sqrt((s**2 - (p**2).sum()) * (s**2 - (t**2).sum()))
    return float((c * s - (p * t).sum()) / denominator) if denominator else 0.0


def top_k_accuracy(
    gold: Sequence[Hashable], rankings: Sequence[Sequence[Hashable]], k: int
) -> float:
    """Anteil der Einheiten, bei denen der Referenzcode unter den ersten k Kandidaten ist."""
    hits = [g in list(r)[:k] for g, r in zip(gold, rankings, strict=True)]
    return _ratio(sum(hits), len(hits))


@dataclass(frozen=True)
class MultiLabelReport:
    labels: list[Hashable]
    per_class: dict[Hashable, ClassScores]
    micro_precision: float
    micro_recall: float
    micro_f1: float
    macro_f1: float
    hamming_loss: float
    exact_match: float  # Anteil exakt gleicher Code-Mengen
    jaccard: float  # mittlere Jaccard-Ähnlichkeit je Einheit; zwei leere Mengen gelten als gleich
    n: int
    kappa_per_class: dict[Hashable, float] = field(default_factory=dict)


def multi_label_report(
    gold: Sequence[frozenset], pred: Sequence[frozenset], labels: Sequence[Hashable]
) -> MultiLabelReport:
    n = len(gold)
    per_class: dict[Hashable, ClassScores] = {}
    kappa: dict[Hashable, float] = {}
    tp_total = fp_total = fn_total = 0
    for label in labels:
        g = [label in s for s in gold]
        p = [label in s for s in pred]
        tp = sum(a and b for a, b in zip(g, p, strict=True))
        fp = sum(b and not a for a, b in zip(g, p, strict=True))
        fn = sum(a and not b for a, b in zip(g, p, strict=True))
        precision, recall = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
        per_class[label] = ClassScores(precision, recall, _f1(precision, recall), sum(g), sum(p))
        kappa[label] = cohen_kappa(g, p, categories=[False, True])
        tp_total, fp_total, fn_total = tp_total + tp, fp_total + fp, fn_total + fn
    micro_p, micro_r = _ratio(tp_total, tp_total + fp_total), _ratio(tp_total, tp_total + fn_total)
    wrong = sum(len(g ^ p) for g, p in zip(gold, pred, strict=True))
    jaccard = [len(g & p) / len(g | p) if g | p else 1.0 for g, p in zip(gold, pred, strict=True)]
    return MultiLabelReport(
        labels=list(labels),
        per_class=per_class,
        micro_precision=micro_p,
        micro_recall=micro_r,
        micro_f1=_f1(micro_p, micro_r),
        macro_f1=float(np.mean([s.f1 for s in per_class.values()])) if labels else 0.0,
        hamming_loss=_ratio(wrong, n * len(labels)),
        exact_match=_ratio(sum(g == p for g, p in zip(gold, pred, strict=True)), n),
        jaccard=float(np.mean(jaccard)) if jaccard else 0.0,
        n=n,
        kappa_per_class=kappa,
    )


@dataclass(frozen=True)
class SelectiveReport:
    coverage: float  # Anteil der Einheiten, bei denen das LLM einen Code statt Abstain vergibt
    abstain_rate: float
    selective_accuracy: float  # Accuracy nur auf den nicht enthaltenen Einheiten


def selective_report(
    gold: Sequence[Hashable], pred: Sequence[Hashable], abstain: Hashable
) -> SelectiveReport:
    kept = [(g, p) for g, p in zip(gold, pred, strict=True) if p != abstain]
    n = len(gold)
    return SelectiveReport(
        coverage=_ratio(len(kept), n),
        abstain_rate=_ratio(n - len(kept), n),
        selective_accuracy=_ratio(sum(g == p for g, p in kept), len(kept)),
    )
