"""Alternative Annotator Test (Calderon, Reichart & Dror 2025, ACL).

Frage: Darf das LLM eine menschliche Kodierperson ersetzen? Für jede Person j wird geprüft, ob
das LLM mit den übrigen Menschen mindestens so gut übereinstimmt wie j selbst, bis auf einen
Kostenvorteil ε. Das LLM besteht, wenn das für mindestens die Hälfte der Personen gilt.
"""

from __future__ import annotations

from collections.abc import Hashable, Sequence
from dataclasses import dataclass

import numpy as np
from scipy import stats

MIN_ANNOTATORS = 3
MIN_INSTANCES_T_TEST = 30  # darunter Wilcoxon-Vorzeichen-Rang-Test, wie im Paper empfohlen
EPSILON = {"Expert:innen": 0.2, "geschulte Kodierende": 0.15, "Crowdworker": 0.1}


@dataclass(frozen=True)
class AnnotatorResult:
    annotator: str
    n: int
    rho_llm: float  # P(LLM ⪰ j): LLM stimmt mit den übrigen mindestens so gut überein
    rho_human: float  # P(j ⪰ LLM)
    p_value: float
    p_adjusted: float  # Benjamini-Yekutieli
    rejected: bool  # H0 (LLM schlechter als j minus ε) verworfen
    test: str


@dataclass(frozen=True)
class AltTestResult:
    epsilon: float
    q: float
    winning_rate: float  # ω: Anteil der Personen, gegen die das LLM gewinnt
    advantage_probability: float  # ρ: mittleres P(LLM ⪰ Mensch), Vergleichsmaß zwischen LLMs
    passed: bool  # ω ≥ 0.5
    annotators: list[AnnotatorResult]


def _alignment(value: Hashable, others: list[Hashable]) -> float:
    return sum(value == o for o in others) / len(others)


def alt_test(
    llm: Sequence[Hashable | None],
    humans: dict[str, Sequence[Hashable | None]],
    *,
    epsilon: float = 0.2,
    q: float = 0.05,
) -> AltTestResult:
    """`llm` und jede Spalte in `humans`: eine Position je Einheit, None = fehlt."""
    if len(humans) < MIN_ANNOTATORS:
        raise ValueError(
            f"Der Alt-Test braucht mindestens {MIN_ANNOTATORS} menschliche Kodierende."
        )
    names = list(humans)
    results: list[tuple[str, int, float, float, float, str]] = []
    for j in names:
        w_llm, w_human = [], []
        for i, own in enumerate(humans[j]):
            if own is None or llm[i] is None:
                continue
            others = [humans[k][i] for k in names if k != j and humans[k][i] is not None]
            if not others:
                continue
            s_llm, s_human = _alignment(llm[i], others), _alignment(own, others)
            # Gleichstand zählt für beide Seiten.
            w_llm.append(float(s_llm >= s_human))
            w_human.append(float(s_human >= s_llm))
        n = len(w_llm)
        if n < 2:
            results.append((j, n, float("nan"), float("nan"), float("nan"), "zu wenige Einheiten"))
            continue
        d = np.array(w_human) - np.array(w_llm)
        p_value, test = _one_sided_p(d, epsilon)
        results.append((j, n, float(np.mean(w_llm)), float(np.mean(w_human)), p_value, test))

    p_values = np.array([r[4] for r in results])
    valid = ~np.isnan(p_values)
    adjusted = np.full(len(results), np.nan)
    if valid.any():
        adjusted[valid] = stats.false_discovery_control(p_values[valid], method="by")
    annotators = [
        AnnotatorResult(
            j, n, rho_f, rho_h, p, float(adj), bool(adj <= q) if not np.isnan(adj) else False, test
        )
        for (j, n, rho_f, rho_h, p, test), adj in zip(results, adjusted, strict=True)
    ]
    omega = sum(a.rejected for a in annotators) / len(annotators)
    rho = float(np.nanmean([a.rho_llm for a in annotators]))
    return AltTestResult(epsilon, q, omega, rho, omega >= 0.5, annotators)


def _one_sided_p(d: np.ndarray, epsilon: float) -> tuple[float, str]:
    """H0: E[d] ≥ ε gegen H1: E[d] < ε, mit d = W_human − W_llm.

    Das ist die Hypothese des Papers, ρ_llm ≤ ρ_human − ε, umgestellt auf die Differenzen.
    """
    shifted = d - epsilon
    if len(d) < MIN_INSTANCES_T_TEST:
        if np.all(shifted == 0):
            return 1.0, "Wilcoxon"
        return float(stats.wilcoxon(shifted, alternative="less").pvalue), "Wilcoxon"
    if np.std(shifted, ddof=1) == 0:
        return (0.0 if shifted.mean() < 0 else 1.0), "t-Test"
    return float(stats.ttest_1samp(shifted, 0.0, alternative="less").pvalue), "t-Test"
