"""Aggregation der Läufe zu einem Ergebnis pro Einheit (docs/PLAN.md, Abschnitt 5).

Reine Funktion über gespeicherte Aufrufe: Ergebnisse lassen sich jederzeit aus den Rohdaten
neu berechnen und hängen nicht von einem Zwischenstand ab.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

from llmanno.core.store import CallRecord

TIE_MARGIN = 0.1  # Spitzenwerte näher als das gelten als unentschieden
MULTI_THRESHOLD = 0.5


@dataclass(frozen=True)
class CodeScore:
    code: str
    p_sc: float  # Self-Consistency: Anteil der Läufe, gemittelt über die Modelle
    p_verbal: float  # mittlere verbalisierte Konfidenz, 0 bis 1


@dataclass(frozen=True)
class EvidenceSpan:
    text: str
    status: str
    start: int | None
    end: int | None
    count: int  # in wie vielen Läufen genannt


@dataclass(frozen=True)
class ItemResult:
    item_id: str
    status: Literal["ok", "invalid", "error", "pending"]
    final: list[str] = field(default_factory=list)
    ranking: list[CodeScore] = field(default_factory=list)
    entropy: float | None = None
    votes: dict[str, list[str]] = field(default_factory=dict)  # Entscheidung je Modell
    rationale: str | None = None
    evidence: dict[str, list[EvidenceSpan]] = field(default_factory=dict)
    n_valid: int = 0
    n_invalid: int = 0
    n_error: int = 0
    flags: list[str] = field(default_factory=list)


def decision(answer: dict, mode: str) -> list[str]:
    """Was ein einzelner Lauf entschieden hat: Top-1 (Single) bzw. alle Codes (Multi)."""
    codes = [c["code"] for c in answer["candidates"]]
    return codes[:1] if mode == "single" else sorted(codes)


# Nicht jedes Panel-Modell hat ein gültiges Ergebnis; entschieden hat nur ein Teil des Panels.
PANEL_INCOMPLETE = "panel_unvollstaendig"


def aggregate(
    item_id: str,
    calls: list[CallRecord],
    *,
    models: list[str],
    mode: Literal["single", "multi"],
    k: int,
    n_codes: int,
    abstain_id: str | None,
) -> ItemResult:
    calls = [c for c in calls if c.model_key in models]  # nur das aktuelle Panel zählt
    valid = [c for c in calls if c.status == "ok" and c.answer]
    n_invalid = sum(c.status == "invalid" for c in calls)
    n_error = sum(c.status == "error" for c in calls)
    if not valid:
        status = "invalid" if n_invalid else "error" if n_error else "pending"
        return ItemResult(item_id, status, n_invalid=n_invalid, n_error=n_error)

    by_model = {m: [c for c in valid if c.model_key == m] for m in models}
    by_model = {m: runs for m, runs in by_model.items() if runs}

    # Self-Consistency je Modell, dann gleich gewichtet über die Modelle mitteln.
    p_sc: Counter[str] = Counter()
    votes: dict[str, list[str]] = {}
    for model, runs in by_model.items():
        counts = Counter(code for run in runs for code in decision(run.answer, mode))
        shares = {code: n / len(runs) for code, n in counts.items()}
        for code, share in shares.items():
            p_sc[code] += share / len(by_model)
        votes[model] = _model_vote(shares, mode)

    # Verbale Konfidenz je Modell gemittelt, dann wie p_sc gleich gewichtet über die Modelle:
    # sonst dominierte bei adaptivem N das Modell, das mehr Läufe brauchte.
    # Nicht genannt zählt als 0; der mittlere Rang (nicht genannt = k+1) bricht nur Gleichstände.
    seen = {cand["code"] for run in valid for cand in run.answer["candidates"]}
    p_verbal = dict.fromkeys(seen, 0.0)
    mean_rank = dict.fromkeys(seen, 0.0)
    for runs in by_model.values():
        for run in runs:
            listed = {
                cand["code"]: (position, cand["confidence"])
                for position, cand in enumerate(run.answer["candidates"], start=1)
            }
            for code in seen:
                position, confidence = listed.get(code, (k + 1, 0.0))
                p_verbal[code] += confidence / 100 / len(runs) / len(by_model)
                mean_rank[code] += position / len(valid)
    full = sorted(
        (CodeScore(code, p_sc[code], p_verbal[code]) for code in seen),
        # Zuletzt das Kürzel: Gleichstände werden reproduzierbar statt zufällig aufgelöst.
        key=lambda s: (-s.p_sc, -s.p_verbal, mean_rank[s.code], s.code),
    )
    # Erst auf der vollen Rangfolge entscheiden, dann auf k kürzen: sonst sieht k = 1 nie einen
    # Gleichstand, und im Multi-Modus könnte die Restkategorie einen Platz belegen.
    ranking = full[:k]

    flags: list[str] = []
    if mode == "single":
        final = [full[0].code]
        entropy = _entropy(list(p_sc.values()), n_codes)
        if len(full) > 1 and full[1].p_sc > 0 and full[0].p_sc - full[1].p_sc < TIE_MARGIN:
            flags.append("unentschieden")
    else:
        final = [s.code for s in full if s.p_sc >= MULTI_THRESHOLD and s.code != abstain_id][:k]
        if not final and abstain_id is not None:
            final = [abstain_id]
        elif not final:
            flags.append("kein_code_ueber_schwelle")
        entropy = _mean_binary_entropy([s.p_sc for s in full])

    if len({tuple(v) for v in votes.values()}) > 1:
        flags.append("dissens")
    if final == [abstain_id]:
        flags.append("abstain")
    if n_invalid:
        flags.append("ungueltige_laeufe")
    if len(by_model) < len(models):
        flags.append(PANEL_INCOMPLETE)

    evidence = {code: _evidence(valid, code) for code in final if code != abstain_id}
    if any(not any(span.status != "missing" for span in spans) for spans in evidence.values()):
        flags.append("nicht_belegt")

    ordered = _panel_order(valid, models)
    target = sorted(final) if mode == "multi" else final
    rationale_run = next((r for r in ordered if decision(r.answer, mode) == target), ordered[0])
    # Bei Multi-Label stehen finale Codes, die außerhalb der Top-k lägen, trotzdem im Ranking.
    ranking += [s for s in full[k:] if s.code in final]
    return ItemResult(
        item_id=item_id,
        status="ok",
        final=final,
        ranking=ranking,
        entropy=entropy,
        votes=votes,
        rationale=rationale_run.answer.get("rationale"),
        evidence=evidence,
        n_valid=len(valid),
        n_invalid=n_invalid,
        n_error=n_error,
        flags=flags,
    )


def _model_vote(shares: dict[str, float], mode: str) -> list[str]:
    if mode == "single":
        return [min(shares, key=lambda c: (-shares[c], c))]
    return sorted(c for c, share in shares.items() if share >= MULTI_THRESHOLD)


def _evidence(valid: list[CallRecord], code: str) -> list[EvidenceSpan]:
    """Signalwörter eines Codes über alle Läufe, die ihn nennen, nach Häufigkeit."""
    counts: Counter[str] = Counter()
    first: dict[str, dict] = {}
    for run in valid:
        for cand in run.answer["candidates"]:
            if cand["code"] != code:
                continue
            for ev in cand["evidence"]:
                key = ev["text"].casefold().strip()
                counts[key] += 1
                # Beste Verortung behalten: ein belegtes Vorkommen schlägt ein unbelegtes.
                if key not in first or (
                    first[key]["status"] == "missing" and ev["status"] != "missing"
                ):
                    first[key] = ev
    return [
        EvidenceSpan(
            first[key]["text"], first[key]["status"], first[key]["start"], first[key]["end"], n
        )
        for key, n in counts.most_common()
    ]


def _panel_order(valid: list[CallRecord], models: list[str]) -> list[CallRecord]:
    return sorted(valid, key=lambda c: (models.index(c.model_key), c.run))


def _entropy(probabilities: list[float], n_codes: int) -> float:
    if n_codes <= 1:
        return 0.0
    h = -sum(p * math.log(p) for p in probabilities if p > 0)
    return h / math.log(n_codes)


def _mean_binary_entropy(probabilities: list[float]) -> float:
    def binary(p: float) -> float:
        return 0.0 if p in (0.0, 1.0) else -(p * math.log2(p) + (1 - p) * math.log2(1 - p))

    return (
        sum(binary(min(max(p, 0.0), 1.0)) for p in probabilities) / len(probabilities)
        if probabilities
        else 0.0
    )
