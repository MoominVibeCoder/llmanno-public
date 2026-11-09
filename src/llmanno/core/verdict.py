"""Kurzurteil der Evaluation: wenige Prüfpunkte mit Ampel und der nächste Schritt.

Die Schwellen sind Faustregeln, keine Gesetze: für α die Konventionen von Krippendorff
(2018), für F1 und den Vergleich mit Menschen übliche Daumenregeln. Die Kennwerte selbst
stehen darunter im Bericht. Texte erzeugt die Oberfläche, damit sie übersetzbar bleiben."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

from llmanno.core.evaluation import (
    MIN_SUPPORT,
    PROMPT_STABLE,
    EvaluationReport,
    LLMComparison,
    main_comparison,
)
from llmanno.core.review import REVIEW_RANDOM

GOOD_ALPHA, TENTATIVE_ALPHA = 0.800, 0.667  # Krippendorff (2018)
GOOD_F1, WEAK_F1 = 0.70, 0.50
HUMAN_CLOSE, HUMAN_FAR = 0.05, 0.15  # α des LLM höchstens so weit unter dem der Menschen
MAX_WIDTH = 0.20  # Breite des 95-%-Intervalls von α; darüber zu wenig Gold
MIN_UNITS = 30  # wie der Stichprobenrechner (metrics.sample_size)
SIGNIFICANT = 0.05

Level = Literal["green", "yellow", "red", "grey"]


@dataclass(frozen=True)
class Check:
    key: str  # alpha | humans | weakest | basis | robust
    level: Level
    values: dict[str, float | str | None] = field(default_factory=dict)


@dataclass(frozen=True)
class Verdict:
    reference: str
    subset: str  # all | dev | test
    has_split: bool
    by_review: bool  # Referenz ist die blinde Review-Stichprobe, kein Gold
    checks: list[Check]
    # sharpen | sharpen_dev | more_gold | more_reviews | split | freeze | evaluate_test |
    # report | broken | rethink
    next_step: str

    def check(self, key: str) -> Check:
        return next(c for c in self.checks if c.key == key)


def verdict(report: EvaluationReport, consensus: str | None = None) -> Verdict | None:
    """None ohne Referenz (weder Gold noch blinde Reviews)."""
    main = main_comparison(report.comparisons, consensus)
    if main is None:
        return None
    by_review = main.reference == REVIEW_RANDOM
    checks = [
        _alpha(main),
        _humans(main, report),
        _weakest(main),
        _basis(main),
        _robust(report),
    ]
    return Verdict(
        main.reference, report.subset, report.has_split, by_review, checks,
        _next_step(report, checks, by_review),
    )  # fmt: skip


def _bad(*values: float) -> bool:
    return any(math.isnan(v) for v in values)


def _alpha(main: LLMComparison) -> Check:
    alpha = main.agreement.alpha
    values: dict[str, float | str | None] = {
        "alpha": alpha.value, "low": alpha.low, "high": alpha.high,
    }  # fmt: skip
    if _bad(alpha.value):  # etwa nur ein Code in den Daten
        return Check("alpha", "grey", values)
    if alpha.value >= GOOD_ALPHA and alpha.low >= TENTATIVE_ALPHA:
        level: Level = "green"
    elif alpha.value >= GOOD_ALPHA:
        level, values["cause"] = "yellow", "uncertain"  # hoch, aber das Intervall reicht tief
    elif alpha.value >= TENTATIVE_ALPHA:
        level = "yellow"
    else:
        level = "red"
    return Check("alpha", level, values)


def _humans(main: LLMComparison, report: EvaluationReport) -> Check:
    if report.baseline is None or _bad(report.baseline.alpha, main.agreement.alpha.value):
        return Check("humans", "grey")
    gap = report.baseline.alpha - main.agreement.alpha.value
    level: Level = "green" if gap <= HUMAN_CLOSE else "yellow" if gap <= HUMAN_FAR else "red"
    values: dict[str, float | str | None] = {
        "llm": main.agreement.alpha.value, "humans": report.baseline.alpha,
    }  # fmt: skip
    if report.alt_test is not None:
        values["alt_test"] = "passed" if report.alt_test.passed else "failed"
    return Check("humans", level, values)


def _weakest(main: LLMComparison) -> Check:
    details = main.single if main.single is not None else main.multi
    scores = details.per_class if details is not None else {}
    rated = {str(c): s.f1 for c, s in scores.items() if s.support >= MIN_SUPPORT and not _bad(s.f1)}
    if not rated:
        return Check("weakest", "grey", {"min_support": MIN_SUPPORT, "codes": len(scores)})
    code = min(rated, key=rated.__getitem__)
    f1 = rated[code]
    level: Level = "green" if f1 >= GOOD_F1 else "yellow" if f1 >= WEAK_F1 else "red"
    return Check("weakest", level, {"code": code, "f1": f1, "rated": len(rated),
                                     "unrated": len(scores) - len(rated)})  # fmt: skip


def _basis(main: LLMComparison) -> Check:
    alpha = main.agreement.alpha
    width = alpha.high - alpha.low
    enough = main.agreement.n >= MIN_UNITS and not _bad(width) and width <= MAX_WIDTH
    return Check("basis", "green" if enough else "yellow", {"n": main.agreement.n, "width": width})


def _robust(report: EvaluationReport) -> Check:
    if not (report.sensitivity or report.differential):
        return Check("robust", "grey")
    unstable = [s.model for s in report.sensitivity if s.alpha < PROMPT_STABLE]
    biased = [t.variable for t in report.differential if t.p_holm < SIGNIFICANT]
    level: Level = "yellow" if unstable or biased else "green"
    return Check("robust", level, {
        "wording": "tested" if report.sensitivity else None,
        "subgroups": "tested" if report.differential else None,
        "unstable": ", ".join(unstable) or None,
        "biased": ", ".join(biased) or None,
    })  # fmt: skip


def _next_step(report: EvaluationReport, checks: list[Check], by_review: bool) -> str:
    levels = {c.key: c.level for c in checks}
    poor = "red" in (levels["alpha"], levels["weakest"])
    frozen = report.instrument_hash is not None
    tested = report.subset == "test" or (report.subset == "all" and report.has_split and frozen)
    if tested:  # auch „alle“ nach dem Freeze enthält den Prüfteil und zählt als Test-Auswertung
        if report.freeze_intact is False:
            return "broken"
        return "rethink" if poor else "report"
    if poor:
        return "sharpen_dev" if report.has_split else "sharpen"
    if levels["basis"] == "yellow":
        return "more_reviews" if by_review else "more_gold"
    if by_review:  # ohne Gold gibt es nichts aufzuteilen; die Stichprobe ist die Validierung
        return "report"
    if not report.has_split:
        return "split"
    return "evaluate_test" if frozen and report.freeze_intact else "freeze"
