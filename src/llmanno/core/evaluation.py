"""Auswertung: LLM gegen Gold, Mensch-Mensch-Baseline, Stabilität, Alt-Test (PLAN, Abschnitt 6).

Alle Kennwerte beziehen sich auf die finalen Codes des letzten Laufs je Panel-Modell. Einheiten
ohne gültiges LLM-Ergebnis oder ohne Referenzwert fallen aus dem jeweiligen Vergleich heraus;
wie viele das sind, steht im Bericht.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass, field, replace
from itertools import combinations
from typing import Literal

import numpy as np

from llmanno.core.aggregate import PANEL_INCOMPLETE, ItemResult, decision
from llmanno.core.config import ProjectConfig
from llmanno.core.engine import (
    basis_warnings,
    prepare_prompts,
    result_basis,
    results,
    served_model_warnings,
)
from llmanno.core.examples import seen_example_ids
from llmanno.core.gold import GoldTable, Label, build_gold
from llmanno.core.i18n import Message
from llmanno.core.metrics import agreement as ag
from llmanno.core.metrics.alt_test import MIN_ANNOTATORS, AltTestResult, alt_test
from llmanno.core.metrics.bootstrap import DEFAULT_RESAMPLES, Estimate, bootstrap
from llmanno.core.metrics.calibration import CalibrationReport, calibration_report
from llmanno.core.metrics.classification import (
    MultiLabelReport,
    SelectiveReport,
    SingleLabelReport,
    multi_label_report,
    selective_report,
    single_label_report,
    top_k_accuracy,
)
from llmanno.core.metrics.dawid_skene import dawid_skene
from llmanno.core.metrics.differential import DifferentialTest, error_rate_test, holm
from llmanno.core.metrics.prevalence import (
    Share,
    TestResult,
    classical_mean,
    mcnemar,
    ppi_plus_mean,
    shares,
    stuart_maxwell,
    weighted_mean,
)
from llmanno.core.metrics.rho import rho
from llmanno.core.project import GOLD_UPDATED, Project
from llmanno.core.review import (
    REVIEW_ALL,
    REVIEW_RANDOM,
    WeightedReviews,
    random_sample_gaps,
    review_agreement,
    review_gold,
    weighted_reviews,
)
from llmanno.core.sensitivity import ModelSensitivity, sensitivity_report
from llmanno.core.store import Unit
from llmanno.core.units import Item
from llmanno.core.validation import (
    TEST_EVALUATED,
    cluster_key,
    count_test_evaluations,
    count_test_exposures,
    freeze_intact,
    grouped_units,
)

MIN_SUPPORT = 20  # darunter ist ein Kennwert pro Code kaum interpretierbar
MIN_ITEMS = 30
MIN_CLUSTERS = 10  # darunter unterschätzt der Cluster-Bootstrap die Unsicherheit
MAJORITY = "Mehrheit der Kodierenden"
PROMPT_STABLE = 0.8  # inter-prompt α darunter: Warnung (Krippendorffs Schwelle)


@dataclass(frozen=True)
class AgreementSet:
    n: int
    percent: Estimate
    kappa: Estimate
    ac1: Estimate
    alpha: Estimate
    scott_pi: Estimate
    brennan_prediger: Estimate  # PABAK
    kappa_weighted: Estimate | None = None  # quadratisch, nur bei ordinalem Skalenniveau


@dataclass(frozen=True)
class SubgroupRow:
    """Kennwerte innerhalb einer Ausprägung der Subgruppen-Variable (PLAN 6 H)."""

    value: str
    n: int
    percent: float
    kappa: Estimate
    ac1: float
    accuracy: float  # Single-Label: Accuracy; Multi-Label: exakte Übereinstimmung
    macro_f1: float
    shares: dict[str, Share]


@dataclass(frozen=True)
class PrevalenceRow:
    """Anteil eines Codes: naiv aus den LLM-Labels, nur aus der Stichprobe, und per PPI++."""

    code: str
    llm: float
    sample: Estimate
    ppi: Estimate  # PPI++ mit Power-Tuning
    ppi_lambda: float = 1.0  # 1 = klassisches PPI, 0 = nur die Stichprobe
    # Alle blinden Reviews, gewichtet mit der Ziehwahrscheinlichkeit (Gate 1, Zufall n/N):
    # confidence-driven inference (Gligorić et al. 2025). None ohne Zufallsanteil.
    weighted: Estimate | None = None
    weighted_lambda: float | None = None


@dataclass(frozen=True)
class LLMComparison:
    reference: str
    agreement: AgreementSet
    accuracy: Estimate | None  # Single-Label
    macro_f1: Estimate
    single: SingleLabelReport | None
    multi: MultiLabelReport | None
    top_k: dict[int, Estimate]
    selective: SelectiveReport | None
    shares: dict[str, Share]
    stuart_maxwell: TestResult | None
    mcnemar: dict[str, TestResult]
    calibration: dict[str, CalibrationReport]  # "p_sc", "p_verbal", im Panel "panel"
    low_support: list[str]
    subgroups: list[SubgroupRow] = field(default_factory=list)
    # Trefferquote je Übereinstimmung der Läufe, etwa 3/3 (adaptiv gestoppt) neben 5/5;
    # nur Single-Label: (Stufe, n Einheiten, Trefferquote)
    by_runs: list[tuple[str, int, float]] = field(default_factory=list)
    mcc: Estimate | None = None  # Single-Label
    # Je Code als ja/nein: κ und Shaffers ρ (rhoR, Schwelle metrics.rho.THRESHOLD)
    code_kappa: dict[str, float] = field(default_factory=dict)
    rho: dict[str, float | None] = field(default_factory=dict)


@dataclass(frozen=True)
class GroupComparison:
    """Übereinstimmung auf Ebene der Code-Gruppen (Oberkategorien)."""

    reference: str
    agreement: AgreementSet


@dataclass(frozen=True)
class HumanBaseline:
    pairs: dict[tuple[str, str], AgreementSet]
    alpha: float  # alle Kodierenden
    fleiss: float
    ac1: float
    alpha_with_llm: float


@dataclass(frozen=True)
class Stability:
    intra_alpha: dict[str, float]  # Modell → α über seine Läufe (Test-Retest im selben Lauf)
    inter_alpha: float | None  # α über die Modellentscheidungen im Panel


@dataclass(frozen=True)
class DawidSkeneCheck:
    """Gegenprobe: das Panel nach Dawid-Skene gewichtet statt gleich gewichtet (Single-Label,
    ab MIN_DS_MODELS Modellen). Die Trefferquoten sind ohne Gold geschätzt."""

    reference: str
    accuracy: dict[str, float]  # geschätzte Trefferquote je Modell
    n: int  # Einheiten mit gültigem Ergebnis
    changed: int  # davon gewichtet anders kodiert
    n_reference: int  # Einheiten mit Referenz für die κ-Werte
    kappa_panel: float  # gleich gewichtet gegen die Referenz
    kappa_weighted: float  # nach Dawid-Skene gegen die Referenz


MIN_DS_MODELS = 3  # mit zwei Modellen ist Dawid-Skene kaum bestimmt


@dataclass(frozen=True)
class EvaluationReport:
    labels: list[str]
    mode: str
    n_items: int
    n_scored: int  # Einheiten mit gültigem LLM-Ergebnis
    gold: GoldTable
    comparisons: list[LLMComparison]
    baseline: HumanBaseline | None
    stability: Stability
    alt_test: AltTestResult | None
    resamples: int
    seed: int
    clustered: bool
    n_clusters: int | None = None
    subset: str = "all"  # all | dev | test
    has_split: bool = False
    instrument_hash: str | None = None  # gesetzt, wenn eingefroren
    freeze_intact: bool | None = None
    test_evaluations: int = 0  # wie oft das Test-Set bisher ausgewertet wurde (inklusive dieser)
    test_exposures: int = 0  # Exporte mit Test-Ergebnissen vor dem Freeze
    subgroup: str | None = None  # Schlüssel der Subgruppen-Variable, siehe subgroup_options
    model: str | None = None  # None: Panel; sonst nur dieses Modell
    prevalence: list[PrevalenceRow] = field(default_factory=list)  # PPI, PLAN 3.11
    prevalence_n: tuple[int, int] | None = None  # (gelabelt, ungelabelt)
    prevalence_weighted_n: int | None = None  # blinde Reviews in der gewichteten Schätzung
    prevalence_weighted_open: int = 0  # Queue-Einheiten ohne verwertbare blinde Review
    differential: list[DifferentialTest] = field(default_factory=list)
    differential_reference: str | None = None  # Referenz der Fehler-Tests
    sensitivity: list[ModelSensitivity] = field(default_factory=list)  # Prompt-Varianten
    groups: list[str] = field(default_factory=list)  # Code-Gruppen ohne Restkategorie
    group_comparisons: list[GroupComparison] = field(default_factory=list)
    group_baseline: float | None = None  # Krippendorffs α der Menschen auf den Gruppen
    review_alpha: tuple[float, int] | None = None  # α blinder Reviewer, Zahl der Einheiten
    dawid_skene: DawidSkeneCheck | None = None
    n_examples: int = 0  # ausgeschlossene Beispiel-Einheiten (Few-Shot aus Dev)
    warnings: list[str] = field(default_factory=list)


Subset = Literal["all", "dev", "test"]
SUBSET_LABELS = {"all": "alle Einheiten", "dev": "Dev", "test": "Test"}
SUBSET_LABELS_EN = {"all": "all units", "dev": "dev", "test": "test"}
NO_VALUE = "(ohne Wert)"


def reference_en(name: str) -> str:
    """Englischer Name der eingebauten Referenzen; Gold-Spalten heißen wie in den Daten."""
    return {
        MAJORITY: "majority of coders",
        REVIEW_RANDOM: "blind review (random sample)",
        REVIEW_ALL: "blind review (all)",
    }.get(name, name)


def subgroup_options(cfg: ProjectConfig) -> list[tuple[str, str]]:
    """Mögliche Subgruppen-Variablen als (Schlüssel, Anzeigename)."""
    if cfg.data is None:
        return []
    mapping = cfg.data.mapping
    per_line = cfg.coding.units.mode != "holistic"  # holistisch gibt es keinen Sprecher je Einheit
    options = []
    if mapping.speaker and per_line:
        options.append(("speaker", f"Sprecher ({mapping.speaker})"))
    if mapping.group and per_line:
        options.append(("group", f"Gruppe ({mapping.group})"))
    options += [(f"meta:{column}", column) for column in mapping.meta]
    return options + [
        (f"analysis:{column}", f"{column} (nicht im Prompt)") for column in mapping.analysis
    ]


def subgroup_values(units: list[Unit], items: list[Item], key: str) -> dict[str, str]:
    by_id = {u.unit_id: u for u in units}
    values = {}
    for item in items:
        unit = by_id[item.lines[0].unit_id]
        if key == "speaker":
            raw = unit.speaker
        elif key == "group":
            raw = unit.group
        elif key.startswith("meta:"):
            raw = item.meta.get(key.removeprefix("meta:"))
        elif key.startswith("analysis:"):
            raw = unit.source.get(key.removeprefix("analysis:"))
        else:
            raise ValueError(f"Unbekannte Subgruppen-Variable: {key}")
        values[item.item_id] = NO_VALUE if raw is None or str(raw).strip() == "" else str(raw)
    return values


def evaluate(
    project: Project,
    *,
    subset: Subset = "all",
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = 0,
    subgroup: str | None = None,
    model: str | None = None,
) -> EvaluationReport:
    """Mit Split gilt: Dev jederzeit, Test (und „alle“, das Test enthält) erst nach dem Freeze.
    Jede Auswertung, die Test-Einheiten berührt, wird protokolliert."""
    cfg = project.config
    _, all_items = prepare_prompts(project)
    shown = seen_example_ids(project)  # Few-Shot-Beispiele: Das Modell sah ihr Gold.
    all_items = [i for i in all_items if i.item_id not in shown]
    items = all_items
    assert cfg.codebook is not None
    parts = project.store.split()
    if subset != "all" and not parts:
        raise ValueError("Es gibt noch keinen Dev/Test-Split (Tab Splits & Freeze).")
    touches_test = bool(parts) and subset in ("all", "test")
    if touches_test and cfg.freeze is None:
        raise PermissionError(
            "Das Test-Set ist bis zum Freeze gesperrt. Vorher nur Dev auswerten, dann einfrieren."
        )
    if subset != "all":
        items = [i for i in items if parts.get(i.item_id) == subset]
    units = project.store.units()
    gold = build_gold(cfg, units, items)
    test_items = [i for i in all_items if parts.get(i.item_id) == "test"]
    test_reference = (
        _reference_sha(build_gold(cfg, units, test_items), [i.item_id for i in test_items])
        if test_items
        else None
    )
    item_ids = [i.item_id for i in items]
    reviewed = review_gold(project, item_ids)
    if not gold.columns and not reviewed:
        raise ValueError(
            "Keine Referenz: weder Gold-Spalten (Tab Daten) noch blinde Reviews (Tab Review)."
        )
    panel = results(project, item_ids)
    if model is not None and model not in {m.key for m in cfg.panel}:
        raise ValueError(f"Modell {model} steht nicht im Panel.")
    by_item = panel if model is None else results(project, item_ids, only=[model])
    multi = cfg.mode == "multi"
    labels = cfg.codebook.allowed_ids()
    abstain = cfg.codebook.abstain_id if cfg.codebook.abstain else None
    llm = {i: _llm_label(by_item[i], multi) for i in item_ids}

    # Einheiten eines Transkripts hängen zusammen: ganze Gruppen ziehen (grouped_units).
    group = {u.unit_id: u.group for u in units}
    clustered = grouped_units(cfg) and any(g is not None for g in group.values())
    clusters = {i: cluster_key(cfg, i, group.get(i)) for i in item_ids} if clustered else None
    ordinal = cfg.codebook.scale == "ordinal" and not multi
    order = [c.id for c in sorted(cfg.codebook.active_codes(), key=lambda c: c.rank or 0)]
    if abstain:
        order.append(abstain)

    def agreement(a: list[Label], b: list[Label], ids: list[str]) -> AgreementSet:
        return _agreement_set(
            a, b, [clusters[i] for i in ids] if clusters else None, labels=labels, multi=multi,
            order=order if ordinal else None, resamples=resamples, seed=seed,
        )  # fmt: skip

    references: list[tuple[str, dict[str, Label | None]]] = [
        (c, gold.labels[c]) for c in gold.coders
    ]
    if gold.consensus:
        references.append((gold.consensus, gold.labels[gold.consensus]))
    elif len(gold.coders) >= 2:
        references.append(
            (MAJORITY, {i: ag.majority([gold.labels[c][i] for c in gold.coders]) for i in item_ids})
        )
    references.extend(reviewed.items())  # blinde Reviews als eigene Gold-Schicht

    comparisons = []
    for name, ref in references:
        ids = [i for i in item_ids if llm[i] is not None and ref[i] is not None]
        if not ids:
            continue
        g, p = [ref[i] for i in ids], [llm[i] for i in ids]
        comparisons.append(
            _compare(
                name,
                ids,
                g,
                p,
                by_item,
                agreement(g, p, ids),
                multi=multi,
                labels=labels,
                k=cfg.k,
                abstain=abstain,
                clusters=clusters,
                resamples=resamples,
                seed=seed,
            )  # fmt: skip
        )

    if subgroup is not None:
        values = subgroup_values(units, items, subgroup)
        comparisons = [
            replace(
                c,
                subgroups=_subgroup_rows(
                    c,
                    references,
                    llm,
                    values,
                    labels=labels,
                    multi=multi,
                    clusters=clusters,
                    resamples=resamples,
                    seed=seed,
                ),  # fmt: skip
            )
            for c in comparisons
        ]

    main = main_comparison(comparisons, gold.consensus)
    differential = (
        _differential(cfg, units, items, dict(references)[main.reference], llm, clusters)
        if main
        else []
    )

    weighted = weighted_reviews(project, item_ids)
    prevalence, prevalence_n, weighted_open = _prevalence(
        item_ids,
        llm,
        reviewed.get(REVIEW_RANDOM),
        labels=labels,
        multi=multi,
        clusters=clusters,
        weighted=weighted,
    )

    baseline = None
    if len(gold.coders) >= 2:
        pairs = {}
        for a_name, b_name in combinations(gold.coders, 2):
            ids = [
                i
                for i in item_ids
                if gold.labels[a_name][i] is not None and gold.labels[b_name][i] is not None
            ]
            if ids:
                a, b = [gold.labels[a_name][i] for i in ids], [gold.labels[b_name][i] for i in ids]
                pairs[(a_name, b_name)] = agreement(a, b, ids)
        humans = [[gold.labels[c][i] for c in gold.coders] for i in item_ids]
        with_llm = [row + [llm[i]] for row, i in zip(humans, item_ids, strict=True)]
        level = "masi" if multi else "ordinal" if ordinal else "nominal"
        baseline = HumanBaseline(
            pairs=pairs,
            alpha=ag.krippendorff_alpha(humans, level, order if ordinal else None),
            fleiss=ag.fleiss_kappa(humans),
            ac1=ag.gwet_ac1(humans, None if multi else labels),
            alpha_with_llm=ag.krippendorff_alpha(with_llm, level, order if ordinal else None),
        )

    group_of = cfg.codebook.groups()
    groups = list(dict.fromkeys(g for c, g in group_of.items() if c != abstain))
    group_labels = groups + ([abstain] if abstain else [])

    def to_group(label: Label | None) -> Label | None:
        if label is None:
            return None
        if multi:
            return frozenset(group_of.get(c, c) for c in label)  # type: ignore[union-attr]
        return group_of.get(label, label)  # type: ignore[arg-type]

    group_comparisons, group_baseline = [], None
    if group_of:
        for name, ref in references:
            ids = [i for i in item_ids if llm[i] is not None and ref[i] is not None]
            if ids:
                g, p = [to_group(ref[i]) for i in ids], [to_group(llm[i]) for i in ids]
                agreement_set = _agreement_set(
                    g, p, [clusters[i] for i in ids] if clusters else None, labels=group_labels,
                    multi=multi, order=None, resamples=resamples, seed=seed,
                )  # fmt: skip
                group_comparisons.append(GroupComparison(name, agreement_set))
        if len(gold.coders) >= 2:
            humans = [[to_group(gold.labels[c][i]) for c in gold.coders] for i in item_ids]
            group_baseline = ag.krippendorff_alpha(humans, "masi" if multi else "nominal", None)

    alt = None
    if len(gold.coders) >= MIN_ANNOTATORS:
        alt = alt_test(
            [llm[i] for i in item_ids],
            {c: [gold.labels[c][i] for i in item_ids] for c in gold.coders},
            epsilon=cfg.gold.epsilon,
        )

    report = EvaluationReport(
        labels=labels,
        mode=cfg.mode,
        n_items=len(item_ids),
        n_scored=sum(1 for v in llm.values() if v is not None),
        gold=gold,
        comparisons=comparisons,
        baseline=baseline,
        stability=_stability(project, item_ids, panel, multi),
        alt_test=alt,
        resamples=resamples,
        seed=seed,
        clustered=clustered,
        n_clusters=len({clusters[i] for i in item_ids}) if clusters else None,
        subset=subset,
        has_split=bool(parts),
        instrument_hash=cfg.freeze.instrument_hash if cfg.freeze else None,
        freeze_intact=freeze_intact(project),
        test_evaluations=count_test_evaluations(project) + touches_test,
        test_exposures=count_test_exposures(project),
        subgroup=subgroup,
        model=model,
        prevalence=prevalence,
        prevalence_n=prevalence_n,
        prevalence_weighted_n=next(
            (r.weighted.n for r in prevalence if r.weighted is not None), None
        ),
        prevalence_weighted_open=weighted_open,
        differential=differential,
        differential_reference=main.reference if main else None,
        sensitivity=sensitivity_report(project, dict(references)[main.reference] if main else None),
        groups=groups,
        group_comparisons=group_comparisons,
        group_baseline=group_baseline,
        review_alpha=review_agreement(project, item_ids),
        n_examples=sum(1 for i in shown if subset == "all" or parts.get(i) == subset),
        dawid_skene=(
            _dawid_skene(
                project, panel, item_ids, labels, main.reference, dict(references)[main.reference]
            )
            if main and not multi
            else None
        ),
    )
    report.warnings.extend(_warnings(report))
    for t in differential:
        if t.p_holm < 0.05:
            # Ohne Ausprägungen: Sprecher und Metadaten können personenbezogen sein, und die
            # Warnungen gehen auch ins Replikationspaket ohne Rohtexte.
            rates = [rate for _, rate in t.levels.values()]
            low, high = f"{min(rates):.0%}", f"{max(rates):.0%}"
            report.warnings.append(
                Message(
                    f"Die Fehlerrate des LLM hängt womöglich von „{t.variable}“ ab (zwischen "
                    f"{low} und {high} je Ausprägung; p = {t.p_holm:.3f}, Holm-korrigiert). "
                    "Analysen nach dieser Variable mit den LLM-Codes wären verzerrt: "
                    "Kennwerte je Ausprägung prüfen und für Schätzungen menschliche Labels "
                    "einbeziehen (PPI, dsl).",
                    f"The LLM's error rate may depend on “{t.variable}” (between {low} and "
                    f"{high} per level; p = {t.p_holm:.3f}, Holm-adjusted). Analyses by this "
                    "variable with LLM codes would be biased: check the metrics per level and "
                    "include human labels in estimates (PPI, dsl).",
                )
            )
    report.warnings.extend(basis_warnings(project))
    report.warnings.extend(served_model_warnings(project, only=model))
    incomplete = sum(PANEL_INCOMPLETE in panel[i].flags for i in item_ids)
    if model is None and incomplete:
        report.warnings.append(
            Message(
                f"{incomplete} Einheiten mit unvollständigem Panel: Dort hat mindestens ein "
                "Modell kein gültiges Ergebnis, entschieden hat nur ein Teil des Panels. Lauf "
                "fortsetzen oder die Einheiten prüfen (Filter im Tab Ergebnisse).",
                f"{incomplete} units with an incomplete panel: at least one model has no valid "
                "result there, so only part of the panel decided. Resume the run or check "
                "these units (filter in the Results tab).",
            )
        )
    for s in report.sensitivity:
        if s.alpha < PROMPT_STABLE:
            code, spread = s.max_spread
            n, pp = len(s.wording_rows), f"{spread * 100:.0f}"
            report.warnings.append(
                Message(
                    f"{s.model}: Die Codes hängen am Wortlaut des Prompts (α über {n} "
                    f"Formulierungen {s.alpha:.2f}, unter {PROMPT_STABLE}; Anteil von {code} "
                    f"schwankt um {pp} Prozentpunkte). Schlüsse nur berichten, wenn sie in "
                    "allen Formulierungen halten."
                    + (
                        ""
                        if s.greedy
                        else " Die Formulierungen liefen mit der Temperatur des Anbieters; "
                        "α enthält deshalb auch Zufallsstreuung."
                    ),
                    f"{s.model}: the codes depend on the wording of the prompt (α across {n} "
                    f"wordings {s.alpha:.2f}, below {PROMPT_STABLE}; the share of {code} "
                    f"varies by {pp} percentage points). Only report conclusions that hold "
                    "across all wordings."
                    + (
                        ""
                        if s.greedy
                        else " The wordings ran at the provider's temperature, so α also "
                        "reflects sampling noise."
                    ),
                )
            )
    audit = project.store.audit()
    first_test = next((e for e in audit if e.event == TEST_EVALUATED), None)
    if first_test is not None and "reference_sha256" in first_test.details:
        changed = first_test.details["reference_sha256"] != test_reference
    else:  # ältere Protokolle ohne Prüfsumme: nur Gold-Nachträge erkennbar
        changed = first_test is not None and any(
            e.event == GOLD_UPDATED and e.ts > first_test.ts for e in audit
        )
    if changed:
        report.warnings.append(
            Message(
                "Die Referenz auf dem Test-Set hat sich seit der ersten Test-Auswertung "
                "geändert (Gold nachgetragen, Zuordnung oder Trennzeichen geändert). Eine "
                "Referenz, die nach Einsicht in Test-Ergebnisse angepasst wurde, entwertet den "
                "Test; die Änderung begründen.",
                "The reference on the test set has changed since the first test evaluation "
                "(gold added, mapping or separator changed). A reference adjusted after "
                "seeing test results invalidates the test; justify the change.",
            )
        )
    missing, drawn = random_sample_gaps(project, item_ids)
    if missing and any(c.reference == REVIEW_RANDOM for c in comparisons):
        report.warnings.append(
            Message(
                f"{missing} von {drawn} Einheiten der Zufallsstichprobe haben noch keine "
                "blinde Review. Die Stichprobe bleibt nur zufällig, wenn das Fehlen nichts mit "
                "den Einheiten zu tun hat; am besten die Queue vollständig abarbeiten.",
                f"{missing} of {drawn} units in the random sample have no blind review yet. "
                "The sample only stays random if the gaps are unrelated to the units; best "
                "complete the queue.",
            )
        )
    if n := report.prevalence_weighted_open:
        report.warnings.append(
            Message(
                f"Die gewichtete Prävalenz (alle Reviews) erscheint erst, wenn die "
                f"Review-Queue vollständig abgearbeitet ist; {n} Einheiten haben noch keine "
                "verwertbare blinde Review. Jede gezogene Einheit braucht ein Label, sonst "
                "ist die Gewichtung verzerrt.",
                f"The weighted prevalence (all reviews) appears once the review queue is "
                f"complete; {n} units still lack a usable blind review. Every drawn unit "
                "needs a label, otherwise the weighting is biased.",
            )
        )
    if touches_test:
        # Erst nach erfolgreicher Berechnung: Eine gescheiterte Auswertung hat nichts gezeigt.
        assert cfg.freeze is not None
        project.store.log(
            TEST_EVALUATED,
            subset=subset,
            instrument_hash=cfg.freeze.instrument_hash,
            freeze_intact=report.freeze_intact,
            n_items=len(items),
            reference_sha256=test_reference,
        )
    return report


def _reference_sha(gold: GoldTable, ids: list[str]) -> str:
    """Prüfsumme der zugeordneten Referenz auf diesen Einheiten."""

    def plain(label: Label | None) -> object:
        return sorted(label) if isinstance(label, frozenset) else label

    payload = {c: [plain(gold.labels[c].get(i)) for i in ids] for c in gold.columns}
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _llm_label(result: ItemResult, multi: bool) -> Label | None:
    if result.status != "ok" or not result.final:
        return None
    return frozenset(result.final) if multi else result.final[0]


def _agreement_set(
    a: list[Label],
    b: list[Label],
    clusters: list[Hashable] | None,
    *,
    labels: list[str],
    multi: bool,
    order: list[str] | None,
    resamples: int,
    seed: int,
) -> AgreementSet:
    a_arr, b_arr = np.array(a, dtype=object), np.array(b, dtype=object)
    cats = None if multi else labels
    level = "masi" if multi else "ordinal" if order else "nominal"

    def on(fn: Callable[[list, list], float]) -> Callable[[np.ndarray], float]:
        return lambda idx: fn(list(a_arr[idx]), list(b_arr[idx]))

    def boot(fn: Callable[[list, list], float], offset: int) -> Estimate:
        return bootstrap(on(fn), len(a), clusters=clusters, resamples=resamples, seed=seed + offset)

    pairs = lambda x, y: list(zip(x, y, strict=True))  # noqa: E731
    return AgreementSet(
        n=len(a),
        percent=boot(lambda x, y: ag.percent_agreement(pairs(x, y)), 1),
        kappa=boot(lambda x, y: ag.cohen_kappa(x, y, categories=cats), 2),
        ac1=boot(lambda x, y: ag.gwet_ac1(pairs(x, y), cats), 3),
        alpha=boot(lambda x, y: ag.krippendorff_alpha(pairs(x, y), level, order), 4),
        scott_pi=boot(lambda x, y: ag.scott_pi(x, y), 5),
        brennan_prediger=boot(lambda x, y: ag.brennan_prediger(pairs(x, y), cats), 6),
        kappa_weighted=boot(
            lambda x, y: ag.cohen_kappa(x, y, weights="quadratic", categories=order), 7
        )
        if order
        else None,
    )


def _compare(
    name: str,
    ids: list[str],
    g: list[Label],
    p: list[Label],
    by_item: dict[str, ItemResult],
    agreement: AgreementSet,
    *,
    multi: bool,
    labels: list[str],
    k: int,
    abstain: str | None,
    clusters: dict[str, Hashable] | None,
    resamples: int,
    seed: int,
) -> LLMComparison:
    g_arr, p_arr = np.array(g, dtype=object), np.array(p, dtype=object)
    cluster_list = [clusters[i] for i in ids] if clusters else None

    def boot(fn: Callable[[list, list], float], offset: int) -> Estimate:
        return bootstrap(
            lambda idx: fn(list(g_arr[idx]), list(p_arr[idx])), len(g),
            clusters=cluster_list, resamples=resamples, seed=seed + offset,
        )  # fmt: skip

    calibration: dict[str, CalibrationReport] = {}
    binary = {
        c: ([c in s for s in g], [c in s for s in p])
        if multi
        else ([x == c for x in g], [y == c for y in p])
        for c in labels
    }
    per_code = {
        "code_kappa": {c: ag.cohen_kappa(*pair) for c, pair in binary.items()},
        "rho": {c: rho(*pair, seed=seed) for c, pair in binary.items()},
    }
    if multi:
        report = multi_label_report(g, p, labels)
        macro = boot(lambda x, y: multi_label_report(x, y, labels).macro_f1, 11)
        code_shares = {
            c: Share(sum(c in s for s in g) / len(g), sum(c in s for s in p) / len(p))
            for c in labels
        }
        tests = {c: mcnemar([c in s for s in g], [c in s for s in p]) for c in labels}
        # Kalibrierung je (Einheit, gelisteter Code): trifft der Code laut Referenz zu?
        pairs = [
            (score, score.code in ref)
            for i, ref in zip(ids, g, strict=True)
            for score in by_item[i].ranking
        ]
        for key in ("p_sc", "p_verbal"):
            calibration[key] = calibration_report(
                [getattr(s, key) for s, _ in pairs], [hit for _, hit in pairs]
            )
        if any(len(by_item[i].votes) > 1 for i in ids):
            panel = [
                (_panel_share(by_item[i], score.code), score.code in ref)
                for i, ref in zip(ids, g, strict=True)
                for score in by_item[i].ranking
            ]
            calibration["panel"] = calibration_report(
                [s for s, _ in panel], [hit for _, hit in panel]
            )
        low = [c for c, s in report.per_class.items() if s.support < MIN_SUPPORT]
        return LLMComparison(
            name,
            agreement,
            None,
            macro,
            None,
            report,
            {},
            None,
            code_shares,
            None,
            tests,
            calibration,
            low,
            **per_code,
        )

    report = single_label_report(g, p, labels)
    rankings = [[s.code for s in by_item[i].ranking] for i in ids]
    correct = [x == y for x, y in zip(g, p, strict=True)]
    for key in ("p_sc", "p_verbal"):
        calibration[key] = calibration_report(
            [getattr(by_item[i].ranking[0], key) for i in ids], correct
        )
    if any(len(by_item[i].votes) > 1 for i in ids):
        calibration["panel"] = calibration_report(
            [_panel_share(by_item[i], by_item[i].ranking[0].code) for i in ids], correct
        )
    return LLMComparison(
        reference=name,
        agreement=agreement,
        accuracy=boot(lambda x, y: single_label_report(x, y, labels).accuracy, 10),
        macro_f1=boot(lambda x, y: single_label_report(x, y, labels).macro_f1, 11),
        single=report,
        multi=None,
        top_k={
            n: bootstrap(
                lambda idx, n=n: top_k_accuracy([g[i] for i in idx], [rankings[i] for i in idx], n),
                len(g),
                clusters=cluster_list,
                resamples=resamples,
                seed=seed + 20 + n,
            )  # fmt: skip
            for n in range(2, k + 1)
        },
        selective=selective_report(g, p, abstain) if abstain else None,
        shares=shares(g, p, labels),
        stuart_maxwell=stuart_maxwell(g, p, labels),
        mcnemar={c: mcnemar([x == c for x in g], [y == c for y in p]) for c in labels},
        calibration=calibration,
        low_support=[c for c, s in report.per_class.items() if s.support < MIN_SUPPORT],
        by_runs=_by_runs([by_item[i] for i in ids], correct),
        mcc=boot(lambda x, y: single_label_report(x, y, labels).mcc, 12),
        **per_code,
    )


def _panel_share(result: ItemResult, code: str) -> float:
    """Anteil der Panel-Modelle, deren Entscheidung den Code enthält."""
    votes = result.votes.values()
    return sum(code in v for v in votes) / len(votes) if votes else 0.0


def _by_runs(results: list[ItemResult], correct: list[bool]) -> list[tuple[str, int, float]]:
    """Trefferquote je Übereinstimmungsstufe. Mit einem Modell als k/n der gültigen Läufe:
    3/3 aus dem adaptiven Abbruch ist nicht dasselbe wie 5/5, obwohl beide 1,0 ergeben."""
    single_model = all(len(r.votes) <= 1 for r in results)
    groups: dict[tuple[int, float], list[bool]] = {}
    for r, hit in zip(results, correct, strict=True):
        key = (r.n_valid, round(r.ranking[0].p_sc, 2))
        groups.setdefault(key, []).append(hit)
    rows = []
    for (n, p), hits in sorted(groups.items(), key=lambda kv: (-kv[0][1], -kv[0][0])):
        label = f"{round(p * n)}/{n}" if single_model else f"{p:.2f} (N {n})"
        rows.append((label, len(hits), sum(hits) / len(hits)))
    return rows


def _prevalence(
    item_ids: list[str],
    llm: dict[str, Label | None],
    sample: dict[str, Label | None] | None,
    *,
    labels: list[str],
    multi: bool,
    clusters: dict[str, Hashable] | None = None,
    weighted: WeightedReviews | None = None,
) -> tuple[list[PrevalenceRow], tuple[int, int] | None, int]:
    """PPI braucht eine zufällig gezogene Gold-Stichprobe: die blinden Reviews der
    Zufallsstichprobe. Per Gate gewählte Reviews sind keine Zufallsauswahl und bleiben außen
    vor; Einheiten ohne gültiges LLM-Ergebnis ebenso."""
    if sample is None:
        return [], None, 0
    scored = [i for i in item_ids if llm[i] is not None]
    labeled = [i for i in scored if sample[i] is not None]
    unlabeled = [i for i in scored if sample[i] is None]
    if len(labeled) < 2 or len(unlabeled) < 2:
        return [], None, 0

    def has(label: Label | None, code: str) -> bool:
        return code in label if multi else label == code  # type: ignore[operator]

    cl = [clusters[i] for i in labeled] if clusters else None
    cu = [clusters[i] for i in unlabeled] if clusters else None
    # Das Gewicht 1/π setzt voraus, dass jede gezogene Einheit ein Label hat: sonst keine
    # gewichtete Schätzung (eine offene Queue verzerrt sie um Prozentpunkte).
    open_ = [
        i for i in scored if weighted and i in weighted.probability and weighted.labels[i] is None
    ]
    usable = (
        weighted is not None
        and not open_
        and sum(weighted.labels[i] is not None for i in scored) >= 2
    )
    rows = []
    for code in labels:
        y = [has(sample[i], code) for i in labeled]
        ppi, lam = ppi_plus_mean(
            [has(llm[i], code) for i in unlabeled], [has(llm[i], code) for i in labeled], y,
            clusters_unlabeled=cu, clusters_labeled=cl,
        )  # fmt: skip
        est, w_lam = None, None
        if usable and weighted is not None:
            est, w_lam = weighted_mean(
                [has(llm[i], code) for i in scored],
                [None if (g := weighted.labels[i]) is None else has(g, code) for i in scored],
                [weighted.probability.get(i) for i in scored],
                clusters=[clusters[i] for i in scored] if clusters else None,
            )
        rows.append(
            PrevalenceRow(
                code,
                sum(has(llm[i], code) for i in scored) / len(scored),
                classical_mean(y, clusters=cl),
                ppi,
                lam,
                est,
                w_lam,
            )
        )
    return rows, (len(labeled), len(unlabeled)), len(open_)


def reference_labels(project: Project, items: list[Item]) -> tuple[str | None, dict]:
    """Referenz der Abweichungsansicht wie der Hauptvergleich: Konsens, sonst Mehrheit der
    Kodierenden, sonst die erste Gold-Spalte. (Name, Einheit → Label) oder (None, {})."""
    gold = build_gold(project.config, project.store.units(), items)
    ids = [i.item_id for i in items]
    if gold.consensus:
        return gold.consensus, gold.labels[gold.consensus]
    if len(gold.coders) >= 2:
        return MAJORITY, {i: ag.majority([gold.labels[c][i] for c in gold.coders]) for i in ids}
    if gold.coders:
        return gold.coders[0], gold.labels[gold.coders[0]]
    return None, {}


def main_comparison(
    comparisons: list[LLMComparison], consensus: str | None
) -> LLMComparison | None:
    """Hauptvergleich: Konsens, sonst Mehrheit, sonst Review-Stichprobe, sonst der erste."""
    names = {c.reference: c for c in comparisons}
    for name in (consensus, MAJORITY, REVIEW_RANDOM):
        if name in names:
            return names[name]
    return comparisons[0] if comparisons else None


def _differential(
    cfg: ProjectConfig,
    units: list[Unit],
    items: list[Item],
    reference: dict[str, Label | None],
    llm: dict[str, Label | None],
    clusters: dict[str, Hashable] | None,
) -> list[DifferentialTest]:
    """Fehlerrate gegen die Hauptreferenz je Subgruppen-Variable, Holm-korrigiert.

    Die Gruppe selbst entfällt, wenn nach ihr geclustert wird: jede Ausprägung wäre ein
    eigener Cluster."""
    ids = [i for i in reference if llm[i] is not None and reference[i] is not None]
    errors = [llm[i] != reference[i] for i in ids]
    tests = []
    for key, name in subgroup_options(cfg):
        if key == "group" and clusters:
            continue
        values = subgroup_values(units, items, key)
        test = error_rate_test(
            name, errors, [values[i] for i in ids], [clusters[i] for i in ids] if clusters else None
        )
        if test is not None:
            tests.append(test)
    adjusted = holm([t.p_value for t in tests])
    return [replace(t, p_holm=p) for t, p in zip(tests, adjusted, strict=True)]


def _subgroup_rows(
    comparison: LLMComparison,
    references: list[tuple[str, dict[str, Label | None]]],
    llm: dict[str, Label | None],
    values: dict[str, str],
    *,
    labels: list[str],
    multi: bool,
    clusters: dict[str, Hashable] | None,
    resamples: int,
    seed: int,
) -> list[SubgroupRow]:
    ref = dict(references)[comparison.reference]
    cats = None if multi else labels
    rows = []
    for value in sorted({values[i] for i in ref if llm[i] is not None and ref[i] is not None}):
        ids = [i for i in ref if values[i] == value and llm[i] is not None and ref[i] is not None]
        g, p = [ref[i] for i in ids], [llm[i] for i in ids]
        g_arr, p_arr = np.array(g, dtype=object), np.array(p, dtype=object)
        kappa = bootstrap(
            lambda idx, g_arr=g_arr, p_arr=p_arr: ag.cohen_kappa(
                list(g_arr[idx]), list(p_arr[idx]), categories=cats
            ),
            len(g), clusters=[clusters[i] for i in ids] if clusters else None,
            resamples=resamples, seed=seed + 21,
        )  # fmt: skip
        pairs = list(zip(g, p, strict=True))
        if multi:
            report = multi_label_report(g, p, labels)
            accuracy, macro = report.exact_match, report.macro_f1
            code_shares = {
                c: Share(sum(c in s for s in g) / len(g), sum(c in s for s in p) / len(p))
                for c in labels
            }
        else:
            single = single_label_report(g, p, labels)
            accuracy, macro, code_shares = single.accuracy, single.macro_f1, shares(g, p, labels)
        rows.append(
            SubgroupRow(
                value,
                len(ids),
                ag.percent_agreement(pairs),
                kappa,
                ag.gwet_ac1(pairs, cats),
                accuracy,
                macro,
                code_shares,
            )  # fmt: skip
        )
    return rows


def _stability(
    project: Project, item_ids: list[str], by_item: dict[str, ItemResult], multi: bool
) -> Stability:
    mode = project.config.mode
    level = "masi" if multi else "nominal"
    basis = result_basis(project)  # dieselben Läufe wie die Ergebnisse
    intra: dict[str, float] = {}
    for key, config_hash in basis.runs.items():
        runs: dict[str, dict[int, Label]] = {}
        for call in project.store.calls(model_key=key, config_hash=config_hash, with_raw=False):
            if call.status == "ok" and call.answer:
                d = decision(call.answer, mode)
                runs.setdefault(call.item_id, {})[call.run] = frozenset(d) if multi else d[0]
        width = max((max(r) + 1 for r in runs.values()), default=0)
        matrix = [[runs.get(i, {}).get(r) for r in range(width)] for i in item_ids]
        intra[key] = ag.krippendorff_alpha(matrix, level)
    inter = None
    if len(basis.runs) >= 2:
        matrix = [[_vote(by_item[i].votes.get(m), multi) for m in basis.runs] for i in item_ids]
        inter = ag.krippendorff_alpha(matrix, level)
    return Stability(intra, inter)


def _model_shares(project: Project, item_ids: list[str], labels: list[str]) -> dict[str, dict]:
    """Je Einheit und Modell der Anteil der Läufe je Code, aus denselben Läufen wie die
    Ergebnisse (Single-Label). So vergleicht Dawid-Skene mit dem gleich gewichteten Mittel der
    Self-Consistency, nicht mit harten Mehrheiten je Modell."""
    wanted, allowed = set(item_ids), set(labels)
    counts: dict[str, dict[str, Counter[str]]] = {}
    for key, config_hash in result_basis(project).runs.items():
        for call in project.store.calls(model_key=key, config_hash=config_hash, with_raw=False):
            if call.item_id in wanted and call.status == "ok" and call.answer:
                code = decision(call.answer, "single")[0]
                if code in allowed:
                    counts.setdefault(call.item_id, {}).setdefault(key, Counter())[code] += 1
    return {
        item: {m: {c: n / tally.total() for c, n in tally.items()} for m, tally in models.items()}
        for item, models in counts.items()
    }


def _dawid_skene(
    project: Project,
    panel: dict[str, ItemResult],
    item_ids: list[str],
    labels: list[str],
    name: str,
    reference: dict[str, Label | None],
) -> DawidSkeneCheck | None:
    """Immer über das ganze Panel, auch wenn die Auswertung nur ein Modell zeigt."""
    shares = _model_shares(project, item_ids, labels)
    ids = [i for i in item_ids if panel[i].status == "ok" and panel[i].final and i in shares]
    votes = [shares[i] for i in ids]
    if len({m for row in votes for m in row}) < MIN_DS_MODELS:
        return None
    ds = dawid_skene(votes, labels)
    weighted = dict(zip(ids, ds.labels, strict=True))
    scored = [i for i in ids if reference.get(i) is not None]
    ref = [reference[i] for i in scored]
    return DawidSkeneCheck(
        reference=name,
        accuracy=ds.accuracy,
        n=len(ids),
        changed=sum(weighted[i] != panel[i].final[0] for i in ids),
        n_reference=len(scored),
        kappa_panel=ag.cohen_kappa(ref, [panel[i].final[0] for i in scored]),
        kappa_weighted=ag.cohen_kappa(ref, [weighted[i] for i in scored]),
    )


def _vote(vote: Sequence[str] | None, multi: bool) -> Label | None:
    if not vote:
        return None
    return frozenset(vote) if multi else vote[0]


def _warnings(report: EvaluationReport) -> list[str]:
    warnings: list[str] = []
    if not report.has_split:
        warnings.append(
            Message(
                "Kein Dev/Test-Split: Wer Codebuch oder Prompt anhand dieser Kennwerte verändert, "
                "darf sie nicht zugleich als unabhängige Validierung berichten.",
                "No dev/test split: if codebook or prompt are changed based on these metrics, they "
                "cannot also be reported as an independent validation.",
            )
        )
    if n := report.n_examples:
        warnings.append(
            Message(
                f"{n} Beispiel-Einheiten aus Dev stehen mit Gold im Prompt und fehlen in allen "
                "Kennwerten.",
                f"{n} example units from dev appear with their gold in the prompt and are left out "
                "of all metrics.",
            )
        )
    if report.freeze_intact is False:
        warnings.append(
            Message(
                "Der Freeze ist gebrochen: Die Konfiguration entspricht nicht mehr dem "
                "eingefrorenen Instrument.",
                "The freeze is broken: the configuration no longer matches the frozen instrument.",
            )
        )
    if n := report.test_exposures:
        warnings.append(
            Message(
                f"Vor dem Freeze wurden {n}-mal LLM-Ergebnisse zu Test-Einheiten exportiert. Wer "
                "daraus Kennwerte außerhalb des Programms berechnet hat, hat die Test-Sperre "
                "umgangen; das gehört in den Methodenteil.",
                f"LLM results for test units were exported {n} time(s) before the freeze. Metrics "
                "computed from them outside the program bypass the test lock; report this in the "
                "methods section.",
            )
        )
    if (n := report.test_evaluations) > 1:
        warnings.append(
            Message(
                f"Das Test-Set wurde bisher {n}-mal ausgewertet; das gehört in den Methodenteil.",
                f"The test set has been evaluated {n} times so far; report this in the methods "
                "section.",
            )
        )
    if report.gold.unmapped:
        common = report.gold.unmapped.most_common(10)
        warnings.append(
            Message(
                "Gold-Werte ohne Zuordnung zu einem Code (nicht ausgewertet): "
                + ", ".join(f"„{v}“ ({n}×)" for v, n in common),
                "Gold values not mapped to a code (not evaluated): "
                + ", ".join(f"“{v}” ({n}×)" for v, n in common),
            )
        )
    if n := len(report.gold.inconsistent):
        warnings.append(
            Message(
                f"{n} Gruppen mit uneinheitlichem Gold im holistischen Modus (nicht ausgewertet).",
                f"{n} groups with inconsistent gold in holistic mode (not evaluated).",
            )
        )
    if report.n_clusters is not None and report.n_clusters < MIN_CLUSTERS:
        warnings.append(
            Message(
                f"Der Bootstrap zieht ganze Gruppen, es gibt aber nur {report.n_clusters}. "
                f"Unter etwa {MIN_CLUSTERS} Gruppen sind die Konfidenzintervalle zu schmal.",
                f"The bootstrap resamples whole groups, but there are only {report.n_clusters}. "
                f"With fewer than about {MIN_CLUSTERS} groups the confidence intervals are too "
                "narrow.",
            )
        )
    if missing := report.n_items - report.n_scored:
        warnings.append(
            Message(
                f"{missing} Einheiten ohne gültiges LLM-Ergebnis fehlen in allen Vergleichen.",
                f"{missing} units without a valid LLM result are missing from all comparisons.",
            )
        )
    for comparison in report.comparisons:
        name, name_en = comparison.reference, reference_en(comparison.reference)
        if (n := comparison.agreement.n) < MIN_ITEMS:
            warnings.append(
                Message(
                    f"{name}: nur {n} Einheiten; Kennwerte sind instabil.",
                    f"{name_en}: only {n} units; the metrics are unstable.",
                )
            )
        if comparison.low_support:
            codes = ", ".join(comparison.low_support)
            warnings.append(
                Message(
                    f"{name}: Codes mit weniger als {MIN_SUPPORT} Referenzfällen ({codes}); deren "
                    "Kennwerte vorsichtig deuten.",
                    f"{name_en}: codes with fewer than {MIN_SUPPORT} reference cases ({codes}); "
                    "interpret their metrics with caution.",
                )
            )
    if any(c.reference == REVIEW_ALL for c in report.comparisons):
        warnings.append(
            Message(
                f"„{REVIEW_ALL}“ enthält per Gate gewählte, also gezielt schwierige Einheiten; "
                "die Kennwerte dagegen sind nach unten verzerrt. Unverzerrt ist nur der Vergleich "
                "mit der Zufallsstichprobe.",
                f"“{reference_en(REVIEW_ALL)}” contains units selected by the gate, i.e. "
                "deliberately difficult ones; metrics against it are biased downwards. Only the "
                "comparison with the random sample is unbiased.",
            )
        )
    # Ohne die Werte selbst: Sie können Namen sein und gingen mit metrics.json ins Paket.
    small = {r.value for c in report.comparisons for r in c.subgroups if r.n < MIN_ITEMS}
    if small:
        warnings.append(
            Message(
                f"{len(small)} Subgruppen mit weniger als {MIN_ITEMS} Einheiten (in der Tabelle "
                "markiert): Kennwerte dort sind instabil; Unterschiede zwischen Subgruppen nicht "
                "überdeuten.",
                f"{len(small)} subgroups with fewer than {MIN_ITEMS} units (marked in the table): "
                "their metrics are unstable; do not over-interpret differences between subgroups.",
            )
        )
    if report.alt_test is None and len(report.gold.coders) < MIN_ANNOTATORS:
        warnings.append(
            Message(
                f"Alt-Test nicht möglich: er braucht mindestens {MIN_ANNOTATORS} Kodierende auf "
                "denselben Einheiten.",
                f"Alternative annotator test not possible: it needs at least {MIN_ANNOTATORS} "
                "coders on the same units.",
            )
        )
    return warnings
