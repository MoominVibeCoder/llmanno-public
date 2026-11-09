"""Methoden-Report als eigenständiges HTML (PLAN 3.13), gegliedert nach Törnberg 2024 und
TRIPOD-LLM 2025, mit automatischem Methodenabsatz in Deutsch und Englisch.

Word öffnet die Datei direkt; Tabellen und Absätze lassen sich von dort übernehmen.
"""

from __future__ import annotations

import platform
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html import escape
from importlib.metadata import version
from pathlib import Path

from llmanno.core.config import CodingConfig, ModelConfig, ProviderConfig
from llmanno.core.engine import (
    fingerprint,
    prepare_prompts,
    result_basis,
    sent_reasoning_effort,
    served_models,
)
from llmanno.core.evaluation import (
    MAJORITY,
    NO_VALUE,
    SUBSET_LABELS,
    SUBSET_LABELS_EN,
    EvaluationReport,
    LLMComparison,
    main_comparison,
    reference_en,
)
from llmanno.core.export.plots import prevalence_svg, reliability_svg, risk_coverage_svg
from llmanno.core.export.prereg import (
    _example,
    examples_line,
    thinking_label,
    unit_mode_label,
)
from llmanno.core.history import codebook_history
from llmanno.core.i18n import current, localized, number, output_language, t
from llmanno.core.metrics.calibration import CalibrationReport
from llmanno.core.metrics.differential import MIN_LEVEL, DifferentialTest
from llmanno.core.metrics.rho import THRESHOLD as RHO_THRESHOLD
from llmanno.core.project import Project
from llmanno.core.prompt import TEMPLATE_VERSION, estimate_tokens
from llmanno.core.review import REVIEW_RANDOM
from llmanno.core.store import sha256_text
from llmanno.core.validation import count_test_evaluations, freeze_intact

PRIVACY = {"local": "lokal", "academic": "akademische API", "external": "externe API"}
PRIVACY_EN = {"local": "local", "academic": "academic API", "external": "external API"}


def privacy_label(level: str) -> str:
    return t(PRIVACY[level], PRIVACY_EN[level])


@dataclass
class ModelFacts:
    key: str
    digest: str | None = None
    num_ctx: int | None = None
    started: datetime | None = None
    calls: int = 0
    status: Counter[str] = field(default_factory=Counter)
    repaired: int = 0
    unsupported: int = 0  # Aufrufe mit nicht belegten Signalwörtern
    tokens_in: int = 0
    tokens_out: int = 0
    seconds: float = 0.0
    served: Counter[str] = field(default_factory=Counter)  # vom Server gemeldete Modelle
    output_modes: Counter[str] = field(default_factory=Counter)  # Schema, JSON oder Text
    details: dict = field(default_factory=dict)  # run_started des letzten Starts
    n_runs: int | None = None  # größtes N über alle Starts (N gehört nicht zur Identität)
    adaptive: bool | None = None  # irgendein Start adaptiv


def model_facts(project: Project) -> list[ModelFacts]:
    """Kennzahlen der Läufe, die den Ergebnissen zugrunde liegen."""
    basis = result_basis(project)
    first, starts, all_starts = {}, {}, {}
    for entry in project.store.audit():
        if entry.event == "run_started":
            run = (entry.details.get("model"), entry.details.get("config_hash"))
            first.setdefault(run, entry)
            starts[run] = entry
            all_starts.setdefault(run, []).append(entry.details)
    facts = []
    served = served_models(project)
    for key, config_hash in basis.runs.items():
        fact = ModelFacts(key, served=served.get(key, Counter()))
        if start := starts.get((key, config_hash)):
            fact.digest = start.details.get("digest")
            fact.num_ctx = start.details.get("num_ctx")
            fact.started = first[(key, config_hash)].ts
            fact.details = start.details
            every = all_starts[(key, config_hash)]
            if any("n_runs" in d for d in every):
                fact.n_runs = max(d["n_runs"] for d in every if "n_runs" in d)
                fact.adaptive = any(d.get("adaptive") for d in every)
        for call in project.store.calls(model_key=key, config_hash=config_hash, with_raw=False):
            fact.calls += 1
            fact.status[call.status] += 1
            if mode := call.meta.get("output_mode"):
                fact.output_modes[mode] += 1
            fact.repaired += "repariert" in call.flags
            fact.unsupported += "nicht_belegt" in call.flags
            for meta in (call.meta, call.meta.get("repair") or {}):
                fact.tokens_in += meta.get("tokens_in") or 0
                fact.tokens_out += meta.get("tokens_out") or 0
            fact.seconds += call.meta.get("duration_s") or call.meta.get("latency_s") or 0
        facts.append(fact)
    return facts


@dataclass(frozen=True)
class RunModel:
    """Ein Modell der Ergebnisse, wie es gelaufen ist; ohne Lauf wie geplant."""

    config: ModelConfig
    provider: ProviderConfig
    temperature: float | None
    effort: str | None
    n_runs: int
    adaptive: bool
    coding: CodingConfig
    fact: ModelFacts | None
    logged: bool  # False: Lauf ohne vollständiges Protokoll, Rest aus der Konfiguration


def run_models(project: Project) -> list[RunModel]:
    """Modelle und Parameter der Läufe, die den Ergebnissen zugrunde liegen.

    Report und GUIDE-LLM beschreiben damit, was gelaufen ist, nicht die Konfiguration von
    heute: Wer danach ein Modell tauscht oder die Temperatur ändert, ändert den Bericht
    nicht. Ohne Lauf beschreiben sie den Plan."""
    cfg = project.config
    facts = model_facts(project)
    if not facts:
        return [
            RunModel(
                m,
                cfg.provider(m.provider),
                m.effective_temperature(cfg.coding.n_runs),
                sent_reasoning_effort(m, cfg.provider(m.provider)),
                cfg.coding.n_runs,
                cfg.coding.adaptive,
                cfg.coding,
                None,
                False,
            )  # fmt: skip
            for m in cfg.panel
        ]
    current = {m.key: m for m in cfg.panel}
    runs = []
    for fact in facts:
        d = fact.details
        logged = "model_config" in d
        model = ModelConfig.model_validate(d["model_config"]) if logged else current[fact.key]
        provider = (
            ProviderConfig.model_validate(d["provider_config"])
            if logged
            else cfg.provider(model.provider)
        )
        coding = CodingConfig.model_validate(d["coding"]) if logged else cfg.coding
        n_runs = fact.n_runs or coding.n_runs
        runs.append(
            RunModel(
                model,
                provider,
                d["temperature"] if "temperature" in d else model.effective_temperature(n_runs),
                d["reasoning_effort"] if logged else sent_reasoning_effort(model, provider),
                n_runs,
                coding.adaptive if fact.adaptive is None else fact.adaptive,
                coding,
                fact,
                logged,
            )  # fmt: skip
        )
    return runs


def pseudonym_note(runs: list[RunModel]) -> str | None:
    """Wurden Namen vor dem Senden ersetzt? Aus den Läufen, nicht aus dem Plan."""
    used = [r.config.model for r in runs if r.coding.pseudonyms_sha256]
    if not used:
        return None
    note = t(
        "Namen wurden vor dem Senden durch Pseudonyme ersetzt (ganze Wörter in Text, Sprecher "
        "und Metadaten; die Namensliste ist nicht veröffentlicht).",
        "Names were replaced by pseudonyms before sending (whole words in text, speaker and "
        "metadata; the list of names is not published).",
    )
    if len(used) < len(runs):
        note += t(f" Nur bei: {', '.join(used)}.", f" Only for: {', '.join(used)}.")
    return note


def run_notes(project: Project, runs: list[RunModel]) -> list[str]:
    """Hinweise, wo der Bericht nicht allein aus dem Lauf schöpfen kann."""
    notes = []
    if runs and not any(r.fact for r in runs):
        notes.append(
            t(
                "Noch kein Lauf: Modelle und Parameter beschreiben den Plan.",
                "No run yet: models and parameters describe the plan.",
            )
        )
    if any(r.fact and not r.logged for r in runs):
        notes.append(
            t(
                "Ein Lauf stammt aus einer Version ohne vollständiges Protokoll: Modell, max. "
                "Tokens, Thinking und Reasoning-Aufwand sind der aktuellen Konfiguration "
                "entnommen.",
                "A run comes from a version without a complete log: model, max tokens, "
                "thinking and reasoning effort are taken from the current configuration.",
            )
        )
    logged = {r.fact.details.get("fingerprint") for r in runs if r.fact} - {None}
    if logged and logged != {fingerprint(project)}:
        notes.append(
            t(
                "Das Instrument wurde nach dem Lauf geändert (Codebuch, Prompt, Einheiten, "
                "Panel oder Parameter). Modelle und Parameter stammen aus dem Lauf, Codebuch "
                "und Prompt in diesem Bericht zeigen den aktuellen Stand.",
                "The instrument was changed after the run (codebook, prompt, units, panel or "
                "parameters). Models and parameters come from the run; codebook and prompt in "
                "this report show the current state.",
            )
        )
    return notes


def primary(report: EvaluationReport) -> LLMComparison | None:
    return main_comparison(report.comparisons, report.gold.consensus)


def _reference(name: str, de: bool) -> str:
    if name == MAJORITY:
        return "die Mehrheit der Kodierenden" if de else "the majority of human coders"
    if name == REVIEW_RANDOM:
        return (
            "blinde Reviews einer Zufallsstichprobe" if de else "blind reviews of a random sample"
        )
    if de:
        return f"die Referenzkodierung „{name}“"
    return f"the reference coding “{reference_en(name)}”"


def _n(value: float | None, lang: str | None = None, digits: int = 2) -> str:
    """Zahl in `lang`, ohne Angabe in der aktuellen Ausgabesprache."""
    if lang is None:
        return number(value, digits)
    with output_language(lang):
        return number(value, digits)


def _temperature(value: float | None, lang: str | None = None) -> str:
    if value is None:
        de = (lang or current()) == "de"
        return "nach Vorgabe des Anbieters" if de else "set by the provider"
    return _n(value, lang, 1)


def _differential_sentence(report: EvaluationReport, lang: str) -> str:
    de = lang == "de"
    tests = report.differential
    names = ", ".join(f"„{t.variable}“" if de else f'"{t.variable}"' for t in tests)
    robust = (
        ("cluster-robusten " if de else "cluster-robust ")
        if any(t.clustered for t in tests)
        else ("robusten " if de else "robust ")
    )
    flagged = [t for t in tests if t.p_holm < 0.05]
    found = ", ".join(f"{t.variable} (p = {_n(t.p_holm, lang, 3)})" for t in flagged)
    if de:
        verdict = (
            f"Hinweise darauf ergaben sich für {found}."
            if flagged
            else "Es ergab sich kein Hinweis darauf (alle p ≥ 0,05)."
        )
        return (
            f"Ob die Fehlerrate des LLM von {names} abhängt, wurde mit {robust}Wald-Tests "
            f"im linearen Wahrscheinlichkeitsmodell geprüft (Holm-korrigiert). {verdict}"
        )
    verdict = (
        f"There was evidence for {found}."
        if flagged
        else "There was no evidence for this (all p ≥ 0.05)."
    )
    return (
        f"Whether the LLM's error rate depends on {names} was tested with {robust}Wald "
        f"tests in a linear probability model (Holm-adjusted). {verdict}"
    )


# --- Methodenabsatz ------------------------------------------------------------------------------


def methods_paragraph(project: Project, report: EvaluationReport | None, lang: str) -> str:
    cfg = project.config
    assert cfg.codebook is not None
    _, items = prepare_prompts(project)
    runs = run_models(project)
    coding = runs[0].coding if runs else cfg.coding
    de = lang == "de"
    models = ", ".join(r.config.model for r in runs) or "–"
    temps = {r.temperature for r in runs}
    efforts = sorted({r.effort for r in runs if r.effort})
    n_runs = max((r.n_runs for r in runs), default=coding.n_runs)
    is_adaptive = any(r.adaptive for r in runs) if runs else coding.adaptive
    effort = "/".join(efforts)
    if effort:
        effort = f", Reasoning-Aufwand {effort}" if de else f", reasoning effort {effort}"
    ordered = sorted(t for t in temps if t is not None) + ([None] if None in temps else [])
    temperature = "/".join(_temperature(t, lang) for t in ordered) or "–"
    n_codes = len(cfg.codebook.active_codes())
    unit = {
        "isolated": ("einzeln", "in isolation"),
        "context": (
            f"mit {coding.units.n_before} vorangehenden und {coding.units.n_after} folgenden "
            "Zeilen als Kontext",
            f"with {coding.units.n_before} preceding and {coding.units.n_after} following lines "
            "as context",
        ),
        "holistic": ("je Gruppe als Ganzes", "as whole groups"),
    }[coding.units.mode][0 if de else 1]
    adaptive = (
        (
            f", abgebrochen nach {coding.adaptive_min_agree} übereinstimmenden Durchläufen"
            if de
            else f", stopping after {coding.adaptive_min_agree} agreeing runs"
        )
        if is_adaptive
        else ""
    )
    panel = (
        (
            "; im Panel zählt jedes Modell gleich"
            if de
            else "; each model of the panel carries equal weight"
        )
        if len(runs) > 1
        else ""
    )
    abstain = (
        (" und einer Restkategorie" if de else " plus a residual category")
        if cfg.codebook.abstain
        else ""
    )
    if de:
        text = [
            f"{len(items)} Einheiten wurden {unit} deduktiv mit {models} kodiert. Das Codebuch "
            f"mit {n_codes} Codes{abstain} stand wörtlich im Prompt"
            + (", die Reihenfolge der Codes wechselte je Durchlauf" if coding.permute_codes else "")
            + f". Je Einheit und Modell liefen bis zu {n_runs} Durchläufe mit Temperatur "
            f"{temperature}{effort}{adaptive}; final ist der häufigste Code "
            f"(Self-Consistency){panel}. Als Konfidenz dienen der Anteil übereinstimmender "
            "Durchläufe und die vom Modell genannte Konfidenz. Die genannten Signalwörter wurden "
            "im Text verortet."
            + (
                f" Im Prompt standen außerdem {len(coding.examples)} von Menschen kodierte "
                "Beispiele aus dem Entwicklungsteil (Few-Shot); diese Einheiten sind von allen "
                "Kennwerten ausgeschlossen."
                if coding.examples
                else ""
            )
        ]
    else:
        text = [
            f"{len(items)} units were coded deductively {unit} with {models}. The codebook with "
            f"{n_codes} codes{abstain} was included verbatim in the prompt"
            + (", with the code order permuted per run" if coding.permute_codes else "")
            + f". Each unit was coded up to {n_runs} times per model at temperature "
            f"{temperature}{effort}{adaptive}; the final code is the most frequent one "
            f"(self-consistency){panel}. Confidence is reported as the share of agreeing runs "
            "and as the model's verbalized confidence. Cited signal words were located in the text."
            + (
                f" The prompt also contained {len(coding.examples)} human-coded examples from "
                "the development set (few-shot); these units are excluded from all metrics."
                if coding.examples
                else ""
            )
        ]
    # Geltungsbereich: Kritik an GenAI in reflexiver qualitativer Forschung (Jowsey et al. 2025).
    text.append(
        "Das Verfahren ist eine deduktive Inhaltsanalyse mit festem Codebuch: Das LLM ordnete "
        "Einheiten vorgegebenen Codes zu und bildete keine Kategorien."
        if de
        else "The procedure is a deductive content analysis with a fixed codebook: the LLM "
        "assigned units to predefined codes and did not develop categories."
    )
    if report is not None and (main := primary(report)) is not None:
        a = main.agreement
        subset = "" if report.subset == "all" else f", {SUBSET_LABELS[report.subset]}"
        if report.model:
            subset += f", {'nur' if de else 'only'} {report.model}"
        boot = (
            (", gruppenweise gezogen" if de else ", resampling groups") if report.clustered else ""
        )
        if de:
            text.append(
                f"Gegen {_reference(main.reference, de)} (n = {a.n}{subset}) lag die "
                f"Übereinstimmung bei "
                f"{_n(a.percent.value * 100, lang, 1)} % (Cohens κ = {_n(a.kappa.value, lang)} "
                f"[{_n(a.kappa.low, lang)}; {_n(a.kappa.high, lang)}], Krippendorffs α = "
                f"{_n(a.alpha.value, lang)}, Gwets AC1 = {_n(a.ac1.value, lang)}; "
                f"95-%-Bootstrap-Konfidenzintervall{boot})."
            )
        else:
            text.append(
                f"Against {_reference(main.reference, de)} (n = {a.n}{subset}), agreement was "
                f"{_n(a.percent.value * 100, lang, 1)}% (Cohen's κ = {_n(a.kappa.value, lang)} "
                f"[{_n(a.kappa.low, lang)}, {_n(a.kappa.high, lang)}], Krippendorff's α = "
                f"{_n(a.alpha.value, lang)}, Gwet's AC1 = {_n(a.ac1.value, lang)}; "
                f"95% bootstrap confidence interval{boot})."
            )
        group = next((g for g in report.group_comparisons if g.reference == main.reference), None)
        if group is not None:
            ga, n_groups = group.agreement, len(report.groups)
            percent = _n(ga.percent.value * 100, lang, 1)
            text.append(
                f"Auf Ebene der {n_groups} Code-Gruppen lag die Übereinstimmung bei {percent} % "
                f"(Cohens κ = {_n(ga.kappa.value, lang)} [{_n(ga.kappa.low, lang)}; "
                f"{_n(ga.kappa.high, lang)}])."
                if de
                else f"At the level of the {n_groups} code groups, agreement was {percent}% "
                f"(Cohen's κ = {_n(ga.kappa.value, lang)} [{_n(ga.kappa.low, lang)}, "
                f"{_n(ga.kappa.high, lang)}])."
            )
        if report.baseline is not None:
            human = _n(report.baseline.alpha, lang)
            text.append(
                f"Zwischen den menschlichen Kodierenden lag Krippendorffs α bei {human}."
                if de
                else f"Among the human coders, Krippendorff's α was {human}."
            )
        if report.review_alpha:
            alpha, n = report.review_alpha
            text.append(
                f"Blinde Reviews verschiedener Personen stimmten auf {n} Einheiten mit "
                f"Krippendorffs α = {_n(alpha, lang)} überein."
                if de
                else f"Blind reviews by different people agreed with Krippendorff's α = "
                f"{_n(alpha, lang)} on {n} units."
            )
        if report.alt_test is not None:
            alt = report.alt_test
            if de:
                verdict = "bestanden" if alt.passed else "nicht bestanden"
                text.append(
                    f"Im Alternative Annotator Test (Calderon et al. 2025, ε = "
                    f"{_n(alt.epsilon, lang)}) erreichte das LLM eine Winning Rate von ω = "
                    f"{_n(alt.winning_rate, lang)} ({verdict})."
                )
            else:
                verdict = "passed" if alt.passed else "not passed"
                text.append(
                    f"In the alternative annotator test (Calderon et al. 2025, ε = "
                    f"{_n(alt.epsilon, lang)}), the LLM reached a winning rate of ω = "
                    f"{_n(alt.winning_rate, lang)} ({verdict})."
                )
    if report is not None and (main := primary(report)) is not None and main.calibration:
        sc, verbal = main.calibration.get("p_sc"), main.calibration.get("p_verbal")
        if sc is not None and verbal is not None:
            text.append(
                f"Die Self-Consistency trennte richtige von falschen Kodierungen mit AUROC = "
                f"{_n(sc.auroc, lang)}, die verbalisierte Konfidenz mit {_n(verbal.auroc, lang)}."
                if de
                else f"Self-consistency separated correct from incorrect codes with AUROC = "
                f"{_n(sc.auroc, lang)}, verbalized confidence with {_n(verbal.auroc, lang)}."
            )
    if report is not None and report.differential:
        text.append(_differential_sentence(report, lang))
    if report is not None and report.sensitivity:
        s = report.sensitivity[0]
        code, spread = s.max_spread
        pp = _n(spread * 100, lang, 0)
        n_texts = len(s.wording_rows)
        text.append(
            f"Zur Prompt-Sensitivität wurden {s.n_items} Einheiten mit {n_texts} "
            f"Formulierungen der Anweisungen kodiert (Barrie et al. 2024); Krippendorffs α "
            f"über die Formulierungen lag bei {_n(s.alpha, lang)}, der Anteil eines Codes "
            f"schwankte um höchstens {pp} Prozentpunkte ({code})."
            if de
            else f"For prompt sensitivity, {s.n_items} units were coded with {n_texts} "
            f"wordings of the instructions (Barrie et al. 2024); Krippendorff's α across "
            f"wordings was {_n(s.alpha, lang)}, and the share of a code varied by at most "
            f"{pp} percentage points ({code})."
        )
        if (iso := s.isolated) is not None:
            pct = _n(iso.agreement * 100, lang, 0)
            text.append(
                f"Ohne Kontext stimmten {pct} % der Codes mit dem Original überein."
                if de
                else f"Without context, {pct}% of codes matched the original."
            )
        if (low := s.reasoning) is not None:
            pct = _n(low.agreement * 100, lang, 0)
            text.append(
                f"Mit niedrigem Reasoning-Aufwand stimmten {pct} % der Codes mit dem Original "
                "überein."
                if de
                else f"With low reasoning effort, {pct}% of codes matched the original."
            )
        if not s.greedy:
            text.append(
                "Die Temperatur legte der Anbieter fest; das α enthält deshalb auch "
                "Zufallsstreuung."
                if de
                else "The provider set the temperature, so α also reflects sampling noise."
            )
    parts = project.store.split()
    n_dev = sum(1 for p in parts.values() if p == "dev")
    if report is not None and parts and report.subset == "all":
        text.append(
            f"Die Kennwerte beziehen sich auf alle Einheiten, einschließlich der {n_dev} "
            "Dev-Einheiten, an denen Codebuch und Prompt geschärft wurden."
            if de
            else f"The metrics cover all units, including the {n_dev} development units used to "
            "refine codebook and prompt."
        )
    if cfg.freeze is not None and parts and not freeze_intact(project):
        text.append(
            "Das Instrument wurde eingefroren, danach aber verändert; der Freeze ist gebrochen."
            if de
            else "The instrument was frozen but changed afterwards; the freeze is broken."
        )
    elif cfg.freeze is not None and parts:
        hash12 = cfg.freeze.instrument_hash[:12]
        evaluations = count_test_evaluations(project)
        text.append(
            f"Codebuch und Prompt wurden auf {n_dev} Dev-Einheiten geschärft und vor der "
            f"Auswertung der {len(parts) - n_dev} Test-Einheiten eingefroren (Instrument-Hash "
            f"{hash12}…; das Test-Set wurde {evaluations}-mal ausgewertet)."
            if de
            else f"Codebook and prompt were refined on {n_dev} development units and frozen "
            f"before evaluating {len(parts) - n_dev} test units (instrument hash {hash12}…; the "
            f"test set was evaluated {evaluations} time(s))."
        )
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    text.append(
        f"Kodiert wurde mit llmanno {version('llmanno')} (Stand {today}); alle Prompts, Antworten "
        "und Parameter sind protokolliert."
        if de
        else f"Coding used llmanno {version('llmanno')} (as of {today}); all prompts, responses "
        "and parameters are logged."
    )
    return " ".join(text)


# --- Gliederung ----------------------------------------------------------------------------------
# Der Report entsteht einmal als Folge von Bausteinen; HTML und DOCX rendern dieselbe Folge.


@dataclass(frozen=True)
class Heading:
    level: int
    text: str


@dataclass(frozen=True)
class Para:
    text: str
    style: str = ""  # "" | "note" (Hinweis) | "small" (Fußnote)


@dataclass(frozen=True)
class Table:
    header: list[str]
    rows: list[list[object]]


@dataclass(frozen=True)
class Pre:
    text: str


@dataclass(frozen=True)
class Bullets:
    items: list[str]
    style: str = ""


@dataclass(frozen=True)
class Reliability:
    calibration: dict[str, CalibrationReport]


@dataclass(frozen=True)
class Svg:
    """Grafik nur im HTML; Word bekommt dieselben Zahlen aus der Tabelle davor."""

    markup: str


Block = Heading | Para | Table | Pre | Bullets | Reliability | Svg


def _differential_row(test: DifferentialTest) -> list[str]:
    # Nur die Spanne: die Ausprägungen (Sprecher, Metadaten) können personenbezogen sein.
    values = [rate for _, rate in test.levels.values()]
    low, high = _n(min(values) * 100, digits=0), _n(max(values) * 100, digits=0)
    rates = t(f"{len(values)} Ausprägungen, {low}–{high} %", f"{len(values)} levels, {low}–{high}%")
    return [test.variable, rates, f"{_n(test.statistic)} ({test.df[0]}, {test.df[1]})",
            _n(test.p_value, digits=3), _n(test.p_holm, digits=3)]  # fmt: skip


def model_size(fact: ModelFacts | None) -> str:
    """Parametergröße und Quantisierung (Ollama), sonst „–“."""
    details = (fact.details.get("model_details") or {}) if fact else {}
    parts = [details.get("parameter_size"), details.get("quantization_level")]
    return ", ".join(p for p in parts if p) or "–"


def output_modes(fact: ModelFacts | None) -> str:
    """Ausgabeformat der Aufrufe; ein Rückfall auf JSON oder Text fällt so auf."""
    if fact is None or not fact.output_modes:
        return "–"
    total = sum(fact.output_modes.values())
    if len(fact.output_modes) == 1:
        return next(iter(fact.output_modes))
    return ", ".join(f"{m} ({n / total:.0%})" for m, n in fact.output_modes.most_common())


def server_note(fact: ModelFacts | None) -> str:
    version = fact.details.get("server_version") if fact else None
    return f", Ollama {version}" if version else ""


def _served(names: Counter[str]) -> str:
    if len(names) <= 1:
        return next(iter(names), "–")
    return ", ".join(f"{name} ({n}×)" for name, n in names.most_common())


def _est(e) -> str:
    return f"{_n(e.value)} [{_n(e.low)}; {_n(e.high)}]" if e is not None else "–"


def report_blocks(project: Project, report: EvaluationReport | None) -> list[Block]:
    """Die Gliederung in der aktuellen Ausgabesprache (i18n.output_language)."""
    cfg = project.config
    assert cfg.data is not None and cfg.codebook is not None
    spec, items = prepare_prompts(project)
    runs = run_models(project)
    coding = runs[0].coding if runs else cfg.coding
    parts = project.store.split()
    out: list[Block] = [
        Heading(1, t("Methoden-Report", "Methods report") + f": {cfg.name}"),
        Para(
            t("Erstellt", "Created")
            + f" {datetime.now(UTC):%Y-%m-%d %H:%M} UTC "
            + t("mit", "with")
            + f" llmanno {version('llmanno')}",
            "small",
        ),
        Heading(2, t("Methodenabsatz", "Methods paragraph")),
    ]
    from llmanno.core.demo import demo_note, is_demo  # demo importiert engine, report nicht

    if is_demo(project):
        out.insert(1, Para(demo_note(), "note"))
    out += [Para(note, "note") for note in run_notes(project, runs)]
    from llmanno.core.transfer import transfer_note  # transfer importiert engine, report nicht

    if note := transfer_note(project):
        out.append(Para(note))
    languages = [("de", "Deutsch"), ("en", "English")]
    for lang, title in sorted(languages, key=lambda pair: pair[0] != current()):
        out += [Heading(3, title), Para(methods_paragraph(project, report, lang))]

    unit = unit_mode_label(coding.units.mode)
    if coding.units.mode == "context":
        before, after = coding.units.n_before, coding.units.n_after
        unit += t(f" ({before} davor, {after} danach)", f" ({before} before, {after} after)")
    if (column := coding.units.target_column) and coding.units.mode != "holistic":
        unit += t(
            f"; kodiert nur Zeilen mit „{column}“ = ja, übrige als Kontext",
            f"; codes only rows with “{column}” = yes, the others serve as context",
        )
    groups = len({u.group for u in project.store.units() if u.group is not None})
    split = (
        f"Dev {sum(1 for p in parts.values() if p == 'dev')}, "
        f"Test {sum(1 for p in parts.values() if p == 'test')}"
        if parts
        else t("kein Split", "no split")
    )
    out += [
        Heading(2, t("1. Daten", "1. Data")),
        Table(
            [t("Merkmal", "Item"), t("Wert", "Value")],
            [
                [
                    t("Quelle", "Source"),
                    f"{cfg.data.source_name}, {t('Blatt', 'sheet')} {cfg.data.sheet}",
                ],
                [t("SHA-256 der Datei", "SHA-256 of the file"), cfg.data.file_sha256],
                [
                    t("Zeilen / Kodiereinheiten", "Rows / coding units"),
                    f"{cfg.data.n_units} / {len(items)}",
                ],
                [t("Analyseeinheit", "Unit of analysis"), unit],
                [t("Gruppen", "Groups"), groups or "–"],
                [
                    t("Referenzkodierungen", "Reference codings"),
                    ", ".join(cfg.data.mapping.gold) or "–",
                ],
                [
                    t(
                        "Analyse-Variablen (nicht im Prompt)",
                        "Analysis variables (not in the prompt)",
                    ),
                    ", ".join(cfg.data.mapping.analysis) or "–",
                ],
                [t("Dev/Test-Split", "Dev/test split"), split],
            ],  # fmt: skip
        ),
    ]

    text = project.codebook_text or ""
    abstain = t("an", "on") if cfg.codebook.abstain else t("aus", "off")
    out += [
        Heading(2, t("2. Codebuch", "2. Codebook")),
        Para(
            f"SHA-256 {sha256_text(text)} · "
            + t(
                f"etwa {estimate_tokens(text)} Token · Skalenniveau {cfg.codebook.scale} · Modus "
                f"{cfg.mode}, bis zu {cfg.k} Codes je Einheit · Restkategorie {abstain}",
                f"about {estimate_tokens(text)} tokens · level of measurement "
                f"{cfg.codebook.scale} · mode {cfg.mode}, up to {cfg.k} codes per unit · "
                f"residual category {abstain}",
            )
        ),
        Table(
            [t("Kürzel", "Code"), "Label"],
            [[c.id, c.label or ""] for c in cfg.codebook.active_codes()],
        ),
    ]
    versions = codebook_history(project)
    if len(versions) > 1:
        out += [
            Heading(3, t("Fassungen des Codebuchs", "Codebook versions")),
            Table(
                [
                    t("Fassung", "Version"),
                    t("geladen", "loaded"),
                    t("Datei", "File"),
                    "SHA-256",
                    t("Zeilen + / −", "Lines + / −"),
                    t("Lauf-Starts", "Run starts"),
                ],
                [
                    [
                        v.number,
                        f"{v.loaded.astimezone():%Y-%m-%d %H:%M}",
                        v.source,
                        v.sha256[:12],
                        f"+{v.added} / −{v.removed}" if v.added is not None else "–",
                        v.runs,
                    ]
                    for v in versions
                ],
            ),  # fmt: skip
            Para(
                t(
                    "Die Fassungen und ihre Unterschiede liegen im Replikationspaket "
                    "(codebook_history/).",
                    "The versions and their differences are in the replication package "
                    "(codebook_history/).",
                ),
                "small",
            ),
        ]

    rows = []
    for run in runs:
        model, provider, fact = run.config, run.provider, run.fact
        rows.append(
            [
                model.model,
                f"{provider.name} ({provider.base_url}{server_note(fact)})",
                privacy_label(provider.privacy_level),
                (fact.digest or "–") if fact else "–",
                model_size(fact),
                _served(fact.served) if fact else "–",
                f"{fact.started:%Y-%m-%d}" if fact and fact.started else "–",
            ]
        )
    out += [
        Heading(2, t("3. Modelle", "3. Models")),
        Table(
            [
                t("Modell", "Model"),
                t("Anbieter", "Provider"),
                t("Datenschutz", "Privacy"),
                "Digest",
                t("Größe, Quantisierung", "Size, quantization"),
                t("vom Anbieter gemeldet", "reported by the provider"),
                t("Laufdatum", "Run date"),
            ],
            rows,
        ),  # fmt: skip
    ]
    if note := pseudonym_note(runs):
        out.append(Para(note, "small"))

    rows = [
        [
            r.config.model,
            _temperature(r.temperature),
            r.config.max_tokens,
            (r.fact.num_ctx if r.fact else None) or "–",
            thinking_label(r.config.thinking),
            r.effort or "–",
            output_modes(r.fact),
        ]
        for r in runs
    ]
    n_runs = max((r.n_runs for r in runs), default=coding.n_runs)
    agree = coding.adaptive_min_agree
    adaptive = (
        t(f", adaptiv nach {agree} einstimmigen", f", adaptive after {agree} agreeing")
        if any(r.adaptive for r in runs)
        else ""
    )
    order = (
        t("pro Lauf gemischt", "permuted per run") if coding.permute_codes else t("fest", "fixed")
    )
    repair = t("an", "on") if coding.evidence_repair else t("aus", "off")
    parallel = [
        f"{r.config.model} {r.fact.details['parallel']}"
        for r in runs
        if r.fact and r.fact.details.get("parallel")
    ]
    parallel_note = (
        t(" · parallele Anfragen: ", " · parallel requests: ") + ", ".join(parallel)
        if parallel
        else ""
    )
    out += [
        Heading(2, t("4. Parameter", "4. Parameters")),
        Table(
            [
                t("Modell", "Model"),
                t("Temperatur", "Temperature"),
                t("max. Tokens", "max tokens"),
                "num_ctx",
                "Thinking",
                t("Reasoning-Aufwand", "Reasoning effort"),
                t("Ausgabeformat", "Output format"),
            ],
            rows,
        ),  # fmt: skip
        Para(
            t(
                f"Durchläufe je Einheit und Modell: bis zu {n_runs}{adaptive} · Seeds "
                f"{coding.base_seed} + Laufindex · Code-Reihenfolge {order} · Evidenz-Repair "
                f"{repair} · Prompt-Template Version {TEMPLATE_VERSION}",
                f"Runs per unit and model: up to {n_runs}{adaptive} · seeds {coding.base_seed} "
                f"+ run index · code order {order} · evidence repair {repair} · prompt template "
                f"version {TEMPLATE_VERSION}",
            )
            + parallel_note
        ),
        *([Para(examples_line(coding).removeprefix("- "))] if coding.examples else []),
    ]

    review = cfg.review
    low = (
        t(f"geschätzte Trefferquote unter {_n(review.tau)} (isotone Abbildung der "
          f"Self-Consistency, gelernt auf {review.calibration.n} Einheiten mit Gold, mit Split "
          "nur Dev)", f"estimated accuracy below {_n(review.tau)} (isotonic mapping of "
          f"self-consistency, learned on {review.calibration.n} units with gold, dev only if "
          "split)")
        if review.calibration
        else t(f"Self-Consistency unter {_n(review.tau)}", f"self-consistency below "
               f"{_n(review.tau)}")
    )  # fmt: skip
    gates = [
        name
        for name, on in (
            (low, review.gate_low_confidence),
            (t("Dissens im Panel", "panel disagreement"), review.gate_dissent),
            (t("Restkategorie", "residual category"), review.gate_abstain),
            (t("nicht belegt", "unsupported"), review.gate_unsupported),
            (t("ungültig", "invalid"), review.gate_invalid),
        )
        if on
    ]  # fmt: skip
    share = _n(review.random_share * 100, digits=0)
    gate = ", ".join(gates) or t("aus", "off")
    out += [
        Heading(2, t("5. Aggregation und Review-Gate", "5. Aggregation and review gate")),
        Para(
            t(
                "Self-Consistency je Modell, gleich gewichtet über das Panel; Multi-Label-Schwelle "
                f"0,5; Abstände unter 0,1 gelten als unentschieden. Review-Gate: {gate}; "
                f"Zufallsstichprobe {share} % (Seed {review.seed}).",
                "Self-consistency per model, equally weighted across the panel; multi-label "
                f"threshold 0.5; margins below 0.1 count as ties. Review gate: {gate}; random "
                f"sample {share}% (seed {review.seed}).",
            )
        ),
        Heading(2, t("6. Validierungsdesign", "6. Validation design")),
    ]
    if cfg.freeze is not None:
        intact = t("intakt", "intact") if freeze_intact(project) else t("gebrochen", "broken")
        frozen, digest = f"{cfg.freeze.frozen_at:%Y-%m-%d %H:%M}", cfg.freeze.instrument_hash
        times = count_test_evaluations(project)
        out.append(
            Para(
                t(
                    f"Eingefroren am {frozen} UTC · Instrument-Hash {digest} · Freeze {intact} · "
                    f"Test-Set {times}-mal ausgewertet",
                    f"Frozen on {frozen} UTC · instrument hash {digest} · freeze {intact} · test "
                    f"set evaluated {times} time(s)",
                )
            )
        )
    else:
        out.append(
            Para(t("Das Instrument wurde nicht eingefroren.", "The instrument was not frozen."),
                 "note")
        )  # fmt: skip

    out.append(Heading(2, t("7. Ergebnisse", "7. Results")))
    if report is None:
        out.append(Para(t("Keine Auswertung berechnet.", "No evaluation computed."), "note"))
    else:
        out += _results(report)

    rows = []
    for f in (r.fact for r in runs if r.fact):
        total = f.calls or 1
        rows.append(
            [
                f.key,
                f.calls,
                f"{f.status['ok'] / total:.1%}",
                f"{(f.status['invalid'] + f.status['error']) / total:.1%}",
                f"{f.repaired / total:.1%}",
                f"{f.unsupported / total:.1%}",
                f"{f.tokens_in} / {f.tokens_out}",
                f"{f.seconds / 60:.1f} min",
            ]
        )
    out += [
        Heading(2, t("8. Läufe, Stabilität, Ressourcen", "8. Runs, stability, resources")),
        Table(
            [
                t("Modell", "Model"),
                t("Aufrufe", "Calls"),
                t("gültig", "valid"),
                t("ungültig/Fehler", "invalid/error"),
                t("repariert", "repaired"),
                t("nicht belegt", "unsupported"),
                t("Token ein/aus", "Tokens in/out"),
                t("Rechenzeit", "Compute time"),
            ],
            rows,
        ),  # fmt: skip
    ]
    if report is not None:
        intra = ", ".join(f"{m}: α {_n(a)}" for m, a in report.stability.intra_alpha.items())
        inter = _n(report.stability.inter_alpha)
        out.append(
            Para(
                t(
                    f"Stabilität über die Läufe: {intra or '–'} · zwischen den Modellen: α {inter} "
                    "(Unsicherheitssignal, kein Gütemaß)",
                    f"Stability across runs: {intra or '–'} · between models: α {inter} "
                    "(an uncertainty signal, not a quality measure)",
                )
            )
        )
    if report is not None and (ds := report.dawid_skene):
        accuracy = ", ".join(f"{m}: {_n(a)}" for m, a in ds.accuracy.items())
        ref = _reference_name(ds.reference)
        out.append(
            Para(
                t(
                    f"Gegenprobe nach Dawid-Skene (Ein-Münz-Variante, ohne Gold): geschätzte "
                    f"Trefferquote je Modell {accuracy}. Gewichtet wären {ds.changed} von "
                    f"{ds.n} Einheiten anders kodiert; κ gegen {ref} {_n(ds.kappa_weighted)} "
                    f"statt {_n(ds.kappa_panel)}. Das Verfahren setzt unabhängige Fehler der "
                    "Modelle voraus; bei LLMs ist das meist verletzt.",
                    f"Dawid-Skene check (one-coin variant, without gold): estimated accuracy "
                    f"per model {accuracy}. Weighted, {ds.changed} of {ds.n} units would be "
                    f"coded differently; κ against {ref} {_n(ds.kappa_weighted)} instead of "
                    f"{_n(ds.kappa_panel)}. The method assumes independent model errors; for "
                    "LLMs this is usually violated.",
                ),
                "small",
            )
        )
    out.append(
        Para(
            f"Python {platform.python_version()} · {platform.platform()} · PySide6 "
            f"{version('PySide6-Essentials')}",
            "small",
        )
    )

    from llmanno.core.export.guide import CITATION, guide_rows, todo  # guide importiert report

    out += [
        Heading(2, t("GUIDE-LLM-Checkliste", "GUIDE-LLM checklist")),
        Para(
            t(
                f"Vorausgefüllt nach {CITATION}. Zeilen mit „{todo()}“ selbst ausfüllen; Punkte "
                "ohne Nummer sind empfohlene Zusatzangaben.",
                f"Prefilled following {CITATION}. Complete the rows marked “{todo()}”; items "
                "without a number are recommended additions.",
            ),
            "small",
        ),
        Table(
            [t("Punkt", "Item"), t("Thema", "Topic"), t("Angabe", "Entry")],
            [list(row) for row in guide_rows(project, report)],
        ),
    ]
    out.append(Heading(2, t("Anhang: Prompt", "Appendix: prompt")))
    if items:
        prompt = _example(spec, items[0], coding.base_seed)
        out += [
            Heading(3, t("System-Nachricht (erster Lauf)", "System message (first run)")),
            Pre(prompt.system),
            Heading(
                3,
                t("Beispiel einer User-Nachricht", "Example user message")
                + f" ({items[0].item_id})",
            ),
            Pre(prompt.user)
            if prompt.user
            else Para(
                t(
                    "Fehlt: Die Namensliste ist nicht vorhanden.",
                    "Missing: the list of names is not available.",
                ),
                "small",
            ),
        ]
    return out


def _reference_name(name: str) -> str:
    return t(name, reference_en(name))


def _results(report: EvaluationReport) -> list[Block]:
    subset = t(SUBSET_LABELS[report.subset], SUBSET_LABELS_EN[report.subset])
    grouped = t(", gruppenweise", ", by group") if report.clustered else ""
    out: list[Block] = [
        Para(
            t(
                f"Teilmenge: {subset} · LLM: {report.model or 'Panel'} · {report.n_scored} von "
                f"{report.n_items} Einheiten mit gültigem LLM-Ergebnis · Bootstrap "
                f"{report.resamples} Ziehungen{grouped}",
                f"Subset: {subset} · LLM: {report.model or 'panel'} · {report.n_scored} of "
                f"{report.n_items} units with a valid LLM result · bootstrap with "
                f"{report.resamples} resamples{grouped}",
            )
        )
    ]
    if report.warnings:
        out.append(Bullets([localized(w) for w in report.warnings], "note"))
    agreement_header = [
        t("Referenz", "Reference"), "n", t("% Übereinst.", "% agreement"),
        t("Cohens κ", "Cohen's κ"), t("Krippendorffs α", "Krippendorff's α"),
        t("Gwets AC1", "Gwet's AC1"),
    ]  # fmt: skip
    rows = [
        [
            _reference_name(c.reference),
            c.agreement.n,
            _est(c.agreement.percent),
            _est(c.agreement.kappa),
            _est(c.agreement.alpha),
            _est(c.agreement.ac1),
            _est(c.accuracy),
            _est(c.macro_f1),
        ]
        for c in report.comparisons
    ]
    out.append(Table([*agreement_header, "Accuracy", "Macro-F1"], rows))
    if report.group_comparisons:
        out += [
            Heading(
                3,
                t("Auf Ebene der Code-Gruppen", "At the level of code groups")
                + f" ({', '.join(report.groups)})",
            ),
            Table(
                agreement_header,
                [
                    [
                        _reference_name(g.reference),
                        g.agreement.n,
                        _est(g.agreement.percent),
                        _est(g.agreement.kappa),
                        _est(g.agreement.alpha),
                        _est(g.agreement.ac1),
                    ]
                    for g in report.group_comparisons
                ],  # fmt: skip
            ),
        ]
        if report.group_baseline is not None:
            alpha = _n(report.group_baseline)
            out.append(
                Para(
                    t(
                        f"Mensch-Mensch auf den Gruppen: Krippendorffs α {alpha}",
                        f"Human–human on the groups: Krippendorff's α {alpha}",
                    )
                )
            )
    if (main := primary(report)) is not None:
        scores = (main.single or main.multi).per_class
        cal = main.calibration
        out += [
            Heading(3, t("Je Code (gegen ", "Per code (against ")
                    + _reference_name(main.reference) + ")"),
            Table(
                ["Code", "Precision", "Recall", "F1", t("Referenz n", "Reference n"), "LLM n",
                 t("Cohens κ je Code", "Cohen's κ per code"), "ρ"],
                [
                    [code, _n(s.precision), _n(s.recall), _n(s.f1), s.support, s.predicted,
                     _n(main.code_kappa.get(code)), _n(main.rho.get(code), digits=3)]
                    for code, s in scores.items()
                ],
            ),  # fmt: skip
            Para(
                t(
                    f"ρ nach Shaffer (2017; R-Paket rhoR): Anteil simulierter Test-Sets gleicher "
                    f"Größe aus Grundgesamtheiten mit κ zwischen 0,40 und {_n(RHO_THRESHOLD)}, die "
                    f"das beobachtete κ erreichen; ρ < 0,05 spricht für κ > {_n(RHO_THRESHOLD)} "
                    "in allen Einheiten. Die Fehlerrate gilt gemittelt über diesen Bereich; "
                    "liegt κ knapp unter der Schwelle, sagt ρ öfter fälschlich „darüber“, umso "
                    "öfter, je größer das Test-Set. ρ setzt unabhängige Einheiten voraus.",
                    f"ρ after Shaffer (2017; R package rhoR): share of simulated test sets of the "
                    f"same size, drawn from populations with κ between 0.40 and "
                    f"{_n(RHO_THRESHOLD)}, that reach the observed κ; ρ < 0.05 suggests "
                    f"κ > {_n(RHO_THRESHOLD)} across all units. The error rate holds on average "
                    "over this range; if κ is just below the threshold, ρ wrongly says “above” "
                    "more often, the more so the larger the test set. ρ assumes independent "
                    "units.",
                ),
                "small",
            ),
            Heading(3, t("Kalibrierung", "Calibration")),
            Table(
                [t("Konfidenz", "Confidence"), "ECE", t("ECE rekalibriert", "ECE recalibrated"),
                 "Brier", "AUROC", "AURC"],
                [
                    [k, _n(v.ece), _n(v.ece_recalibrated), _n(v.brier), _n(v.auroc), _n(v.aurc)]
                    for k, v in cal.items()
                ],
            ),
            Para(
                t(
                    "AUROC: trennt die Konfidenz richtige von falschen Kodierungen (0,5 = nein). "
                    "AURC: mittlere Fehlerrate bei Annahme nach absteigender Konfidenz. ECE "
                    "rekalibriert: nach isotoner Rekalibrierung, fünffach kreuzvalidiert.",
                    "AUROC: does the confidence separate correct from incorrect codes (0.5 = no). "
                    "AURC: mean error rate when accepting units by descending confidence. ECE "
                    "recalibrated: after isotonic recalibration, five-fold cross-validated.",
                ),
                "small",
            ),
            Reliability(cal),
        ]  # fmt: skip
        if main.by_runs:
            out.append(
                Table(
                    [
                        t("Übereinstimmung der Läufe", "Agreement of runs"),
                        "n",
                        t("Trefferquote", "Accuracy"),
                    ],
                    [[label, n, _n(acc)] for label, n, acc in main.by_runs],
                )  # fmt: skip
            )
        if main.subgroups:
            out += [
                Heading(3, t("Subgruppen", "Subgroups") + f" ({report.subgroup or ''})"),
                Table(
                    [
                        t("Ausprägung", "Level"),
                        "n",
                        t("% Übereinst.", "% agreement"),
                        t("Cohens κ", "Cohen's κ"),
                        "Macro-F1",
                    ],
                    [
                        [
                            t(r.value, "(no value)") if r.value == NO_VALUE else r.value,
                            r.n,
                            _n(r.percent),
                            _est(r.kappa),
                            _n(r.macro_f1),
                        ]
                        for r in main.subgroups
                    ],
                ),  # fmt: skip
            ]
    if report.prevalence:
        labeled, unlabeled = report.prevalence_n or (0, 0)
        robust = t(", cluster-robust über Gruppen.", ", cluster-robust across groups.")
        labels = (t("LLM naiv", "LLM naive"), t("nur Stichprobe", "sample only"), "PPI++")
        out += [
            Heading(
                3, t("Bias-korrigierte Prävalenz (PPI++)", "Bias-corrected prevalence (PPI++)")
            ),
            Para(
                t(
                    f"Zufällige Review-Stichprobe {labeled}, ungelabelt {unlabeled}; PPI++ mit "
                    "Power-Tuning λ (Angelopoulos, Duchi & Zrnic 2023; λ = 1 ist PPI, λ = 0 die "
                    "Stichprobe allein), 95-%-KI per Normalapproximation",
                    f"Random review sample {labeled}, unlabeled {unlabeled}; PPI++ with power "
                    "tuning λ (Angelopoulos, Duchi & Zrnic 2023; λ = 1 is PPI, λ = 0 the sample "
                    "alone), 95% CI by normal approximation",
                )
                + (robust if report.clustered else ".")
                + (
                    t(
                        f" „Alle Reviews gewichtet“ nutzt zusätzlich die blinden Reviews aus "
                        f"dem Gate ({report.prevalence_weighted_n} Einheiten insgesamt), "
                        "gewichtet mit ihrer protokollierten Ziehwahrscheinlichkeit (Gate 1, "
                        "Zufallsstichprobe n/N) und Power-Tuning λ (confidence-driven "
                        "inference, Gligorić et al. 2025; Zrnic & Candès 2024).",
                        f" “All reviews weighted” also uses the blind reviews from the gate "
                        f"({report.prevalence_weighted_n} units in total), weighted by their "
                        "logged draw probability (gate 1, random sample n/N) with power "
                        "tuning λ (confidence-driven inference, Gligorić et al. 2025; "
                        "Zrnic & Candès 2024).",
                    )
                    if report.prevalence_weighted_n
                    else ""
                )
            ),
            Table(
                ["Code", *labels, "λ"]
                + ([t("Alle Reviews gewichtet", "All reviews weighted"), "λ"]
                   if report.prevalence_weighted_n else []),
                [
                    [r.code, _n(r.llm), _est(r.sample), _est(r.ppi), _n(r.ppi_lambda)]
                    + ([_est(r.weighted) if r.weighted else "–",
                        _n(r.weighted_lambda) if r.weighted_lambda is not None else "–"]
                       if report.prevalence_weighted_n else [])
                    for r in report.prevalence
                ],
            ),
            Svg(prevalence_svg(report.prevalence, labels, t(",", "."))),
        ]  # fmt: skip
    for s in report.sensitivity:
        temperature = (
            t(", Temperatur 0", ", temperature 0")
            if s.greedy
            else t(", Temperatur nach Vorgabe des Anbieters", ", temperature set by the provider")
        )
        out += [
            Heading(3, t("Prompt-Sensitivität", "Prompt sensitivity") + f": {s.model}"),
            Para(
                t(
                    f"{s.n_items} Einheiten, je Variante ein Lauf{temperature}. Krippendorffs α "
                    "über Original und Umformulierungen (inter-prompt, Barrie et al. 2024): "
                    f"{_n(s.alpha)}. „ohne Kontext“ zählt nicht zum α: Es ändert die "
                    "Analyseeinheit, nicht den Wortlaut; ebenso „Reasoning niedrig“, es ändert "
                    "den Reasoning-Aufwand des Modells.",
                    f"{s.n_items} units, one run per variant{temperature}. Krippendorff's α across "
                    "the original and the rewordings (inter-prompt, Barrie et al. 2024): "
                    f"{_n(s.alpha)}. “Without context” does not count towards α: it changes the "
                    "unit of analysis, not the wording; neither does “Reasoning niedrig” (low "
                    "reasoning), which changes the model's reasoning effort.",
                )
            ),
            Table(
                [
                    t("Variante", "Variant"),
                    "n",
                    t("gleich wie Original", "same as original"),
                    t("α gegen Referenz", "α against reference"),
                    *report.labels,
                ],
                [
                    [
                        r.variant + ("" if r.wording else t(" (nicht im α)", " (not in α)")),
                        r.n,
                        _n(r.agreement),
                        _n(r.alpha_reference),
                    ]
                    + [_n(r.shares.get(c)) for c in report.labels]
                    for r in s.rows
                ],
            ),  # fmt: skip
        ]
    if report.differential:
        reference = _reference_name(report.differential_reference or "")
        out += [
            Heading(3, t("Messfehler nach Subgruppen", "Measurement error by subgroup")),
            Para(
                t(
                    f"Fehlerrate gegen {reference}; Wald-Test im linearen "
                    "Wahrscheinlichkeitsmodell, robust bzw. cluster-robust, Holm-korrigiert. "
                    "Ausprägungen mit weniger als "
                    f"{MIN_LEVEL} Einheiten entfallen.",
                    f"Error rate against {reference}; Wald test in a linear probability model, "
                    "robust or cluster-robust, Holm-adjusted. Levels with fewer than "
                    f"{MIN_LEVEL} units are dropped.",
                )
            ),
            Table(
                [
                    "Variable",
                    t("Fehlerrate (Spanne)", "Error rate (range)"),
                    "F (df)",
                    "p",
                    t("p Holm", "p Holm"),
                ],
                [_differential_row(d) for d in report.differential],
            ),  # fmt: skip
        ]
    if report.baseline is not None:
        out += [
            Heading(3, t("Mensch-Mensch-Baseline", "Human–human baseline")),
            Table(
                [
                    t("Paar", "Pair"),
                    "n",
                    t("Cohens κ", "Cohen's κ"),
                    t("Krippendorffs α", "Krippendorff's α"),
                ],
                [
                    [f"{a} vs. {b}", v.n, _est(v.kappa), _est(v.alpha)]
                    for (a, b), v in report.baseline.pairs.items()
                ],
            ),  # fmt: skip
        ]
    if report.alt_test is not None:
        alt = report.alt_test
        verdict = t("bestanden", "passed") if alt.passed else t("nicht bestanden", "not passed")
        out.append(
            Para(
                t("Alt-Test", "Alternative annotator test")
                + f" (Calderon et al. 2025): ε = {_n(alt.epsilon)}, ω = {_n(alt.winning_rate)}, "
                f"Advantage Probability = {_n(alt.advantage_probability)} · {verdict}"
            )
        )
    out.append(
        Para(
            t(
                "Begründungen des Modells sind Plausibilisierungen, keine Erklärungen seiner "
                "Entscheidung. Waren Daten oder Codebuch öffentlich zugänglich, können sie im "
                "Training der Modelle enthalten sein; die Übereinstimmung fällt dann womöglich "
                "zu hoch aus (Ludwig et al. 2024).",
                "Model rationales are post-hoc plausibilizations, not explanations of its "
                "decision. If data or codebook were publicly available, they may be part of the "
                "models' training data; agreement may then be inflated (Ludwig et al. 2024).",
            ),
            "small",
        )
    )
    return out


# --- HTML -----------------------------------------------------------------------------------------

CSS = """
body { font-family: Segoe UI, Arial, sans-serif; max-width: 60rem; margin: 2rem auto;
       padding: 0 1rem; line-height: 1.45; color: #1f2937; background: #ffffff; }
table { border-collapse: collapse; margin: .5rem 0 1rem; }
th, td { border: 1px solid #d1d5db; padding: .25rem .5rem; text-align: left; vertical-align: top; }
th { background: #f3f4f6; }
pre { white-space: pre-wrap; background: #f9fafb; border: 1px solid #e5e7eb; padding: .5rem; }
.note { color: #92400e; }
.small { font-size: .85rem; color: #4b5563; }
"""


def _html_block(block: Block) -> str:
    if isinstance(block, Heading):
        return f"<h{block.level}>{escape(block.text)}</h{block.level}>"
    if isinstance(block, Para):
        style = f" class='{block.style}'" if block.style else ""
        return f"<p{style}>{escape(block.text)}</p>"
    if isinstance(block, Table):
        head = "".join(f"<th>{escape(h)}</th>" for h in block.header)
        body = "".join(
            "<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in row) + "</tr>"
            for row in block.rows
        )
        return f"<table><tr>{head}</tr>{body}</table>"
    if isinstance(block, Pre):
        return f"<pre>{escape(block.text)}</pre>"
    if isinstance(block, Bullets):
        style = f" class='{block.style}'" if block.style else ""
        return f"<ul{style}>" + "".join(f"<li>{escape(i)}</li>" for i in block.items) + "</ul>"
    if isinstance(block, Svg):
        return f"<div>{block.markup}</div>"
    labels = (t("Konfidenz", "Confidence"), t("Trefferquote", "Accuracy"))
    curve = (t("Abdeckung", "Coverage"), t("Fehlerrate", "Error rate"))
    decimal = t(",", ".")
    svgs = "".join(
        reliability_svg(k, v, *labels, decimal) + risk_coverage_svg(k, v, *curve, decimal)
        for k, v in block.calibration.items()
    )
    return f"<div>{svgs}</div>"


def report_html(project: Project, report: EvaluationReport | None) -> str:
    body = "".join(_html_block(b) for b in report_blocks(project, report))
    return (
        f"<!doctype html><html lang='{current()}'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{t('Methoden-Report', 'Methods report')} {escape(project.config.name)}</title>"
        f"<style>{CSS}</style>"
        f"</head><body>{body}</body></html>"
    )


# --- DOCX -----------------------------------------------------------------------------------------


def report_docx(project: Project, report: EvaluationReport | None, path: Path) -> None:
    """Word-Fassung derselben Gliederung; das Reliability-Diagramm steht dort als Tabelle."""
    from docx import Document
    from docx.shared import Pt, RGBColor

    document = Document()
    colors = {"note": RGBColor(0x92, 0x40, 0x0E), "small": RGBColor(0x4B, 0x55, 0x63)}

    def table(header: list[str], rows: list[list[object]]) -> None:
        grid = document.add_table(rows=1, cols=len(header))
        grid.style = "Table Grid"
        for cell, text in zip(grid.rows[0].cells, header, strict=True):
            cell.text = text
            for run in cell.paragraphs[0].runs:
                run.bold = True
        for row in rows:
            cells = grid.add_row().cells
            for cell, value in zip(cells, row, strict=True):
                cell.text = docx_text(str(value))
        document.add_paragraph()

    for block in report_blocks(project, report):
        if isinstance(block, Heading):
            document.add_heading(docx_text(block.text), level=block.level - 1)
        elif isinstance(block, Para):
            run = document.add_paragraph().add_run(docx_text(block.text))
            if block.style in colors:
                run.font.color.rgb = colors[block.style]
            if block.style == "small":
                run.font.size = Pt(8)
        elif isinstance(block, Bullets):
            for item in block.items:
                run = document.add_paragraph(style="List Bullet").add_run(docx_text(item))
                if block.style in colors:
                    run.font.color.rgb = colors[block.style]
        elif isinstance(block, Table):
            table(block.header, block.rows)
        elif isinstance(block, Svg):
            continue
        elif isinstance(block, Pre):
            run = document.add_paragraph().add_run(docx_text(block.text))
            run.font.name = "Consolas"
            run.font.size = Pt(8)
        else:
            for name, cal in block.calibration.items():
                document.add_paragraph(
                    t(f"Reliability-Diagramm {name} als Tabelle:", f"Reliability diagram {name}:")
                )
                table(
                    [t("Konfidenzklasse", "Confidence bin"), "n",
                     t("mittlere Konfidenz", "mean confidence"), t("Trefferquote", "Accuracy")],
                    [
                        [f"{_n(b.low, digits=1)}–{_n(b.high, digits=1)}", b.n,
                         _n(b.mean_confidence), _n(b.accuracy)]
                        for b in cal.bins
                        if b.n
                    ],
                )  # fmt: skip
    document.save(str(path))


_DOCX_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")


def docx_text(text: str) -> str:
    """Word-XML verträgt keine Steuerzeichen (etwa Seitenumbrüche aus pdftotext)."""
    return _DOCX_ILLEGAL.sub("", text)


def export_report(
    project: Project, report: EvaluationReport | None, path: Path, lang: str = "de"
) -> None:
    """Methoden-Report als Word oder HTML (nach Endung), auf Deutsch oder Englisch."""
    with output_language(lang):
        if path.suffix.lower() == ".docx":
            report_docx(project, report, path)
            fmt = "report_docx"
        else:
            path.write_text(report_html(project, report), encoding="utf-8")
            fmt = "report_html"
    project.store.log("exported", format=fmt, file=path.name, lang=lang)
