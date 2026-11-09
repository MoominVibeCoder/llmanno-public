"""Unstimmigkeiten: an welchen Code-Paaren die Modelle vom Gold oder voneinander abweichen,
mit ihren Begründungen. Auf Wunsch schlägt ein Modell des Panels eine schärfere Abgrenzung
vor; der Vorschlag ändert nichts am Codebuch, er wird nur protokolliert.

Der Prüfteil (Test) und die Beispiel-Einheiten im Prompt bleiben immer außen vor: Wer an
ihnen schärft, misst danach nicht mehr unabhängig."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
from pathlib import Path

import openpyxl

from llmanno.core.aggregate import decision
from llmanno.core.engine import prepare_prompts, result_basis, results, sent_reasoning_effort
from llmanno.core.evaluation import reference_labels
from llmanno.core.examples import seen_example_ids
from llmanno.core.export.xlsx import append_row
from llmanno.core.project import Project
from llmanno.core.prompt import estimate_tokens
from llmanno.core.providers import make_provider
from llmanno.core.providers.base import ChatRequest, Provider
from llmanno.core.store import CallRecord
from llmanno.core.units import Item, build_items

NONE = "—"  # kein Code: Multi-Label-Entscheidung ohne Gegenstück
SUGGESTED = "boundary_suggested"
MAX_CASES = 12  # Fälle je Vorschlag im Prompt
MIN_TOKENS = 2048  # Platz für Antwort (und Denken), auch wenn das Modell knapp eingestellt ist


@dataclass(frozen=True)
class Case:
    item: Item
    reference: tuple[str, ...] | None  # Gold-Codes, None: ohne Gold
    votes: dict[str, tuple[str, ...]]  # Modell → Entscheidung
    rationales: dict[str, str]  # Modell → Begründung zu seiner Entscheidung
    gold: bool  # ein Modell weicht an diesem Paar vom Gold ab
    models: bool  # die Modelle sind sich an diesem Paar uneinig


@dataclass(frozen=True)
class Pair:
    codes: tuple[str, str]
    cases: list[Case]

    @property
    def n_gold(self) -> int:
        return sum(c.gold for c in self.cases)

    @property
    def n_models(self) -> int:
        return sum(c.models for c in self.cases)


@dataclass(frozen=True)
class Overview:
    reference: str | None  # Name der Gold-Referenz
    dev_only: bool  # es gibt eine Aufteilung: nur der Übungsteil
    n_units: int  # Einheiten mit gültigem Ergebnis im Umfang
    models: list[str]
    pairs: list[Pair]  # die häufigsten zuerst


@dataclass(frozen=True)
class Suggestion:
    ts: datetime
    model: str
    codes: tuple[str, str]
    text: str


def code_pairs(a: tuple[str, ...], b: tuple[str, ...]) -> set[tuple[str, str]]:
    """Code-Paare, an denen zwei Entscheidungen auseinandergehen. Multi-Label: was die eine
    hat und die andere nicht; ohne Gegenstück gegen „kein Code“."""
    if set(a) == set(b):
        return set()
    only_a = sorted(set(a) - set(b)) or [NONE]
    only_b = sorted(set(b) - set(a)) or [NONE]
    return {tuple(sorted((x, y))) for x in only_a for y in only_b}  # type: ignore[misc]


def overview(project: Project) -> Overview:
    cfg = project.config
    if cfg.data is None or cfg.codebook is None or not cfg.panel:
        raise ValueError("Noch keine Ergebnisse: Daten, Codebuch und Modelle einrichten, kodieren.")
    settings = result_basis(project).settings  # gedeutet wie die Ergebnisse
    mode, abstain = settings["mode"], settings.get("abstain_id")
    parts, seen = project.store.split(), seen_example_ids(project)
    items = [
        i for i in build_items(project.store.units(), cfg.coding.units) if _in_scope(i, parts, seen)
    ]
    found = results(project, [i.item_id for i in items])
    name, reference = reference_labels(project, items)
    calls = _calls(project)
    cases: dict[tuple[str, str], list[Case]] = {}
    n_units = 0
    for item in items:
        result = found.get(item.item_id)
        if result is None or result.status != "ok":
            continue
        n_units += 1
        votes = {m: _normal(tuple(v), mode, abstain) for m, v in result.votes.items()}
        ref = _codes(reference.get(item.item_id))
        ref = _normal(ref, mode, abstain) if ref is not None else None
        against = set().union(*(code_pairs(ref, v) for v in votes.values())) if ref else set()
        between = set().union(*(code_pairs(a, b) for a, b in combinations(votes.values(), 2)))
        if not against | between:
            continue
        rationales = _rationales(calls.get(item.item_id, []), votes, mode, abstain)
        for pair in against | between:
            cases.setdefault(pair, []).append(
                Case(item, ref, votes, rationales, pair in against, pair in between)
            )
    pairs = sorted(
        (Pair(codes, found_cases) for codes, found_cases in cases.items()),
        key=lambda p: (-len(p.cases), -p.n_gold, p.codes),
    )
    return Overview(name, bool(parts), n_units, [m.key for m in cfg.panel], pairs)


def _in_scope(item: Item, parts: dict[str, str], seen: set[str]) -> bool:
    """Mit Aufteilung nur der Übungsteil, Prüfteil und Beispiele nie. Geprüft über die Einheit
    und ihre Zeilen: Wechselt die Analyseeinheit nach dem Split, passen die Schlüssel sonst
    nicht mehr, und Test-Texte rutschten durch."""
    keys = {item.item_id, *(line.unit_id for line in item.lines)}
    if keys & seen:
        return False
    return not parts or {parts[k] for k in keys if k in parts} == {"dev"}


def _normal(codes: tuple[str, ...], mode: str, abstain: str | None) -> tuple[str, ...]:
    """Wie das Ergebnis der Einheit: Im Multi-Modus fällt die Restkategorie neben anderen Codes
    weg, ohne Code steht sie allein."""
    if mode != "multi" or abstain is None:
        return codes
    return tuple(c for c in codes if c != abstain) or (abstain,)


def _codes(label: object) -> tuple[str, ...] | None:
    if label is None:
        return None
    return tuple(sorted(label)) if isinstance(label, frozenset) else (str(label),)


def _calls(project: Project) -> dict[str, list[CallRecord]]:
    """Gültige Aufrufe der Läufe, auf denen die Ergebnisse beruhen, je Einheit."""
    found: dict[str, list[CallRecord]] = {}
    for key, config_hash in result_basis(project).runs.items():
        for call in project.store.calls(model_key=key, config_hash=config_hash, with_raw=False):
            if call.status == "ok" and call.answer:
                found.setdefault(call.item_id, []).append(call)
    return found


def _rationales(
    calls: list[CallRecord], votes: dict[str, tuple[str, ...]], mode: str, abstain: str | None
) -> dict[str, str]:
    """Je Modell die Begründung eines Durchlaufs, der so entschied wie das Modell insgesamt,
    sonst die eines anderen Durchlaufs."""
    matching: dict[str, str] = {}
    other: dict[str, str] = {}
    for call in calls:
        vote = votes.get(call.model_key)
        rationale = str(call.answer.get("rationale") or "").strip()
        if vote is None or not rationale:
            continue
        same = _normal(tuple(decision(call.answer, mode)), mode, abstain) == vote
        (matching if same else other).setdefault(call.model_key, rationale)
    return {**other, **matching}


# --- Vorschlag einer schärferen Abgrenzung ---------------------------------------------------

_TEXTS = {
    "de": {
        "system": "Du unterstützt Forschende dabei, ein Codebuch für deduktive qualitative "
        "Kodierung zu präzisieren. Du änderst keine Codes und erfindest keine neuen; du "
        "schlägst Formulierungen vor, über die Menschen entscheiden.",
        "codebook": "## Codebuch",
        "problem": "## Abgrenzungsproblem\n\nZwischen {a} und {b} weichen Sprachmodelle vom "
        "menschlichen Gold oder voneinander ab. „{none}“ heißt: kein Code.",
        "cases": "## Fälle",
        "case": "### Fall {n} (Einheit {id})",
        "text": "Text:",
        "gold": "Gold (menschlich): {codes}",
        "no_gold": "Gold: keines",
        "vote": "Modell {model}: {codes}",
        "because": " – Begründung: {text}",
        "task": "## Aufgabe\n\n"
        "1. Woran liegt die Verwechslung? Benenne die unklare Stelle im Codebuch (ein bis drei "
        "Sätze).\n"
        "2. Schlage eine präzisere Abgrenzungsregel vor, die sich wörtlich ins Codebuch "
        "übernehmen lässt.\n"
        "3. Falls passend: je ein kurzes Ankerbeispiel aus den Fällen (Einheit nennen).\n"
        "4. Nenne Fälle, bei denen eher das Gold fraglich wirkt als das Codebuch.\n\n"
        "Antworte knapp auf Deutsch in Markdown.",
        "cut": "\n\n_(abgeschnitten: max. Tokens im Tab Modelle erhöhen)_",
    },
    "en": {
        "system": "You help researchers refine a codebook for deductive qualitative coding. You "
        "do not change codes or invent new ones; you suggest wording for humans to decide on.",
        "codebook": "## Codebook",
        "problem": "## Boundary problem\n\nBetween {a} and {b}, language models deviate from the "
        "human gold standard or from each other. “{none}” means: no code.",
        "cases": "## Cases",
        "case": "### Case {n} (unit {id})",
        "text": "Text:",
        "gold": "Gold (human): {codes}",
        "no_gold": "Gold: none",
        "vote": "Model {model}: {codes}",
        "because": " – rationale: {text}",
        "task": "## Task\n\n"
        "1. What causes the confusion? Name the unclear passage in the codebook (one to three "
        "sentences).\n"
        "2. Suggest a more precise boundary rule that can be copied into the codebook "
        "verbatim.\n"
        "3. If suitable: one short anchor example per code from the cases (name the unit).\n"
        "4. Name cases where the gold coding rather than the codebook seems questionable.\n\n"
        "Answer concisely in English in Markdown.",
        "cut": "\n\n_(truncated: increase max. tokens in the Models tab)_",
    },
}


def _language(project: Project) -> str:
    return prepare_prompts(project)[0].language


def suggestion_messages(project: Project, pair: Pair) -> list[dict[str, str]]:
    """Codebuch und die Fälle des Paars, Namen ersetzt wie beim Kodieren; Abweichungen vom Gold
    zuerst, höchstens MAX_CASES."""
    spec, _ = prepare_prompts(project, sending=True)
    if spec.names_missing:
        raise ValueError(
            "Die Namensliste fehlt oder wurde außerhalb von llmanno geändert; ohne sie gingen "
            "die Klarnamen hinaus."
        )
    t = _TEXTS[spec.language]
    names = spec.pseudonyms.apply if spec.pseudonyms is not None else (lambda s: s)
    a, b = pair.codes
    blocks = [
        t["codebook"],
        spec.codebook_text.strip(),
        t["problem"].format(a=a, b=b, none=NONE),
        t["cases"],
    ]
    chosen = sorted(pair.cases, key=lambda c: not c.gold)[:MAX_CASES]
    for n, case in enumerate(chosen, 1):
        text = "\n".join(
            f"{names(line.speaker)}: {names(line.text)}" if line.speaker else names(line.text)
            for line in case.item.lines
        )
        lines = [t["case"].format(n=n, id=case.item.item_id), t["text"], text]
        lines.append(
            t["gold"].format(codes=", ".join(case.reference)) if case.reference else t["no_gold"]
        )
        for model, vote in case.votes.items():
            line = t["vote"].format(model=model, codes=", ".join(vote) or NONE)
            if rationale := case.rationales.get(model):
                line += t["because"].format(text=names(rationale))
            lines.append(line)
        blocks.append("\n".join(lines))
    blocks.append(t["task"])
    return [
        {"role": "system", "content": t["system"]},
        {"role": "user", "content": "\n\n".join(blocks)},
    ]


def suggest(
    project: Project,
    pair: Pair,
    model_key: str,
    *,
    remote_consent: bool = False,
    provider_factory: Callable[..., Provider] = make_provider,
) -> str:
    """Vorschlag eines Panel-Modells für eine schärfere Abgrenzung; im Protokoll vermerkt."""
    cfg = project.config
    model = next((m for m in cfg.panel if m.key == model_key), None)
    if model is None:
        raise ValueError(f"„{model_key}“ ist nicht im Panel.")
    provider_config = cfg.provider(model.provider)
    remote = provider_config.privacy_level != "local"
    if remote and not remote_consent:
        raise PermissionError(f"Daten gingen an einen externen Anbieter: {provider_config.name}")
    messages = suggestion_messages(project, pair)
    max_tokens = max(model.max_tokens, MIN_TOKENS)
    num_ctx = None
    if provider_config.kind == "ollama":  # sonst kürzt Ollama still auf sein Standardfenster
        prompt = sum(estimate_tokens(m["content"]) for m in messages)
        num_ctx = max(math.ceil((prompt + max_tokens) * 1.1 / 1024) * 1024, 4096)
    request = ChatRequest(
        model=model.model,
        messages=messages,
        temperature=None if model.provider_temperature else model.temperature,
        max_tokens=max_tokens,
        num_ctx=num_ctx,
        think=model.thinking if provider_config.kind == "ollama" else None,
        reasoning_effort=sent_reasoning_effort(model, provider_config),
    )
    n_cases = min(len(pair.cases), MAX_CASES)
    if remote:  # vor dem Senden: auch eine fehlgeschlagene Anfrage hat Daten übermittelt
        project.store.log(
            "remote_consent",
            providers=[{"name": provider_config.name, "base_url": provider_config.base_url,
                        "privacy": provider_config.privacy_level}],
            n_items=n_cases,
            purpose=SUGGESTED,
        )  # fmt: skip
    provider = provider_factory(provider_config)
    try:
        response = provider.chat(request)
    finally:
        provider.close()
    if num_ctx and response.tokens_in and response.tokens_in + max_tokens > num_ctx:
        raise ValueError(
            f"Prompt vermutlich gekürzt ({response.tokens_in} Token bei num_ctx {num_ctx}); "
            "weniger Fälle oder ein kürzeres Codebuch."
        )
    text = response.text.strip()
    if not text:
        raise ValueError(
            "Leere Antwort"
            + (": abgeschnitten, max. Tokens im Tab Modelle erhöhen."
               if response.finish_reason == "length" else ".")
        )  # fmt: skip
    if response.finish_reason == "length":
        text += _TEXTS[_language(project)]["cut"]
    project.store.log(
        SUGGESTED,
        model=model_key,
        codes=list(pair.codes),
        cases=n_cases,
        remote_consent=remote,
        text_sha256=project.store.put_text(text),
    )
    return text


def suggestions(project: Project) -> list[Suggestion]:
    """Alle bisherigen Vorschläge, älteste zuerst."""
    store = project.store
    return [
        Suggestion(
            e.ts,
            e.details["model"],
            tuple(e.details["codes"]),
            store.get_text(e.details["text_sha256"]),
        )  # fmt: skip
        for e in store.audit()
        if e.event == SUGGESTED
    ]


# --- Arbeitsblatt ----------------------------------------------------------------------------


def export_overview(project: Project, found: Overview, path: Path) -> None:
    """Abgrenzungen mit letztem Vorschlag und alle Fälle mit Begründungen, als XLSX."""
    latest = {s.codes: s for s in suggestions(project)}
    book = openpyxl.Workbook()
    info = book.active
    info.title = "Info"
    for row in (
        ["Umfang", "Übungsteil (Dev)" if found.dev_only else "alle kodierten Einheiten"],
        ["Einheiten mit Ergebnis", found.n_units],
        ["Gold-Referenz", found.reference or "–"],
        ["Hinweis", "Prüfteil (Test) und Beispiel-Einheiten im Prompt sind nicht enthalten. "
                    "Vorschläge stammen von einem Sprachmodell und sind ungeprüft."],
    ):  # fmt: skip
        append_row(info, row)
    info.column_dimensions["A"].width = 24
    info.column_dimensions["B"].width = 100

    sheet = book.create_sheet("Abgrenzungen")
    append_row(sheet, ["Code A", "Code B", "Einheiten", "weicht vom Gold ab",
                       "Modelle uneinig", "letzter Vorschlag (LLM, ungeprüft)",
                       "Modell des Vorschlags"])  # fmt: skip
    for pair in found.pairs:
        s = latest.get(pair.codes)
        append_row(sheet, [*pair.codes, len(pair.cases), pair.n_gold, pair.n_models,
                           s.text if s else None, s.model if s else None])  # fmt: skip
    sheet.freeze_panes = "A2"

    sheet = book.create_sheet("Fälle")
    header = ["Code A", "Code B", "Einheit", "Text", "Gold", "weicht vom Gold ab",
              "Modelle uneinig"]  # fmt: skip
    for model in found.models:
        header += [f"{model}: Code", f"{model}: Begründung"]
    append_row(sheet, header)
    for pair in found.pairs:
        for case in pair.cases:
            row = [*pair.codes, case.item.item_id, case.item.target,
                   ", ".join(case.reference) if case.reference else None,
                   case.gold, case.models]  # fmt: skip
            for model in found.models:
                vote = case.votes.get(model)
                row += [", ".join(vote) if vote is not None else None,
                        case.rationales.get(model)]  # fmt: skip
            append_row(sheet, row)
    sheet.freeze_panes = "D2"
    book.save(path)
    project.store.log("exported", format="abgrenzungen", file=path.name)
