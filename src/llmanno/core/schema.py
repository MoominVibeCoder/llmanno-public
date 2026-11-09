"""Ausgabeschema und Prüfung der Modellantworten.

Die Feldreihenfolge ist Absicht: erst die Begründung, dann pro Kandidat erst die Belege, dann
Code und Konfidenz. Das Modell legt sich so erst nach einer kurzen Begründung fest
(Tam et al. 2024). Constrained Decoding hält die Reihenfolge des Schemas ein.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, ValidationError


# Nur Schlüsselwörter, die alle Backends verstehen. Längen und Wertebereiche prüft parse_answer.
def response_schema(allowed: list[str]) -> dict[str, Any]:
    candidate = {
        "type": "object",
        "properties": {
            "evidence": {"type": "array", "items": {"type": "string"}},
            "code": {"type": "string", "enum": allowed},
            "confidence": {"type": "integer"},
        },
        "required": ["evidence", "code", "confidence"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "rationale": {"type": "string"},
            "candidates": {"type": "array", "items": candidate},
        },
        "required": ["rationale", "candidates"],
        "additionalProperties": False,
    }


class _Candidate(BaseModel):
    evidence: list[str]
    code: str
    confidence: Annotated[float, Field(allow_inf_nan=False)]  # NaN würde Sortierung kippen


class _Answer(BaseModel):
    rationale: str
    candidates: list[_Candidate]


class AnswerError(ValueError):
    """Antwort unbrauchbar; `kind` wählt die Repair-Nachricht (json | codes | rules)."""

    def __init__(self, kind: Literal["json", "codes", "rules"], detail: str) -> None:
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


@dataclass(frozen=True)
class Candidate:
    code: str
    confidence: float  # 0 bis 100
    evidence: list[str]


@dataclass(frozen=True)
class Parsed:
    rationale: str
    candidates: list[Candidate]
    flags: list[str]


def parse_answer(
    text: str,
    *,
    allowed: list[str],
    abstain_id: str | None,
    mode: Literal["single", "multi"],
    k: int,
    max_signal_words: int,
) -> Parsed:
    try:
        answer = _Answer.model_validate(load_json(text))
    except ValidationError as exc:
        fields = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
        raise AnswerError("json", "fehlerhafte Felder: " + ", ".join(fields)) from exc
    if not answer.candidates:
        # Multi-Label ohne Restkategorie: „kein Code trifft zu“ ist eine leere Liste.
        if mode == "multi" and abstain_id is None:
            return Parsed(answer.rationale.strip(), [], [])
        raise AnswerError("rules", "keine Kandidaten")

    unknown = sorted({c.code for c in answer.candidates if c.code not in allowed})
    if unknown:
        raise AnswerError("codes", ", ".join(unknown))

    flags: list[str] = []
    candidates: list[Candidate] = []
    for raw in answer.candidates:
        if any(c.code == raw.code for c in candidates):
            _add(flags, "doppelter_code")
            continue
        evidence = [e.strip() for e in raw.evidence if e.strip()]
        if raw.code != abstain_id and not evidence:
            raise AnswerError("rules", f"keine Signalwörter für {raw.code}")
        if len(evidence) > max_signal_words:
            evidence = evidence[:max_signal_words]
            _add(flags, "zu_viele_signalwoerter")
        confidence = min(max(raw.confidence, 0.0), 100.0)
        if confidence != raw.confidence:
            _add(flags, "konfidenz_begrenzt")
        candidates.append(Candidate(raw.code, confidence, evidence))

    if len(candidates) > k:
        candidates = candidates[:k]
        _add(flags, "zu_viele_codes")
    if mode == "multi" and abstain_id and len(candidates) > 1:
        if any(c.code == abstain_id for c in candidates):
            raise AnswerError("rules", f"{abstain_id} zusammen mit anderen Codes")
    if mode == "single":
        total = sum(c.confidence for c in candidates)
        if total > 100:
            candidates = [
                Candidate(c.code, c.confidence * 100 / total, c.evidence) for c in candidates
            ]
            _add(flags, "konfidenz_normalisiert")
    return Parsed(answer.rationale.strip(), candidates, flags)


def load_json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fallback für Freitext-Antworten: erstes „{“ bis letztes „}“ (auch in ```json-Blöcken).
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise AnswerError("json", "kein JSON-Objekt gefunden")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise AnswerError("json", f"JSON nicht lesbar: {exc.msg}") from exc


def _add(flags: list[str], flag: str) -> None:
    if flag not in flags:
        flags.append(flag)
