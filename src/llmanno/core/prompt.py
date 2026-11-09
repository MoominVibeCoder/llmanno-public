"""Prompt-Templates (DE/EN) und Rendering.

Aufbau: System-Nachricht = Regeln, Codebuch verbatim, Code-Liste, Ausgabeformat.
User-Nachricht = Metadaten, Kontext, Zieltext. Das Codebuch steht vorn und ist für alle
Einheiten identisch, damit Ollama und OpenAI-kompatible Server das Präfix cachen können.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Literal

from llmanno.core.config import CodebookConfig
from llmanno.core.pseudonyms import Pseudonymizer
from llmanno.core.units import Item, Line

TEMPLATE_VERSION = "2"  # 2: leere Liste bei Multi-Label ohne Restkategorie

Language = Literal["de", "en"]
Mode = Literal["single", "multi"]
UnitMode = Literal["isolated", "context", "holistic"]

_TEXTS: dict[str, dict[str, str]] = {
    "de": {
        "intro": (
            "Du kodierst Texte in einer deduktiven Inhaltsanalyse. Grundlage ist ausschließlich "
            "das folgende Codebuch. Wende seine Definitionen, Kodierregeln und Abgrenzungen "
            "wörtlich an und bringe kein eigenes Vorwissen über die Kategorien ein."
        ),
        "codebook_tag": "codebuch",
        "codes_head": (
            "Zulässige Codes (nur diese Kürzel verwenden; die Reihenfolge ist zufällig und "
            "bedeutet nichts):"
        ),
        "steps_head": "Vorgehen:",
        "read_target": "Lies den Zieltext.",
        "read_unit": (
            "Lies die Einheit. Sie besteht aus nummerierten Zeilen und wird als Ganzes kodiert."
        ),
        "context_rule": (
            "Kontext dient nur dem Verständnis; kodiert wird ausschließlich der Zieltext."
        ),
        "meta_rule": "Metadaten beschreiben die Einheit und werden nicht kodiert.",
        "check": "Prüfe die in Frage kommenden Codes gegen die Kriterien des Codebuchs.",
        "rationale": (
            "Begründe deine Entscheidung knapp (höchstens {max_words} Wörter) mit Bezug auf das "
            "Codebuch."
        ),
        "select_single_1": "Nenne den am besten passenden Code.",
        "select_single": (
            "Nenne den am besten passenden Code und bis zu {alternatives} plausible Alternativen, "
            "absteigend sortiert."
        ),
        "select_multi": "Nenne alle zutreffenden Codes, höchstens {k}, absteigend nach Sicherheit.",
        "abstain_alone": "{abstain} steht immer allein.",
        "multi_none": "Trifft keiner der Codes zu, bleibt die Liste candidates leer.",
        "evidence": (
            "Gib für jeden genannten Code 1 bis {max_sw} Signalwörter an: Wörter oder kurze "
            "Wortgruppen (höchstens 6 Wörter), die du zeichengenau aus {source} kopierst und die "
            "für diesen Code sprechen. Nichts umformulieren, nichts aus dem Kontext."
        ),
        "evidence_source_target": "dem Zieltext",
        "evidence_source_unit": "der Einheit",
        "abstain_evidence": "Für {abstain} sind keine Signalwörter nötig.",
        "confidence_single": (
            "Gib für jeden Code eine Konfidenz von 0 bis 100 an: die Wahrscheinlichkeit, dass "
            "dieser Code der richtige ist. Die Summe ist höchstens 100."
        ),
        "confidence_multi": (
            "Gib für jeden Code eine Konfidenz von 0 bis 100 an: die Wahrscheinlichkeit, dass "
            "dieser Code zutrifft."
        ),
        "format_head": "Antworte ausschließlich mit einem JSON-Objekt in genau diesem Format:",
        "fmt_rationale": "<kurze Begründung>",
        "fmt_evidence": "<Signalwort, wörtlich kopiert>",
        "fmt_code": "<Kürzel>",
        "meta_tag": "metadaten",
        "before_tag": "kontext_davor",
        "after_tag": "kontext_danach",
        "target_tag": "zieltext",
        "unit_tag": "einheit",
        "speaker_attr": "sprecher",
        "examples_head": (
            "Beispiele aus denselben Daten, von Menschen kodiert. Sie veranschaulichen das "
            "Codebuch; bei Widerspruch gilt das Codebuch."
        ),
        "examples_tag": "beispiele",
        "example_tag": "beispiel",
        "example_codes": "codes",
        "example_hidden": "[Text der Einheit {id}, in diesem Export nicht enthalten]",
        "repair_json": (
            "Deine Antwort war kein gültiges JSON im verlangten Format ({detail}). Antworte "
            "erneut, ausschließlich mit dem JSON-Objekt."
        ),
        "repair_codes": (
            "Diese Codes sind nicht zulässig: {detail}. Verwende nur die Kürzel aus der Liste und "
            "antworte erneut, ausschließlich mit dem JSON-Objekt."
        ),
        "repair_rules": (
            "Deine Antwort verletzt eine Regel: {detail}. Antworte erneut, ausschließlich mit dem "
            "JSON-Objekt."
        ),
        "repair_evidence": (
            "Diese Signalwörter stehen nicht wörtlich in {source}: {detail}. Kopiere Signalwörter "
            "zeichengenau und antworte erneut, ausschließlich mit dem JSON-Objekt."
        ),
    },
    "en": {
        "intro": (
            "You code texts in a deductive content analysis. Your only basis is the codebook "
            "below. Apply its definitions, coding rules and distinctions literally and do not "
            "bring in your own prior knowledge about the categories."
        ),
        "codebook_tag": "codebook",
        "codes_head": (
            "Allowed codes (use only these identifiers; their order is random and carries no "
            "meaning):"
        ),
        "steps_head": "Procedure:",
        "read_target": "Read the target text.",
        "read_unit": "Read the unit. It consists of numbered lines and is coded as a whole.",
        "context_rule": "Context is only for understanding; code only the target text.",
        "meta_rule": "Metadata describe the unit and are not coded.",
        "check": "Check the candidate codes against the criteria of the codebook.",
        "rationale": (
            "Justify your decision briefly (at most {max_words} words) with reference to the "
            "codebook."
        ),
        "select_single_1": "Give the best-fitting code.",
        "select_single": (
            "Give the best-fitting code and up to {alternatives} plausible alternatives, in "
            "descending order."
        ),
        "select_multi": "Give all applicable codes, at most {k}, in descending order of certainty.",
        "abstain_alone": "{abstain} always stands alone.",
        "multi_none": "If none of the codes applies, leave the candidates list empty.",
        "evidence": (
            "For each code, give 1 to {max_sw} signal words: words or short phrases (at most 6 "
            "words) copied character by character from {source} that speak for this code. Do not "
            "paraphrase, do not copy from the context."
        ),
        "evidence_source_target": "the target text",
        "evidence_source_unit": "the unit",
        "abstain_evidence": "{abstain} needs no signal words.",
        "confidence_single": (
            "Give each code a confidence from 0 to 100: the probability that this code is the "
            "correct one. The sum is at most 100."
        ),
        "confidence_multi": (
            "Give each code a confidence from 0 to 100: the probability that this code applies."
        ),
        "format_head": "Answer only with a JSON object in exactly this format:",
        "fmt_rationale": "<short justification>",
        "fmt_evidence": "<signal word, copied verbatim>",
        "fmt_code": "<identifier>",
        "meta_tag": "metadata",
        "before_tag": "context_before",
        "after_tag": "context_after",
        "target_tag": "target",
        "unit_tag": "unit",
        "speaker_attr": "speaker",
        "examples_head": (
            "Examples from the same data, coded by humans. They illustrate the codebook; "
            "where they conflict, the codebook prevails."
        ),
        "examples_tag": "examples",
        "example_tag": "example",
        "example_codes": "codes",
        "example_hidden": "[text of unit {id}, not included in this export]",
        "repair_json": (
            "Your answer was not valid JSON in the required format ({detail}). Answer again, "
            "only with the JSON object."
        ),
        "repair_codes": (
            "These codes are not allowed: {detail}. Use only identifiers from the list and answer "
            "again, only with the JSON object."
        ),
        "repair_rules": (
            "Your answer breaks a rule: {detail}. Answer again, only with the JSON object."
        ),
        "repair_evidence": (
            "These signal words do not appear verbatim in {source}: {detail}. Copy signal words "
            "character by character and answer again, only with the JSON object."
        ),
    },
}


# Umformulierte Anweisungen für die Prompt-Sensitivität (Barrie, Palaiologou & Törnberg 2024).
# Nur der Wortlaut ändert sich; Aufgabe, Regeln für Signalwörter und Konfidenz sowie das
# Ausgabeformat bleiben gleich, damit Unterschiede auf die Formulierung zurückgehen.
VARIANT_TEXTS: dict[str, dict[str, dict[str, str]]] = {
    "u1": {
        "de": {
            "intro": (
                "Deine Aufgabe ist die deduktive Kodierung von Texten. Maßgeblich ist allein das "
                "unten stehende Codebuch: Halte dich genau an seine Definitionen, Regeln und "
                "Abgrenzungen und nutze kein eigenes Wissen über die Kategorien."
            ),
            "codes_head": (
                "Erlaubte Codes (verwende ausschließlich diese Kürzel; die Reihenfolge ist "
                "zufällig gewählt):"
            ),
            "steps_head": "So gehst du vor:",
            "read_target": "Lies zuerst den Zieltext.",
            "context_rule": "Der Kontext hilft beim Verstehen; kodiere aber nur den Zieltext.",
            "check": "Gleiche die infrage kommenden Codes mit den Kriterien im Codebuch ab.",
            "rationale": (
                "Begründe die Entscheidung in höchstens {max_words} Wörtern und beziehe dich dabei "
                "auf das Codebuch."
            ),
            "select_single_1": "Gib den Code an, der am besten passt.",
            "select_single": (
                "Gib den Code an, der am besten passt, und bis zu {alternatives} weitere plausible "
                "Codes, nach Passung absteigend."
            ),
            "select_multi": (
                "Gib alle Codes an, die zutreffen, höchstens {k}, geordnet nach absteigender "
                "Sicherheit."
            ),
            "format_head": "Gib als Antwort nur ein JSON-Objekt in genau dieser Form aus:",
        },
        "en": {
            "intro": (
                "Your task is to code texts deductively. The codebook below is your only basis: "
                "follow its definitions, rules and distinctions exactly and do not use your own "
                "knowledge about the categories."
            ),
            "codes_head": "Permitted codes (use these identifiers only; the order is random):",
            "steps_head": "How to proceed:",
            "read_target": "First read the target text.",
            "context_rule": "The context helps with understanding, but code only the target text.",
            "check": "Compare the candidate codes with the criteria in the codebook.",
            "rationale": (
                "Justify the decision in at most {max_words} words, referring to the codebook."
            ),
            "select_single_1": "State the code that fits best.",
            "select_single": (
                "State the code that fits best and up to {alternatives} further plausible codes, "
                "by descending fit."
            ),
            "select_multi": (
                "State all codes that apply, at most {k}, ordered by descending certainty."
            ),
            "format_head": "Output only a JSON object in exactly this form:",
        },
    },
    "u2": {
        "de": {
            "intro": (
                "Aufgabe: deduktive Inhaltsanalyse. Kodiere ausschließlich nach dem folgenden "
                "Codebuch und wende dessen Definitionen, Kodierregeln und Abgrenzungen wortgetreu "
                "an. Eigenes Vorwissen über die Kategorien bleibt außen vor."
            ),
            "codes_head": (
                "Codes, die vergeben werden dürfen (nur diese Kürzel; ihre Reihenfolge hat keine "
                "Bedeutung):"
            ),
            "steps_head": "Arbeitsschritte:",
            "read_target": "Lies den Zieltext sorgfältig.",
            "context_rule": (
                "Kodiert wird nur der Zieltext; der Kontext dient allein dem Verständnis."
            ),
            "check": "Prüfe, welche Codes nach den Kriterien des Codebuchs in Betracht kommen.",
            "rationale": (
                "Gib eine knappe Begründung mit Bezug auf das Codebuch (maximal {max_words} "
                "Wörter)."
            ),
            "select_single_1": "Wähle den passendsten Code.",
            "select_single": (
                "Wähle den passendsten Code und nenne bis zu {alternatives} plausible Alternativen "
                "in absteigender Reihenfolge."
            ),
            "select_multi": (
                "Wähle alle zutreffenden Codes aus, maximal {k}, sortiert nach absteigender "
                "Sicherheit."
            ),
            "format_head": (
                "Deine Antwort besteht nur aus einem JSON-Objekt in exakt diesem Format:"
            ),
        },
        "en": {
            "intro": (
                "Task: deductive content analysis. Code strictly according to the following "
                "codebook and apply its definitions, coding rules and distinctions verbatim. "
                "Leave aside any prior knowledge about the categories."
            ),
            "codes_head": (
                "Codes that may be assigned (only these identifiers; their order has no meaning):"
            ),
            "steps_head": "Steps:",
            "read_target": "Read the target text carefully.",
            "context_rule": (
                "Only the target text is coded; the context serves understanding only."
            ),
            "check": "Check which codes come into question under the codebook's criteria.",
            "rationale": (
                "Give a brief justification with reference to the codebook (at most {max_words} "
                "words)."
            ),
            "select_single_1": "Choose the most fitting code.",
            "select_single": (
                "Choose the most fitting code and list up to {alternatives} plausible "
                "alternatives in descending order."
            ),
            "select_multi": (
                "Choose all applicable codes, at most {k}, sorted by descending certainty."
            ),
            "format_head": "Your answer consists only of a JSON object in exactly this format:",
        },
    },
    "u3": {
        "de": {
            "intro": (
                "Du arbeitest als geschulte Kodierperson in einer deduktiven Inhaltsanalyse. "
                "Entscheide nur auf Grundlage des folgenden Codebuchs. Wende Definitionen, "
                "Kodierregeln und Abgrenzungen so an, wie sie dort stehen, und lass eigenes "
                "Vorwissen über die Kategorien beiseite."
            ),
            "codes_head": (
                "Diese Codes stehen zur Wahl (nur die Kürzel verwenden; die Reihenfolge ist "
                "zufällig):"
            ),
            "steps_head": "Ablauf:",
            "read_target": "Lies den zu kodierenden Zieltext.",
            "context_rule": "Nutze den Kontext nur zum Verstehen und kodiere allein den Zieltext.",
            "check": "Vergleiche die möglichen Codes mit den Kriterien des Codebuchs.",
            "rationale": (
                "Erkläre kurz (bis zu {max_words} Wörter), warum der Code nach dem Codebuch passt."
            ),
            "select_single_1": "Bestimme den Code, der am besten passt.",
            "select_single": (
                "Bestimme den am besten passenden Code und nenne bis zu {alternatives} plausible "
                "Alternativen, absteigend geordnet."
            ),
            "select_multi": (
                "Bestimme alle zutreffenden Codes, höchstens {k}, von der sichersten zur "
                "unsichersten Entscheidung."
            ),
            "format_head": "Antworte nur mit einem JSON-Objekt, das genau so aufgebaut ist:",
        },
        "en": {
            "intro": (
                "You work as a trained coder in a deductive content analysis. Decide solely on the "
                "basis of the following codebook. Apply definitions, coding rules and distinctions "
                "as they are written there, and set aside your own prior knowledge about the "
                "categories."
            ),
            "codes_head": (
                "These codes are available (use only the identifiers; the order is random):"
            ),
            "steps_head": "Process:",
            "read_target": "Read the target text to be coded.",
            "context_rule": (
                "Use the context only for understanding and code the target text alone."
            ),
            "check": "Compare the possible codes with the criteria of the codebook.",
            "rationale": (
                "Explain briefly (up to {max_words} words) why the code fits according to the "
                "codebook."
            ),
            "select_single_1": "Determine the code that fits best.",
            "select_single": (
                "Determine the best-fitting code and list up to {alternatives} plausible "
                "alternatives, in descending order."
            ),
            "select_multi": (
                "Determine all applicable codes, at most {k}, from the most to the least certain."
            ),
            "format_head": "Respond only with a JSON object structured exactly like this:",
        },
    },
}


@dataclass(frozen=True)
class PromptSpec:
    """Alles, was die System-Nachricht festlegt. Für alle Einheiten eines Laufs gleich."""

    language: Language
    mode: Mode
    k: int
    unit_mode: UnitMode
    has_meta: bool
    codebook_text: str
    codebook: CodebookConfig
    max_signal_words: int
    max_rationale_words: int
    permute_codes: bool
    variant: str = ""  # "" = Originalwortlaut, sonst ein Schlüssel aus VARIANT_TEXTS
    pseudonyms: Pseudonymizer | None = None  # Namen vor dem Senden ersetzen
    # Die Namensliste fehlt (Lese- und Exportwege): Nutzer-Nachrichten nicht erzeugen, sie
    # enthielten die Klarnamen.
    names_missing: bool = False
    # Beispiele aus Dev (Few-Shot): Einheit und ihre Gold-Codes; stehen in der System-Nachricht
    examples: tuple[tuple[Item, tuple[str, ...]], ...] = ()


@dataclass(frozen=True)
class Prompt:
    system: str
    user: str

    def messages(self) -> list[dict[str, str]]:
        return [{"role": "system", "content": self.system}, {"role": "user", "content": self.user}]


def render(spec: PromptSpec, item: Item, seed: int) -> Prompt:
    return Prompt(render_system(spec, seed), render_user(spec, item))


def render_system(spec: PromptSpec, seed: int, *, example_texts: bool = True) -> str:
    """`example_texts`: False ersetzt die Texte der Beispiele durch einen Platzhalter, für
    Exporte ohne Rohtexte (die Beispiele sind Daten)."""
    variant = VARIANT_TEXTS.get(spec.variant, {}).get(spec.language, {})
    t = {**_TEXTS[spec.language], **variant}
    cb = spec.codebook
    codes = cb.active_codes()
    if spec.permute_codes:
        codes = codes[:]
        random.Random(seed).shuffle(codes)
    # Die Restkategorie steht immer am Ende, wie „keine der genannten“ in Fragebögen.
    code_lines = [f"- {c.id}: {c.label}" if c.label else f"- {c.id}" for c in codes]
    if cb.abstain:
        code_lines.append(f"- {cb.abstain_id}: {cb.abstain_label}")

    holistic = spec.unit_mode == "holistic"
    read = [t["read_unit"] if holistic else t["read_target"]]
    if spec.unit_mode == "context":
        read.append(t["context_rule"])
    if spec.has_meta:
        read.append(t["meta_rule"])

    if spec.mode == "single":
        select = (
            t["select_single_1"]
            if spec.k == 1
            else t["select_single"].format(alternatives=spec.k - 1)
        )
    else:
        select = t["select_multi"].format(k=spec.k)
        if cb.abstain:
            select += " " + t["abstain_alone"].format(abstain=cb.abstain_id)
        else:
            select += " " + t["multi_none"]

    source = t["evidence_source_unit"] if holistic else t["evidence_source_target"]
    evidence = t["evidence"].format(max_sw=spec.max_signal_words, source=source)
    if cb.abstain:
        evidence += " " + t["abstain_evidence"].format(abstain=cb.abstain_id)

    steps = [
        " ".join(read),
        t["check"],
        t["rationale"].format(max_words=spec.max_rationale_words),
        select,
        evidence,
        t[f"confidence_{spec.mode}"],
    ]
    fmt = (
        "{\n"
        f'  "rationale": "{t["fmt_rationale"]}",\n'
        '  "candidates": [\n'
        f'    {{"evidence": ["{t["fmt_evidence"]}"], "code": "{t["fmt_code"]}", '
        '"confidence": <0-100>}\n'  # keine konkrete Zahl: sie würde die Angaben verankern
        "  ]\n"
        "}"
    )
    tag = t["codebook_tag"]
    examples = [_examples(spec, t, example_texts)] if spec.examples else []
    return "\n\n".join(
        [
            t["intro"],
            f"<{tag}>\n{spec.codebook_text.strip()}\n</{tag}>",
            t["codes_head"] + "\n" + "\n".join(code_lines),
            *examples,
            t["steps_head"] + "\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1)),
            t["format_head"] + "\n" + fmt,
        ]
    )


def _examples(spec: PromptSpec, t: dict[str, str], texts: bool) -> str:
    """Die Beispiele wie Nutzer-Nachrichten, jeweils mit den Gold-Codes."""
    parts = []
    for item, codes in spec.examples:
        content = (
            _unit_blocks(spec, item, t) if texts else t["example_hidden"].format(id=item.item_id)
        )
        attrs = {t["example_codes"]: ", ".join(codes)}
        parts.append(_block(t["example_tag"], content, **attrs))
    tag = t["examples_tag"]
    return t["examples_head"] + f"\n<{tag}>\n" + "\n".join(parts) + f"\n</{tag}>"


def render_user(spec: PromptSpec, item: Item) -> str:
    return _unit_blocks(spec, item, _TEXTS[spec.language])


def _unit_blocks(spec: PromptSpec, item: Item, t: dict[str, str]) -> str:
    """Metadaten, Kontext und Zieltext einer Einheit, Namen ersetzt."""
    if spec.names_missing:
        raise ValueError(
            "Die Namensliste fehlt oder wurde außerhalb von llmanno geändert; ohne sie gingen "
            "die Klarnamen hinaus."
        )
    if spec.pseudonyms is not None:
        item = spec.pseudonyms.item(item)
    blocks = []
    if item.meta:
        meta = "\n".join(f"{key}: {value}" for key, value in item.meta.items())
        blocks.append(_block(t["meta_tag"], meta))
    if item.before:
        blocks.append(_block(t["before_tag"], _lines(item.before)))
    if spec.unit_mode == "holistic":
        blocks.append(_block(t["unit_tag"], _lines(item.lines), id=item.item_id))
    else:
        [line] = item.lines
        attrs: dict[str, Any] = {"id": line.unit_id}
        if line.speaker:
            attrs[t["speaker_attr"]] = line.speaker
        blocks.append(_block(t["target_tag"], line.text, **attrs))
    if item.after:
        blocks.append(_block(t["after_tag"], _lines(item.after)))
    return "\n".join(blocks)


def repair_message(language: Language, kind: str, detail: str, unit_mode: UnitMode) -> str:
    t = _TEXTS[language]
    source = t["evidence_source_unit" if unit_mode == "holistic" else "evidence_source_target"]
    return t[f"repair_{kind}"].format(detail=detail, source=source)


def estimate_tokens(text: str) -> int:
    """Bewusst pessimistisch (3 Zeichen pro Token), damit num_ctx nie zu klein gewählt wird."""
    return math.ceil(len(text) / 3)


def _lines(lines: tuple[Line, ...]) -> str:
    return "\n".join(
        f"[{ln.unit_id}] {ln.speaker}: {ln.text}" if ln.speaker else f"[{ln.unit_id}] {ln.text}"
        for ln in lines
    )


def _block(tag: str, content: str, **attrs: Any) -> str:
    rendered = "".join(f' {key}="{value}"' for key, value in attrs.items())
    return f"<{tag}{rendered}>\n{content}\n</{tag}>"
