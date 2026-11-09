"""Präregistrierung als Markdown oder Word, Deutsch oder Englisch: das eingefrorene Instrument
und der Analyseplan.

Gedacht zum Hochladen bei OSF o. Ä. vor der Test-Auswertung. Ohne Freeze entsteht ein Entwurf.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from llmanno.core.config import CodingConfig
from llmanno.core.engine import prepare_prompts, sent_reasoning_effort
from llmanno.core.i18n import output_language, t
from llmanno.core.metrics.alt_test import MIN_ANNOTATORS
from llmanno.core.metrics.bootstrap import DEFAULT_RESAMPLES
from llmanno.core.project import Project
from llmanno.core.prompt import (
    TEMPLATE_VERSION,
    Prompt,
    PromptSpec,
    render_system,
    render_user,
)
from llmanno.core.schema import response_schema
from llmanno.core.store import sha256_text
from llmanno.core.units import Item
from llmanno.core.validation import count_test_evaluations, grouped_units, split_sha256

UNIT_MODES = {
    "isolated": "Zeile isoliert",
    "context": "Zeile mit Kontextfenster",
    "holistic": "Gruppe holistisch",
}
THINKING = {None: "–", False: "aus", True: "an"}
UNIT_MODES_EN = {
    "isolated": "single row",
    "context": "row with context window",
    "holistic": "whole group",
}
THINKING_EN = {None: "–", False: "off", True: "on"}


def unit_mode_label(mode: str) -> str:
    return t(UNIT_MODES[mode], UNIT_MODES_EN[mode])


def thinking_label(value: bool | None) -> str:
    return t(THINKING[value], THINKING_EN[value])


def _fence(text: str, language: str = "") -> str:
    fence = "````" if "```" in text else "```"
    return f"{fence}{language}\n{text.rstrip()}\n{fence}"


def _header(project: Project) -> list[str]:
    state = project.config.freeze
    lines = [t("# Präregistrierung", "# Preregistration") + f": {project.config.name}", ""]
    from llmanno.core.demo import demo_note, is_demo  # demo importiert engine

    if is_demo(project):
        lines += [f"> **{demo_note()}**", ""]
    if state is None:
        lines += [
            t(
                "> **Entwurf.** Das Instrument ist noch nicht eingefroren.",
                "> **Draft.** The instrument is not frozen yet.",
            ),
            "",
        ]
    else:
        frozen = state.frozen_at.strftime("%Y-%m-%d %H:%M UTC")
        lines += [
            t("- Instrument-Hash: ", "- Instrument hash: ") + f"`{state.instrument_hash}`",
            t("- Eingefroren: ", "- Frozen: ") + frozen,
        ]
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return lines + [
        t(f"- Erstellt: {now} mit llmanno ", f"- Created: {now} with llmanno ")
        + version("llmanno"),
        t("- Prompt-Template: Version ", "- Prompt template: version ") + str(TEMPLATE_VERSION),
        t("- Test-Auswertungen bis jetzt: ", "- Test evaluations so far: ")
        + str(count_test_evaluations(project)),
        "",
    ]


def _data(project: Project, n_items: int) -> list[str]:
    cfg = project.config
    assert cfg.data is not None
    data, units = cfg.data, cfg.coding.units
    unit = unit_mode_label(units.mode)
    if units.mode == "context":
        unit += t(
            f" ({units.n_before} davor, {units.n_after} danach, innerhalb der Gruppe)",
            f" ({units.n_before} before, {units.n_after} after, within the group)",
        )
    gold = ", ".join(data.mapping.gold) or t("keine", "none")
    if data.mapping.consensus:
        gold += t("; Konsens: ", "; consensus: ") + data.mapping.consensus
    return [
        t("## 1. Daten", "## 1. Data"),
        "",
        t(
            f"- Quelle: {data.source_name}, Blatt „{data.sheet}“",
            f"- Source: {data.source_name}, sheet “{data.sheet}”",
        ),
        t("- SHA-256 der Datei: ", "- SHA-256 of the file: ") + f"`{data.file_sha256}`",
        t(
            f"- Zeilen: {data.n_units}; Kodiereinheiten: {n_items}",
            f"- Rows: {data.n_units}; coding units: {n_items}",
        ),
        t("- Analyseeinheit: ", "- Unit of analysis: ") + unit,
        t("- Referenzkodierungen: ", "- Reference codings: ") + gold,
        "",
    ]


def _split(project: Project) -> list[str]:
    cfg = project.config
    parts = project.store.split()
    lines = [t("## 2. Dev/Test-Split", "## 2. Dev/test split"), ""]
    if not parts:
        return lines + [
            t(
                "- Kein Split: Alle Einheiten mit Gold dienen der Validierung.",
                "- No split: all units with gold serve for validation.",
            ),
            "",
        ]
    counts = Counter(parts.values())
    grouped = grouped_units(cfg)
    drawing = t("gruppenweise", "by group") if grouped else t("je Einheit", "per unit")
    if cfg.split.stratify and not grouped:
        drawing += t(", geschichtet nach Referenzcode", ", stratified by reference code")
    share = f"{cfg.split.dev_share:.0%}"
    return lines + [
        t(
            f"- Dev: {counts['dev']} Einheiten, Test: {counts['test']} Einheiten",
            f"- Dev: {counts['dev']} units, test: {counts['test']} units",
        ),
        t(
            f"- Anteil Dev {share}, Seed {cfg.split.seed}, Ziehung {drawing}",
            f"- Dev share {share}, seed {cfg.split.seed}, drawn {drawing}",
        ),
        f"- Split-SHA-256: `{split_sha256(parts)}`",
        t(
            "- Codebuch und Prompt wurden nur auf Dev geschärft; Test war bis zum Freeze gesperrt.",
            "- Codebook and prompt were refined on dev only; test was locked until the freeze.",
        ),
        "",
    ]


def _codebook(project: Project) -> list[str]:
    cfg = project.config
    cb = cfg.codebook
    assert cb is not None
    text = project.codebook_text or ""
    abstain = f"{cb.abstain_id} ({cb.abstain_label})" if cb.abstain else t("keine", "none")
    yes, no = t("ja", "yes"), t("nein", "no")
    rows = []
    for c in cb.codes:
        rank = "" if c.rank is None else c.rank
        rows.append(f"| {c.id} | {c.label} | {rank} | {yes if c.active else no} |")
    return [
        t("## 3. Codebuch", "## 3. Codebook"),
        "",
        t("- Datei: ", "- File: ") + str(cb.source_name),
        t("- SHA-256 des verwendeten Textes: ", "- SHA-256 of the text used: ")
        + f"`{sha256_text(text)}`",
        t(
            f"- Skalenniveau: {cb.scale}; Modus: {cfg.mode}, bis zu {cfg.k} Codes je Einheit",
            f"- Level of measurement: {cb.scale}; mode: {cfg.mode}, up to {cfg.k} codes per unit",
        ),
        t("- Restkategorie: ", "- Residual category: ") + abstain,
        "",
        t("| Kürzel | Label | Rang | aktiv |", "| Code | Label | Rank | active |"),
        "|---|---|---|---|",
        *rows,
        "",
        t("Volltext, so wie er im Prompt steht:", "Full text as it appears in the prompt:"),
        "",
        _fence(text),
        "",
    ]


def _models(project: Project) -> list[str]:
    cfg = project.config
    state = cfg.freeze
    coding = cfg.coding
    lines = [
        t("## 4. Modelle und Parameter", "## 4. Models and parameters"),
        "",
        t(
            "| Modell | Anbieter | Datenschutz | Temperatur | max. Tokens | Thinking "
            "| Reasoning-Aufwand | Lauf-Hash |",
            "| Model | Provider | Privacy | Temperature | max tokens | Thinking "
            "| Reasoning effort | Run hash |",
        ),
        "|---|---|---|---|---|---|---|---|",
    ]
    for model in cfg.panel:
        provider = cfg.provider(model.provider)
        run_hash = state.model_hashes.get(model.key, "") if state else ""
        temperature = model.effective_temperature(coding.n_runs)
        shown = (
            t("nach Vorgabe des Anbieters", "set by the provider")
            if temperature is None
            else temperature
        )
        lines.append(
            f"| {model.model} | {provider.name} ({provider.base_url}) | {provider.privacy_level} | "
            f"{shown} | {model.max_tokens} | {thinking_label(model.thinking)} | "
            f"{sent_reasoning_effort(model, provider) or '–'} | `{run_hash}` |"
        )
    if coding.pseudonyms_sha256:
        lines += [
            "",
            t(
                "Namen werden vor dem Senden durch Pseudonyme ersetzt (ganze Wörter in Text, "
                "Sprecher und Metadaten; die Namensliste wird nicht veröffentlicht).",
                "Names are replaced by pseudonyms before sending (whole words in text, "
                "speaker and metadata; the list of names is not published).",
            ),
        ]
    agree = coding.adaptive_min_agree
    runs = t(
        f"- Läufe je Einheit und Modell: {coding.n_runs}",
        f"- Runs per unit and model: {coding.n_runs}",
    )
    if coding.adaptive:
        runs += t(
            f", adaptiv: Stopp nach {agree} einstimmigen Läufen",
            f", adaptive: stop after {agree} agreeing runs",
        )
    order = (
        t("pro Lauf gemischt", "permuted per run") if coding.permute_codes else t("fest", "fixed")
    )
    repair = t("an", "on") if coding.evidence_repair else t("aus", "off")
    return lines + [
        "",
        runs,
        t(
            f"- Seeds: Basis {coding.base_seed} + Laufindex; Code-Reihenfolge im Prompt {order}",
            f"- Seeds: base {coding.base_seed} + run index; code order in the prompt {order}",
        ),
        t(
            f"- Signalwörter: höchstens {coding.max_signal_words} je Code",
            f"- Signal words: at most {coding.max_signal_words} per code",
        ),
        t(
            f"- Begründung: höchstens {coding.max_rationale_words} Wörter",
            f"- Rationale: at most {coding.max_rationale_words} words",
        ),
        t("- Evidenz-Repair: ", "- Evidence repair: ") + repair,
        *([examples_line(coding)] if coding.examples else []),
        t(
            "- Aggregation: Self-Consistency je Modell, gleich gewichtet über die Modelle; "
            "Multi-Label-Schwelle 0,5; Abstände unter 0,1 gelten als unentschieden",
            "- Aggregation: self-consistency per model, equally weighted across models; "
            "multi-label threshold 0.5; margins below 0.1 count as ties",
        ),
        "",
    ]


def examples_line(coding: CodingConfig) -> str:
    """Beispiele aus Dev (Few-Shot) für Präregistrierung und Report."""
    ids = ", ".join(f"{e.item_id} ({', '.join(e.codes)})" for e in coding.examples)
    n = len(coding.examples)
    return t(
        f"- Beispiele im Prompt (Few-Shot): {n} Dev-Einheiten mit Gold, aus allen Kennwerten "
        f"ausgeschlossen: {ids}",
        f"- Examples in the prompt (few-shot): {n} dev units with gold, excluded from all "
        f"metrics: {ids}",
    )


def _prompt(project: Project) -> list[str]:
    spec, items = prepare_prompts(project)
    cfg = project.config
    assert cfg.codebook is not None
    if not items:
        return []
    prompt = _example(spec, items[0], cfg.coding.base_seed)
    schema = json.dumps(response_schema(cfg.codebook.allowed_ids()), ensure_ascii=False, indent=2)
    return [
        "## 5. Prompt",
        "",
        t("System-Nachricht (erster Lauf):", "System message (first run):"),
        "",
        _fence(prompt.system),
        "",
        t(
            f"Beispiel einer User-Nachricht (Einheit {items[0].item_id}):",
            f"Example user message (unit {items[0].item_id}):",
        ),
        "",
        _fence(prompt.user)
        if prompt.user
        else t("(fehlt: Namensliste nicht vorhanden)", "(missing: list of names not available)"),
        "",
        t(
            "Ausgabeschema (Structured Outputs, sofern der Anbieter es unterstützt):",
            "Output schema (structured outputs, where the provider supports them):",
        ),
        "",
        _fence(schema, "json"),
        "",
    ]


def _analysis(project: Project) -> list[str]:
    cfg = project.config
    assert cfg.data is not None and cfg.codebook is not None
    coders = cfg.data.mapping.gold
    if cfg.data.mapping.consensus:
        reference = cfg.data.mapping.consensus
    elif len(coders) >= 2:
        reference = t("Mehrheit der Kodierenden", "majority of coders")
    else:
        reference = coders[0] if coders else "–"
    agreement = t(
        "prozentuale Übereinstimmung, Cohens κ, Gwets AC1, Krippendorffs α",
        "percent agreement, Cohen's κ, Gwet's AC1, Krippendorff's α",
    )
    if cfg.codebook.scale == "ordinal":
        agreement += t(" (ordinal) und quadratisch gewichtetes κ", " (ordinal) and quadratically "
                       "weighted κ")  # fmt: skip
    bootstrap = t(
        f"95-%-Perzentil-Bootstrap mit {DEFAULT_RESAMPLES} Ziehungen",
        f"95% percentile bootstrap with {DEFAULT_RESAMPLES} resamples",
    )
    if grouped_units(cfg):
        bootstrap += t(", gruppenweise", ", by group")
    if len(coders) >= MIN_ANNOTATORS:
        baseline = t(
            f"Mensch-Mensch-Baseline und Alt-Test (Calderon et al. 2025, ε = {cfg.gold.epsilon})",
            f"human–human baseline and alternative annotator test (Calderon et al. 2025, ε = "
            f"{cfg.gold.epsilon})",
        )
    else:
        baseline = t(
            "Mensch-Mensch-Baseline, sofern mehrere Kodierende vorliegen",
            "human–human baseline, if several coders are available",
        )
    return [
        t("## 6. Analyseplan", "## 6. Analysis plan"),
        "",
        t("- Primäre Referenz: ", "- Primary reference: ") + reference,
        t("- Übereinstimmung: ", "- Agreement: ") + agreement,
        t(
            "- Klassifikation: Accuracy, Macro-F1, Precision, Recall und F1 je Code, MCC, Top-k",
            "- Classification: accuracy, macro-F1, precision, recall and F1 per code, MCC, top-k",
        ),
        t(
            "- Je Code: κ (ja/nein) und Shaffers ρ (rhoR, Schwelle κ = 0,65)",
            "- Per code: κ (yes/no) and Shaffer's ρ (rhoR, threshold κ = 0.65)",
        ),
        t("- Konfidenzintervalle: ", "- Confidence intervals: ") + bootstrap,
        t(
            "- Prävalenz: Anteile je Code, Stuart-Maxwell-Test, McNemar je Code",
            "- Prevalence: shares per code, Stuart–Maxwell test, McNemar per code",
        ),
        t(
            "- Kalibrierung: ECE, Brier, AUROC für beide Konfidenzen",
            "- Calibration: ECE, Brier, AUROC for both confidences",
        ),
        f"- {baseline}",
        t(
            "- Begründungen des Modells werden als Plausibilisierung berichtet, nicht als "
            "Erklärung.",
            "- Model rationales are reported as plausibilizations, not as explanations.",
        ),
        "",
    ]


def preregistration(project: Project, lang: str = "de") -> str:
    _, items = prepare_prompts(project)
    with output_language(lang):
        sections = [
            _header(project),
            _data(project, len(items)),
            _split(project),
            _codebook(project),
            _models(project),
            _prompt(project),
            _analysis(project),
        ]
    return "\n".join(line for section in sections for line in section)


def markdown_docx(markdown: str, path: Path) -> None:
    """Das Markdown der Präregistrierung als Word-Datei: Überschriften, Listen, Tabellen,
    Code-Blöcke; Hervorhebungen (**, `) fallen weg."""
    from docx import Document
    from docx.shared import Pt

    from llmanno.core.export.report import docx_text  # report importiert prereg

    def plain(text: str) -> str:
        return docx_text(text.replace("**", "").replace("`", ""))

    document = Document()
    lines = markdown.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            fence = line[: len(line) - len(line.lstrip("`"))]
            end = lines.index(fence, i + 1) if fence in lines[i + 1 :] else len(lines)
            run = document.add_paragraph().add_run(docx_text("\n".join(lines[i + 1 : end])))
            run.font.name = "Consolas"
            run.font.size = Pt(8)
            i = end + 1
            continue
        if line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [plain(c.strip()) for c in lines[i].strip().strip("|").split("|")]
                if not all(set(c) <= set("-: ") for c in cells):  # Trennzeile
                    rows.append(cells)
                i += 1
            grid = document.add_table(rows=0, cols=len(rows[0]))
            grid.style = "Table Grid"
            for row in rows:
                for cell, text in zip(grid.add_row().cells, row, strict=False):
                    cell.text = text
            continue
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            document.add_heading(plain(line[level:].strip()), level=level - 1)
        elif line.startswith("- "):
            document.add_paragraph(plain(line[2:]), style="List Bullet")
        elif line.startswith("> "):
            document.add_paragraph().add_run(plain(line[2:])).italic = True
        elif line.strip():
            document.add_paragraph(plain(line))
        i += 1
    document.save(str(path))


def export_preregistration(project: Project, path: Path, lang: str = "de") -> None:
    """Markdown, oder Word bei der Endung .docx (für OSF und Ethikanträge üblich)."""
    text = preregistration(project, lang)
    if path.suffix.lower() == ".docx":
        markdown_docx(text, path)
    else:
        path.write_text(text, encoding="utf-8")
    state = project.config.freeze
    project.store.log(
        "exported",
        format="praeregistrierung",
        file=path.name,
        instrument_hash=state.instrument_hash if state else None,
    )


def _example(spec: PromptSpec, item: Item, seed: int) -> Prompt:
    """Erster Prompt für Präregistrierung und Report: Beispiele aus Dev nur mit ID (sie sind
    Daten); ohne Nutzer-Nachricht, wenn die Namensliste fehlt (sie enthielte die Klarnamen)."""
    system = render_system(spec, seed, example_texts=False)
    return Prompt(system, "" if spec.names_missing else render_user(spec, item))
