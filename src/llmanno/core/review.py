"""Review-Queue (PLAN 3.9): Gate unsicherer Fälle, Zufallsstichprobe, Reviews als Gold-Schicht.

Nur blinde Reviews zählen als Gold; assistierte („LLM-gestützt korrigiert“) nie. Unverzerrt ist
nur der Vergleich mit der Zufallsstichprobe: Das Gate wählt gezielt die schwierigen Fälle.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import numpy as np

from llmanno.core.aggregate import ItemResult
from llmanno.core.config import GateCalibration, ReviewConfig
from llmanno.core.engine import prepare_prompts, results
from llmanno.core.examples import seen_example_ids
from llmanno.core.gold import Label, build_gold
from llmanno.core.metrics import agreement as ag
from llmanno.core.metrics.calibration import isotonic_fit
from llmanno.core.project import Project
from llmanno.core.store import QueueEntry, Review
from llmanno.core.validation import reference_labels

RANDOM = "random"
REASONS = {
    RANDOM: "Zufallsstichprobe",
    "low_confidence": "geringe Konfidenz",
    "dissent": "Dissens im Panel",
    "abstain": "Restkategorie",
    "unsupported": "nicht belegt",
    "invalid": "ungültig / Fehler",
}
SUGGESTION_SHOWN = "review_suggestion_shown"
REVIEW_RANDOM = "Review blind (Zufallsstichprobe)"
REVIEW_ALL = "Review blind (alle)"


MIN_CALIBRATION = 20  # Einheiten mit Gold, ab denen die rekalibrierte Schwelle geht
UNDECIDED = {"unentschieden", "kein_code_ueber_schwelle"}


def weakest_score(result: ItemResult) -> float:
    """Self-Consistency des unsichersten finalen Codes; daran misst das Gate."""
    scores = {s.code: s.p_sc for s in result.ranking}
    return min((scores.get(c, 0.0) for c in result.final), default=0.0)


def gate_score(result: ItemResult, cfg: ReviewConfig) -> float:
    """Was das Gate mit τ vergleicht: die Self-Consistency, mit Abbildung die geschätzte
    Trefferquote."""
    score = weakest_score(result)
    if cfg.calibration is None:
        return score
    return float(np.interp(score, cfg.calibration.xs, cfg.calibration.fitted))


def unsure(result: ItemResult, cfg: ReviewConfig) -> bool:
    """Gültiges Ergebnis unter τ oder unentschieden (Gate „geringe Konfidenz“)."""
    return result.status == "ok" and (
        gate_score(result, cfg) < cfg.tau or bool(set(result.flags) & UNDECIDED)
    )


def gate_calibration(project: Project) -> GateCalibration:
    """Lernt die Abbildung auf den Dev-Einheiten (ohne Split: allen) mit Referenz des Splits
    und gültigem Ergebnis; Treffer heißt: finale Codes gleich der Referenz. Test-Einheiten nie,
    sonst richtete sich das Gate am Test-Set aus. Gespeichert in ReviewConfig.calibration, damit
    sie zum Instrument gehört (Freeze, neue Erhebungswelle)."""
    cfg = project.config
    _, items = prepare_prompts(project)
    parts = project.store.split()
    shown = seen_example_ids(project)  # Beispiel-Einheiten: Das Modell kannte ihr Gold.
    items = [
        i for i in items if (not parts or parts.get(i.item_id) == "dev") and i.item_id not in shown
    ]
    by_item = results(project, [i.item_id for i in items])
    gold = build_gold(cfg, project.store.units(), items)
    pairs = []
    if gold.columns:
        reference = reference_labels(project, gold, [i.item_id for i in items])
        for item_id, label in reference.items():
            result = by_item[item_id]
            if label is None or result.status != "ok" or not result.final:
                continue
            llm = frozenset(result.final) if cfg.mode == "multi" else result.final[0]
            pairs.append((weakest_score(result), float(llm == label)))
    if len(pairs) < MIN_CALIBRATION:
        raise ValueError(
            f"Für τ als geschätzte Trefferquote braucht es mindestens {MIN_CALIBRATION} "
            f"Einheiten {'des Übungsteils (Dev) ' if parts else ''}mit Gold und gültigem "
            f"LLM-Ergebnis (jetzt {len(pairs)}). Abhilfe: Gold nachtragen (Tab Daten) oder mehr "
            f"Einheiten {'des Übungsteils ' if parts else ''}kodieren (Tab Lauf). Bis dahin gilt "
            "τ für die Konsistenz."
        )
    xs, fitted = isotonic_fit(*map(np.asarray, zip(*pairs, strict=True)))
    return GateCalibration(
        xs=[float(x) for x in xs], fitted=[float(y) for y in fitted], n=len(pairs)
    )


def gate_reasons(result: ItemResult, cfg: ReviewConfig) -> list[str]:
    if result.status in ("invalid", "error"):
        return ["invalid"] if cfg.gate_invalid else []
    if result.status != "ok":
        return []  # noch nicht kodiert
    flags = set(result.flags)
    checks = [
        ("low_confidence", cfg.gate_low_confidence and unsure(result, cfg)),
        ("dissent", cfg.gate_dissent and "dissens" in flags),
        ("abstain", cfg.gate_abstain and "abstain" in flags),
        ("unsupported", cfg.gate_unsupported and "nicht_belegt" in flags),
    ]
    return [reason for reason, hit in checks if hit]


def build_queue(project: Project) -> list[QueueEntry]:
    """Zufallsstichprobe aus allen Einheiten (ohne Zurücklegen, Wahrscheinlichkeit n/N) plus
    alle Einheiten, die das Gate auswählt (Wahrscheinlichkeit 1). Die Reihenfolge ist gemischt,
    damit die Position nicht verrät, warum eine Einheit in der Queue steht."""
    cfg = project.config.review
    _, items = prepare_prompts(project)
    shown = seen_example_ids(project)  # ihr Gold stand im Prompt: nicht reviewen
    ids = [i.item_id for i in items if i.item_id not in shown]
    by_item = results(project, ids)
    rng = random.Random(cfg.seed)
    n_random = round(cfg.random_share * len(ids))
    drawn = set(rng.sample(ids, n_random))
    p_random = n_random / len(ids)
    picked = []
    for item_id in ids:
        gate = gate_reasons(by_item[item_id], cfg)
        reasons = ([RANDOM] if item_id in drawn else []) + gate
        if reasons:
            picked.append((item_id, reasons, 1.0 if gate else p_random))
    rng.shuffle(picked)
    queue = [QueueEntry(i, pos, reasons, p) for pos, (i, reasons, p) in enumerate(picked)]
    project.store.replace_queue(queue)
    project.store.log(
        "review_queue_created",
        n_queue=len(queue),
        n_items=len(ids),
        n_random=n_random,
        p_random=p_random,
        n_gated=sum(1 for e in queue if set(e.reasons) - {RANDOM}),
        settings=cfg.model_dump(mode="json"),  # samt Gate-Abbildung
    )
    return queue


def person(name: str) -> str:
    """Wie Personen verglichen werden: Groß- und Kleinschreibung und Leerraum zählen nicht.
    Sonst könnte „anna“ blind reviewen, was „Anna“ assistiert schon gesehen hat."""
    return " ".join(name.split()).casefold()


def _spelling(project: Project, name: str) -> str:
    """Die Schreibweise, unter der die Person im Projekt zuerst auftrat."""
    name = " ".join(name.split())
    known = [r.reviewer for r in project.store.reviews()] + [
        e.details.get("reviewer") or ""
        for e in project.store.audit()
        if e.event == SUGGESTION_SHOWN
    ]
    return next((k for k in known if k and person(k) == person(name)), name)


def add_review(
    project: Project, item_id: str, reviewer: str, mode: str, codes: list[str], note: str = ""
) -> Review:
    cfg = project.config
    assert cfg.codebook is not None
    if not reviewer.strip():
        raise ValueError(
            "Bitte links unter „Person“ den eigenen Namen oder ein Kürzel eintragen; er steht bei "
            "jeder Review."
        )
    reviewer = _spelling(project, reviewer)
    if mode not in ("blind", "assisted"):
        raise ValueError(f"Unbekannter Review-Modus: {mode}")
    if mode == "blind" and saw_suggestion(project, item_id, reviewer):
        raise ValueError(
            f"{reviewer} hat den LLM-Vorschlag für diese Einheit schon gesehen; eine blinde "
            "Review ist nicht mehr möglich. Die Einheit überspringen oder im Modus „assistiert“ "
            "speichern."
        )
    allowed = cfg.codebook.allowed_ids()
    if unknown := [c for c in codes if c not in allowed]:
        raise ValueError(f"Unbekannte Codes: {', '.join(unknown)}")
    if not codes:
        raise ValueError("Bitte mindestens einen Code wählen (anklicken oder Taste drücken).")
    if cfg.mode == "single" and len(codes) != 1:
        raise ValueError("Im Single-Label-Modus genau einen Code wählen.")
    abstain = cfg.codebook.abstain_id if cfg.codebook.abstain else None
    if abstain in codes and len(codes) > 1:
        raise ValueError("Die Restkategorie schließt andere Codes aus.")
    entry = next((e for e in project.store.queue() if e.item_id == item_id), None)
    review = Review(
        item_id=item_id,
        reviewer=reviewer,
        mode=mode,
        codes=sorted(codes) if cfg.mode == "multi" else list(codes),
        note=note.strip(),
        reasons=entry.reasons if entry else [],
        draw_probability=entry.draw_probability if entry else None,
    )
    project.store.add_review(review)
    return review


def mark_suggestion_shown(project: Project, item_id: str, reviewer: str) -> None:
    """Assistierter Modus: Wer den LLM-Vorschlag gesehen hat, kann nicht mehr blind urteilen,
    auch wenn er nichts speichert."""
    if not saw_suggestion(project, item_id, reviewer):
        project.store.log(SUGGESTION_SHOWN, item_id=item_id, reviewer=_spelling(project, reviewer))


def saw_suggestion(project: Project, item_id: str, reviewer: str) -> bool:
    who = person(reviewer)
    if any(
        r.item_id == item_id and person(r.reviewer) == who and r.mode == "assisted"
        for r in project.store.reviews()
    ):
        return True
    return any(
        e.event == SUGGESTION_SHOWN
        and e.details.get("item_id") == item_id
        and person(e.details.get("reviewer") or "") == who
        for e in project.store.audit()
    )


def random_sample_gaps(project: Project, item_ids: list[str]) -> tuple[int, int]:
    """(ohne blinde Review, gezogen): Einheiten der Zufallsstichprobe in der aktuellen Queue."""
    drawn = {e.item_id for e in project.store.queue() if RANDOM in e.reasons} & set(item_ids)
    reviewed = {r.item_id for r in project.store.reviews() if r.mode == "blind"}
    return len(drawn - reviewed), len(drawn)


def review_status(project: Project) -> dict[str, str]:
    """Je Einheit: „blind“ (mindestens eine blinde Review), „assistiert“ (nur assistierte)
    oder „offen“ (in der Queue, noch ohne Review). Einheiten ohne beides fehlen."""
    status = {e.item_id: "offen" for e in project.store.queue()}
    for review in project.store.reviews():
        if review.mode == "blind":
            status[review.item_id] = "blind"
        elif status.get(review.item_id) != "blind":
            status[review.item_id] = "assistiert"
    return status


def latest_reviews(project: Project) -> dict[tuple[str, str, str], Review]:
    """Gültige Review je (Einheit, Person, Modus): die jüngste; frühere bleiben im Store.
    Die Person steht im Schlüssel als person(Name)."""
    return {(r.item_id, person(r.reviewer), r.mode): r for r in project.store.reviews()}


def review_gold(project: Project, item_ids: list[str]) -> dict[str, dict[str, Label | None]]:
    """Blinde Reviews als Referenzen. Je Einheit zählt die Person, die sie zuerst blind
    reviewt hat (mit ihrer jüngsten Review); weitere Personen überschreiben sie nicht, sie
    gehen in review_agreement ein.

    „Zufallsstichprobe“ enthält nur zufällig gezogene Einheiten und ist unverzerrt; „alle“
    erscheint nur, wenn es zusätzlich per Gate gewählte Einheiten gibt."""
    cfg = project.config
    assert cfg.codebook is not None
    allowed = set(cfg.codebook.allowed_ids())
    latest = _first_blind(project)

    def label(review: Review) -> Label | None:
        return _label(review, cfg.mode, allowed)

    every = {i: label(latest[i]) if i in latest else None for i in item_ids}
    drawn = {i: every[i] if i in latest and RANDOM in latest[i].reasons else None for i in item_ids}
    gold: dict[str, dict[str, Label | None]] = {}
    if any(v is not None for v in drawn.values()):
        gold[REVIEW_RANDOM] = drawn
    if any(every[i] is not None and drawn[i] is None for i in item_ids):
        gold[REVIEW_ALL] = every
    return gold


@dataclass(frozen=True)
class WeightedReviews:
    """Blinde Reviews der aktuellen Queue mit ihrer Ziehwahrscheinlichkeit, für die gewichtete
    Prävalenz (confidence-driven inference): Gate π = 1, Zufallsstichprobe π = n/N."""

    labels: dict[str, Label | None]  # Einheit → Label; None außerhalb der Queue oder offen
    probability: dict[str, float]  # Einheit der Queue → Wahrscheinlichkeit, gezogen zu werden


def weighted_reviews(project: Project, item_ids: list[str]) -> WeightedReviews | None:
    """None ohne Queue oder ohne Zufallsanteil: Einheiten außerhalb des Gates hätten dann
    Wahrscheinlichkeit 0, und ihr LLM-Fehler bliebe unkorrigiert.

    Es zählen nur Einheiten der aktuellen Queue, mit deren Wahrscheinlichkeit: Nach einem
    Neubau der Queue gehören Reviews früherer Ziehungen nicht mehr zum Design (sie verzerrten
    die Schätzung). Ob eine Einheit der Queue früher oder später reviewt wurde, ist gleich."""
    created = [e for e in project.store.audit() if e.event == "review_queue_created"]
    if not created or not created[-1].details.get("p_random"):
        return None
    queue = {e.item_id: e.draw_probability for e in project.store.queue()}
    reviews = _first_blind(project)
    cfg = project.config
    assert cfg.codebook is not None
    allowed = set(cfg.codebook.allowed_ids())
    labels = {
        i: _label(reviews[i], cfg.mode, allowed) if i in queue and i in reviews else None
        for i in item_ids
    }
    return WeightedReviews(labels, {i: p for i, p in queue.items() if i in labels})


def _first_blind(project: Project) -> dict[str, Review]:
    """Je Einheit die jüngste blinde Review der Person, die sie zuerst blind reviewt hat."""
    by_person = _blind_by_person(project)
    first: dict[str, str] = {}
    for review in project.store.reviews():
        if review.mode == "blind":
            first.setdefault(review.item_id, person(review.reviewer))
    return {i: by_person[p][i] for i, p in first.items()}


def _label(review: Review, mode: str, allowed: set[str]) -> Label | None:
    if not set(review.codes) <= allowed:
        return None  # Code inzwischen nicht mehr im Codebuch
    return frozenset(review.codes) if mode == "multi" else review.codes[0]


def _blind_by_person(project: Project) -> dict[str, dict[str, Review]]:
    """Person → Einheit → jüngste blinde Review."""
    out: dict[str, dict[str, Review]] = {}
    for review in project.store.reviews():
        if review.mode == "blind":
            out.setdefault(person(review.reviewer), {})[review.item_id] = review
    return out


def review_agreement(project: Project, item_ids: list[str]) -> tuple[float, int] | None:
    """Krippendorffs α zwischen blinden Review-Personen, auf den Einheiten mit mindestens zwei;
    (α, Zahl der Einheiten) oder None."""
    cfg = project.config
    assert cfg.codebook is not None
    allowed = set(cfg.codebook.allowed_ids())
    by_person = _blind_by_person(project)
    names = sorted(by_person)
    shared = [i for i in item_ids if sum(i in by_person[p] for p in names) >= 2]
    if len(names) < 2 or not shared:
        return None
    matrix = [
        [_label(by_person[p][i], cfg.mode, allowed) if i in by_person[p] else None for p in names]
        for i in shared
    ]
    level = "masi" if cfg.mode == "multi" else "nominal"
    return ag.krippendorff_alpha(matrix, level), len(shared)
