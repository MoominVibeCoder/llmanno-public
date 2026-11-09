"""Kennwerte als XLSX: Langformat (eine Zeile pro Kennwert) plus Detailblätter."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import openpyxl

from llmanno.core.evaluation import SUBSET_LABELS, AgreementSet, EvaluationReport
from llmanno.core.export.xlsx import append_row
from llmanno.core.metrics.bootstrap import Estimate
from llmanno.core.metrics.rho import THRESHOLD as RHO_THRESHOLD


def _round(value: float | None) -> float | None:
    return None if value is None or math.isnan(value) else round(value, 4)


def metric_rows(report: EvaluationReport) -> list[list[Any]]:
    """[Bereich, Vergleich, Kennwert, Wert, KI unten, KI oben, n]"""
    rows: list[list[Any]] = []

    def add(section: str, comparison: str, metric: str, value: Any, n: int | None = None) -> None:
        if isinstance(value, Estimate):
            rows.append([section, comparison, metric, value.value, value.low, value.high, value.n])
        elif value is not None:
            rows.append([section, comparison, metric, value, None, None, n])

    def agreement(section: str, name: str, a: AgreementSet) -> None:
        add(section, name, "Prozentuale Übereinstimmung", a.percent)
        add(section, name, "Cohens Kappa", a.kappa)
        add(section, name, "Gewichtetes Kappa (quadratisch)", a.kappa_weighted)
        add(section, name, "Scotts Pi", a.scott_pi)
        add(section, name, "Gwets AC1", a.ac1)
        add(section, name, "Brennan-Prediger", a.brennan_prediger)
        add(section, name, "Krippendorffs Alpha", a.alpha)

    for c in report.comparisons:
        name = f"LLM vs. {c.reference}"
        agreement("Übereinstimmung", name, c.agreement)
        n = c.agreement.n
        add("Klassifikation", name, "Accuracy", c.accuracy)
        add("Klassifikation", name, "Macro-F1", c.macro_f1)
        if c.single:
            add("Klassifikation", name, "Balanced Accuracy", c.single.balanced_accuracy, n)
            add("Klassifikation", name, "Weighted-F1", c.single.weighted_f1, n)
            add("Klassifikation", name, "MCC", c.mcc or c.single.mcc, n)
        if c.multi:
            add("Klassifikation", name, "Micro-F1", c.multi.micro_f1, n)
            add("Klassifikation", name, "Hamming-Loss", c.multi.hamming_loss, n)
            add("Klassifikation", name, "Exact Match", c.multi.exact_match, n)
            add("Klassifikation", name, "Jaccard", c.multi.jaccard, n)
        for k, value in c.top_k.items():
            add("Klassifikation", name, f"Top-{k}-Accuracy", value, n)
        if c.selective:
            add("Klassifikation", name, "Abstain-Rate", c.selective.abstain_rate, n)
            add("Klassifikation", name, "Selektive Accuracy", c.selective.selective_accuracy, n)
        if c.stuart_maxwell:
            add("Prävalenz", name, "Stuart-Maxwell χ²", c.stuart_maxwell.statistic, n)
            add("Prävalenz", name, "Stuart-Maxwell p", c.stuart_maxwell.p_value, n)
        for key, cal in c.calibration.items():
            add("Kalibrierung", name, f"ECE ({key})", cal.ece, cal.n)
            add("Kalibrierung", name, f"Brier ({key})", cal.brier, cal.n)
            add("Kalibrierung", name, f"AUROC ({key})", cal.auroc, cal.n)
            add("Kalibrierung", name, f"AURC ({key})", cal.aurc, cal.n)
            add("Kalibrierung", name, f"ECE rekalibriert ({key})", cal.ece_recalibrated, cal.n)
        for label, n, acc in c.by_runs:
            add("Kalibrierung", name, f"Trefferquote bei {label}", acc, n)

    for g in report.group_comparisons:
        agreement("Code-Gruppen", f"LLM vs. {g.reference}", g.agreement)
    add("Code-Gruppen", "alle Kodierenden", "Krippendorffs Alpha", report.group_baseline)

    if report.baseline:
        for (a, b), values in report.baseline.pairs.items():
            agreement("Mensch-Mensch", f"{a} vs. {b}", values)
        add("Mensch-Mensch", "alle Kodierenden", "Krippendorffs Alpha", report.baseline.alpha)
        add("Mensch-Mensch", "alle Kodierenden", "Fleiss' Kappa", report.baseline.fleiss)
        add("Mensch-Mensch", "alle Kodierenden", "Gwets AC1", report.baseline.ac1)
        add(
            "Mensch-Mensch",
            "alle Kodierenden + LLM",
            "Krippendorffs Alpha",
            report.baseline.alpha_with_llm,
        )

    if report.review_alpha:
        alpha, n = report.review_alpha
        add("Mensch-Mensch", "blinde Reviews", "Krippendorffs Alpha", alpha, n)

    for model, alpha in report.stability.intra_alpha.items():
        add("Stabilität", model, "Alpha über Läufe", alpha)
    add("Stabilität", "Panel", "Alpha über Modelle", report.stability.inter_alpha)
    if ds := report.dawid_skene:
        for model, accuracy in ds.accuracy.items():
            add("Dawid-Skene", model, "geschätzte Trefferquote (ohne Gold)", accuracy, ds.n)
        name = f"LLM vs. {ds.reference}"
        add("Dawid-Skene", name, "Einheiten gewichtet anders kodiert", ds.changed, ds.n)
        add("Dawid-Skene", name, "Cohens Kappa, gleich gewichtet", ds.kappa_panel, ds.n_reference)
        add("Dawid-Skene", name, "Cohens Kappa, Dawid-Skene", ds.kappa_weighted, ds.n_reference)

    if report.alt_test:
        alt = report.alt_test
        add("Alt-Test", f"ε = {alt.epsilon}", "Winning Rate ω", alt.winning_rate)
        add("Alt-Test", f"ε = {alt.epsilon}", "Advantage Probability", alt.advantage_probability)
        add("Alt-Test", f"ε = {alt.epsilon}", "bestanden (ω ≥ 0,5)", "ja" if alt.passed else "nein")
    return rows


def export_evaluation(report: EvaluationReport, path: Path) -> None:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Kennwerte"
    append_row(sheet, ["Bereich", "Vergleich", "Kennwert", "Wert", "KI unten", "KI oben", "n"])
    for row in metric_rows(report):
        append_row(sheet, [round(v, 4) if isinstance(v, float) else v for v in row])
    sheet.freeze_panes = "A2"

    per_code = workbook.create_sheet("Je Code")
    append_row(
        per_code,
        [
            "Vergleich",
            "Code",
            "Precision",
            "Recall",
            "F1",
            "Referenz n",
            "LLM n",
            "Anteil Referenz",
            "Anteil LLM",
            "McNemar p",
            "Cohens κ je Code (ja/nein)",
            f"ρ (κ > {RHO_THRESHOLD})",
        ],
    )
    for c in report.comparisons:
        scores = (c.single or c.multi).per_class
        for code, s in scores.items():
            share = c.shares.get(code)
            test = c.mcnemar.get(code)
            append_row(
                per_code,
                [
                    f"LLM vs. {c.reference}",
                    code,
                    round(s.precision, 4),
                    round(s.recall, 4),
                    round(s.f1, 4),
                    s.support,
                    s.predicted,
                    share and round(share.gold, 4),
                    share and round(share.llm, 4),
                    test and round(test.p_value, 4),
                    _round(c.code_kappa.get(code)),
                    _round(c.rho.get(code)),
                ],
            )

    confusion = workbook.create_sheet("Konfusion")
    for c in report.comparisons:
        if not c.single:
            continue
        labels = c.single.labels
        append_row(confusion, [f"LLM vs. {c.reference}: Zeilen = Referenz, Spalten = LLM"])
        append_row(confusion, ["", *labels])
        for label, row in zip(labels, c.single.confusion.tolist(), strict=True):
            append_row(confusion, [label, *row])
        append_row(confusion, [])

    calibration = workbook.create_sheet("Kalibrierung")
    append_row(
        calibration,
        ["Vergleich", "Konfidenz", "von", "bis", "n", "mittlere Konfidenz", "Trefferquote"],
    )
    for c in report.comparisons:
        for key, cal in c.calibration.items():
            for b in cal.bins:
                append_row(
                    calibration,
                    [
                        f"LLM vs. {c.reference}",
                        key,
                        b.low,
                        b.high,
                        b.n,
                        round(b.mean_confidence, 4),
                        round(b.accuracy, 4),
                    ],
                )

    if report.alt_test:
        alt = workbook.create_sheet("Alt-Test")
        append_row(
            alt,
            [
                "Kodierperson",
                "n",
                "LLM gleich gut oder besser",
                "Person gleich gut oder besser",
                "p",
                "p (BY)",
                "H0 verworfen",
                "Test",
            ],
        )
        for a in report.alt_test.annotators:
            append_row(
                alt,
                [
                    a.annotator,
                    a.n,
                    round(a.rho_llm, 4),
                    round(a.rho_human, 4),
                    round(a.p_value, 6),
                    round(a.p_adjusted, 6),
                    a.rejected,
                    a.test,
                ],
            )

    if report.prevalence:
        sheet = workbook.create_sheet("Prävalenz PPI")
        append_row(
            sheet,
            ["Code", "LLM naiv", "Stichprobe", "KI unten", "KI oben", "PPI++", "KI unten",
             "KI oben", "lambda"]
            + (["Alle Reviews gewichtet", "KI unten", "KI oben", "lambda gewichtet"]
               if report.prevalence_weighted_n else []),
        )  # fmt: skip
        for r in report.prevalence:
            values = [r.llm, r.sample.value, r.sample.low, r.sample.high]
            values += [r.ppi.value, r.ppi.low, r.ppi.high, r.ppi_lambda]
            if r.weighted is not None and r.weighted_lambda is not None:
                values += [r.weighted.value, r.weighted.low, r.weighted.high, r.weighted_lambda]
            append_row(sheet, [r.code, *(round(v, 4) for v in values)])
        labeled, unlabeled = report.prevalence_n or (0, 0)
        append_row(sheet, [])
        append_row(
            sheet,
            [f"Gelabelte Zufallsstichprobe: {labeled}, ungelabelt: {unlabeled}. "
             "PPI++ mit Power-Tuning (Angelopoulos, Duchi & Zrnic 2023), 95-%-KI per "
             f"Normalapproximation{', cluster-robust über Gruppen' if report.clustered else ''}."],
        )  # fmt: skip
        if report.prevalence_weighted_n:
            append_row(
                sheet,
                [f"Alle Reviews gewichtet: {report.prevalence_weighted_n} blinde Reviews (Gate "
                 "und Zufallsstichprobe), gewichtet mit der protokollierten Ziehwahrscheinlichkeit "
                 "und Power-Tuning (confidence-driven inference, Gligorić et al. 2025)."],
            )  # fmt: skip

    if report.sensitivity:
        sheet = workbook.create_sheet("Sensitivität")
        append_row(
            sheet,
            ["Modell", "alpha inter-prompt", "Variante", "im alpha", "n", "gleich wie Original",
             "alpha gegen Referenz", "Token Ausgabe (Mittel)", "Sekunden (Mittel)",
             *report.labels],
        )  # fmt: skip
        for s in report.sensitivity:
            for r in s.rows:
                append_row(
                    sheet,
                    [s.model, round(s.alpha, 4), r.variant, r.wording, r.n,
                     round(r.agreement, 4),
                     None if r.alpha_reference is None else round(r.alpha_reference, 4),
                     None if r.tokens_out is None else round(r.tokens_out, 1),
                     None if r.seconds is None else round(r.seconds, 2),
                     *(round(r.shares.get(c, 0.0), 4) for c in report.labels)],
                )  # fmt: skip

    if report.differential:
        sheet = workbook.create_sheet("Messfehler")
        append_row(
            sheet,
            ["Variable", "Ausprägung", "n", "Fehlerrate", "F", "df1", "df2", "p",
             "p Holm", "cluster-robust"],
        )  # fmt: skip
        for t in report.differential:
            for value, (n, rate) in t.levels.items():
                append_row(
                    sheet,
                    [t.variable, value, n, round(rate, 4), round(t.statistic, 4), *t.df,
                     round(t.p_value, 6), round(t.p_holm, 6), t.clustered],
                )  # fmt: skip
        append_row(sheet, [])
        append_row(
            sheet,
            [f"Fehler gegen {report.differential_reference}; Wald-Test im linearen "
             "Wahrscheinlichkeitsmodell (Egami et al. 2023), Holm-korrigiert."],
        )  # fmt: skip

    if any(c.subgroups for c in report.comparisons):
        _subgroup_sheet(workbook.create_sheet("Subgruppen"), report)

    notes = workbook.create_sheet("Hinweise")
    append_row(notes, ["Erstellt", datetime.now(UTC).isoformat(timespec="seconds")])
    append_row(
        notes,
        [
            "Bootstrap",
            f"{report.resamples} Ziehungen, Seed {report.seed}, "
            f"{'nach Gruppen' if report.clustered else 'über Einheiten'}, 95-%-Perzentil-KI",
        ],
    )
    append_row(
        notes, ["Einheiten", f"{report.n_scored} von {report.n_items} mit gültigem LLM-Ergebnis"]
    )
    append_row(notes, ["Teilmenge", SUBSET_LABELS[report.subset]])
    append_row(notes, ["LLM", report.model or "Panel (alle Modelle, gleich gewichtet)"])
    if report.instrument_hash:
        intact = "intakt" if report.freeze_intact else "gebrochen"
        append_row(notes, ["Instrument-Hash", f"{report.instrument_hash} (Freeze {intact})"])
    if report.has_split:
        append_row(notes, ["Test-Auswertungen", report.test_evaluations])
    for warning in report.warnings:
        append_row(notes, ["Warnung", warning])
    notes.column_dimensions["B"].width = 120
    workbook.save(path)


def _subgroup_sheet(sheet, report: EvaluationReport) -> None:
    accuracy = "Exact Match" if report.mode == "multi" else "Accuracy"
    append_row(
        sheet,
        ["Vergleich", "Variable", "Ausprägung", "n", "% Übereinstimmung", "Cohens Kappa"]
        + ["KI unten", "KI oben", "Gwets AC1", accuracy, "Macro-F1"]
        + [f"Anteil {who} {c}" for c in report.labels for who in ("Referenz", "LLM")],
    )
    for c in report.comparisons:
        for r in c.subgroups:
            shares = [
                round(getattr(r.shares[code], side), 4) if code in r.shares else None
                for code in report.labels
                for side in ("gold", "llm")
            ]
            values = [r.percent, r.kappa.value, r.kappa.low, r.kappa.high, r.ac1]
            append_row(
                sheet,
                [f"LLM vs. {c.reference}", report.subgroup, r.value, r.n]
                + [round(v, 4) for v in values]
                + [round(r.accuracy, 4), round(r.macro_f1, 4)]
                + shares,
            )
    sheet.freeze_panes = "D2"
