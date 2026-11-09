"""Prompt-Sensitivität: Wie sehr hängen die Codes am Wortlaut der Anweisungen?

Kleine Änderungen am Prompt können Ergebnisse und Schlüsse verschieben („LLM hacking“, Baumann
et al. 2025). Gemessen wird wie beim Prompt Stability Score (Barrie, Palaiologou & Törnberg
2024): dieselben Einheiten mit umformuliertem Prompt kodieren und Krippendorffs α über die
Varianten berechnen (inter-prompt). Dazu je Variante Übereinstimmung mit dem Original, α gegen
die Referenz und die Code-Anteile: Kippt der inhaltliche Schluss je nach Formulierung?

Jede Variante läuft einmal je Einheit (kein Self-Consistency), auf den Dev-Einheiten oder,
ohne Split, auf allen; bei vielen Einheiten auf einer Zufallsstichprobe.

Dazu kommt, für Modelle, die vor der Antwort denken, das Original mit niedrigem Reasoning-
Aufwand: Liefert es dieselben Codes, spart „low“ Ausgabe-Token und Zeit (Kontingent).
"""

from __future__ import annotations

import math
import random
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass, replace

from llmanno.core.aggregate import decision
from llmanno.core.config import LOW_EFFORTS, ModelConfig, ProjectConfig
from llmanno.core.engine import (
    ISOLATED,
    REASONING_LOW,
    SENSITIVITY_STARTED,
    Engine,
    Progress,
    ProviderFactory,
    RunAborted,
    RunSummary,
    fingerprint,
    prepare_prompts,
)
from llmanno.core.examples import seen_example_ids
from llmanno.core.gold import Label
from llmanno.core.metrics import agreement as ag
from llmanno.core.project import Project
from llmanno.core.prompt import VARIANT_TEXTS

ORIGINAL = ""
DEFAULT_LIMIT = 100
LABELS = {
    ORIGINAL: "Original",
    **{key: f"Umformulierung {i}" for i, key in enumerate(VARIANT_TEXTS, 1)},
    ISOLATED: "ohne Kontext",
    REASONING_LOW: "Reasoning niedrig",
}


def variants_for(cfg: ProjectConfig) -> list[str]:
    """Original, die Umformulierungen, im Kontextmodus eine Variante ohne Kontext und, wo ein
    Modell den Aufwand senken kann, Reasoning niedrig (läuft nur bei denkenden Modellen)."""
    extra = [ISOLATED] if cfg.coding.units.mode == "context" else []
    if any(can_lower_effort(m, cfg) for m in cfg.panel):
        extra.append(REASONING_LOW)
    return [ORIGINAL, *VARIANT_TEXTS, *extra]


def can_lower_effort(model: ModelConfig, cfg: ProjectConfig) -> bool:
    """Nur OpenAI-kompatible Anbieter kennen reasoning_effort."""
    effort = (model.reasoning_effort or "").lower()
    return cfg.provider(model.provider).kind == "openai" and effort not in LOW_EFFORTS


def reasoning_models(project: Project) -> list[str]:
    """Modelle für „Reasoning niedrig“: Aufwand senkbar, und das Modell denkt nachweislich
    (Aufwand gesetzt, oder die bisherigen Antworten verbrauchten verborgene Token). Ohne
    diesen Nachweis wäre „low“ bei einem Modell ohne Reasoning ein Fehler oder wirkungslos."""
    cfg = project.config
    current = fingerprint(project)
    started = {  # schon begonnen: beim Fortsetzen nicht an der Heuristik scheitern
        e.details.get("model")
        for e in project.store.audit()
        if e.event == SENSITIVITY_STARTED
        and e.details.get("variant") == REASONING_LOW
        and e.details.get("fingerprint") == current
    }
    return [
        m.key
        for m in cfg.panel
        if can_lower_effort(m, cfg)
        and (
            m.reasoning_effort is not None
            or m.key in started
            or project.store.hidden_reasoning(m.key)
        )
    ]


def sensitivity_items(project: Project, limit: int = DEFAULT_LIMIT, seed: int = 1) -> list[str]:
    """Dev-Einheiten, ohne Split alle; bei mehr als `limit` eine feste Zufallsstichprobe.
    Ohne Beispiel-Einheiten: Ihr Gold steht im Prompt."""
    _, items = prepare_prompts(project)
    parts = project.store.split()
    shown = seen_example_ids(project)
    ids = [
        i.item_id
        for i in items
        if (not parts or parts.get(i.item_id) == "dev") and i.item_id not in shown
    ]
    if len(ids) > limit:
        chosen = set(random.Random(seed).sample(ids, limit))
        ids = [i for i in ids if i in chosen]
    return ids


def run_sensitivity(
    project: Project,
    item_ids: Collection[str],
    *,
    remote_consent: bool = False,
    provider_factory: ProviderFactory | None = None,
    should_stop: Callable[[], bool] = lambda: False,
    on_progress: Callable[[Progress], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[tuple[str, RunSummary]]:
    """Alle Varianten nacheinander; schon kodierte Aufrufe werden übersprungen.

    Der Fortschritt läuft über alle Varianten durch, statt je Variante bei 0 zu beginnen."""
    done = []
    variants = variants_for(project.config)
    per_variant = len(item_ids) * len(project.config.panel)
    offset = 0
    for index, variant in enumerate(variants):
        later = (len(variants) - index - 1) * per_variant  # obere Schranke
        models = reasoning_models(project) if variant == REASONING_LOW else None
        if models == []:  # erst nach dem Original prüfbar: Es liefert die Token
            continue

        def report(p: Progress, offset: int = offset, later: int = later) -> None:
            assert on_progress is not None
            on_progress(replace(p, done=offset + p.done, planned=offset + p.planned + later))

        engine = Engine(
            project, provider_factory=provider_factory, sleep=sleep, variant=variant,
            models=models,
        )  # fmt: skip
        try:
            summary = engine.run(
                item_ids=item_ids,
                remote_consent=remote_consent,
                should_stop=should_stop,
                on_progress=report if on_progress else None,
            )
        except RunAborted as exc:
            # Lehnt ein Anbieter den Aufwand ab (HTTP 400), bleibt nur diese letzte Zeile
            # unvollständig; die übrigen Varianten sind fertig und gelten.
            if variant != REASONING_LOW or exc.summary is None:
                raise
            summary = exc.summary
            summary.notes.append(f"{LABELS[REASONING_LOW]} abgebrochen: {exc}")
        offset += summary.calls
        done.append((variant, summary))
        if summary.stopped:
            break
    return done


@dataclass(frozen=True)
class VariantRow:
    variant: str  # Anzeigename
    n: int  # gemeinsame Einheiten mit gültiger Antwort
    agreement: float  # Anteil gleicher Entscheidungen wie das Original
    alpha_reference: float | None  # Krippendorffs α gegen die Referenz
    shares: dict[str, float]  # Code → Anteil
    # False: ohne Kontext oder Reasoning niedrig; zählt nicht zum α über den Wortlaut
    wording: bool = True
    key: str = ORIGINAL  # Variante (Schlüssel)
    tokens_out: float | None = None  # mittlere Ausgabe-Token je Aufruf (mit Reasoning)
    seconds: float | None = None  # mittlere Antwortzeit je Aufruf (ohne Wartezeiten)


@dataclass(frozen=True)
class ModelSensitivity:
    model: str
    n_items: int  # Einheiten, die in allen Formulierungen gültig kodiert sind
    alpha: float  # Krippendorffs α über Original und Umformulierungen (Prompt Stability Score)
    rows: list[VariantRow]
    spread: dict[str, float]  # Code → größter minus kleinster Anteil über die Formulierungen
    greedy: bool  # alle Formulierungen mit Temperatur 0; sonst enthält α auch Zufallsstreuung

    @property
    def wording_rows(self) -> list[VariantRow]:
        return [r for r in self.rows if r.wording]

    @property
    def isolated(self) -> VariantRow | None:
        return next((r for r in self.rows if r.key == ISOLATED), None)

    @property
    def reasoning(self) -> VariantRow | None:
        return next((r for r in self.rows if r.key == REASONING_LOW), None)

    @property
    def max_spread(self) -> tuple[str, float]:
        return max(self.spread.items(), key=lambda kv: kv[1], default=("", 0.0))


def sensitivity_report(
    project: Project, reference: dict[str, Label | None] | None = None
) -> list[ModelSensitivity]:
    """Je Panel-Modell mit mindestens zwei kodierten Formulierungen des aktuellen Instruments.

    Läufe zu einem früheren Instrument (anderer Fingerabdruck) zählen nicht. Alle Kennwerte
    beziehen sich auf die Einheiten, die in allen Formulierungen gültig kodiert sind, damit ein
    abgebrochener oder kleinerer Lauf keine Unterschiede vortäuscht."""
    cfg = project.config
    assert cfg.codebook is not None
    multi = cfg.mode == "multi"
    labels = cfg.codebook.allowed_ids()
    current = fingerprint(project)
    latest: dict[tuple[str, str], dict] = {}
    for entry in project.store.audit():
        if entry.event == SENSITIVITY_STARTED and entry.details.get("fingerprint") == current:
            latest[(entry.details["model"], entry.details["variant"])] = entry.details

    def label(answer: dict) -> Label:
        codes = decision(answer, cfg.mode)
        return frozenset(codes) if multi else codes[0]

    def has(value: Label, code: str) -> bool:
        return code in value if multi else value == code  # type: ignore[operator]

    out = []
    level = "masi" if multi else "nominal"
    wording = [ORIGINAL, *VARIANT_TEXTS]
    for model in cfg.panel:
        runs = {v: latest[(model.key, v)] for v in variants_for(cfg) if (model.key, v) in latest}
        calls = {
            v: [
                c
                for c in project.store.calls(
                    model_key=model.key, config_hash=run["config_hash"], with_raw=False
                )
                # Multi-Label darf leer sein (kein Code trifft zu), Single-Label nicht.
                if c.run == 0
                and c.status == "ok"
                and c.answer
                and (multi or c.answer["candidates"])
            ]
            for v, run in runs.items()
        }
        decisions = {v: {c.item_id: label(c.answer) for c in cs} for v, cs in calls.items()}
        meta = {v: {c.item_id: c.meta for c in cs} for v, cs in calls.items()}
        texts = [v for v in wording if v in decisions]
        if len(texts) < 2 or ORIGINAL not in decisions:
            continue
        common = sorted(set.intersection(*(set(decisions[v]) for v in texts)))
        if not common:
            continue
        alpha = ag.krippendorff_alpha([[decisions[v][i] for v in texts] for i in common], level)
        original = decisions[ORIGINAL]
        rows = []
        for v in [*texts, *(x for x in decisions if x not in wording)]:
            ids = [i for i in common if i in decisions[v]]
            d = {i: decisions[v][i] for i in ids}
            agreement = sum(d[i] == original[i] for i in ids) / len(ids) if ids else math.nan
            alpha_ref = None
            if reference is not None:
                paired = [[reference[i], d[i]] for i in ids if reference.get(i) is not None]
                if len(paired) >= 2:
                    alpha_ref = ag.krippendorff_alpha(paired, level)
            shares = (
                {code: sum(has(x, code) for x in d.values()) / len(d) for code in labels}
                if d
                else {}
            )
            rows.append(
                VariantRow(
                    LABELS.get(v, v), len(ids), agreement, alpha_ref, shares, v in wording, v,
                    _mean(_with_repair(meta[v][i], "tokens_out") for i in ids),
                    _mean(_with_repair(meta[v][i], "latency_s") for i in ids),
                )
            )  # fmt: skip
        spread = {
            code: max(r.shares[code] for r in rows if r.wording)
            - min(r.shares[code] for r in rows if r.wording)
            for code in labels
        }
        greedy = all(runs[v].get("temperature") == 0 for v in texts)
        out.append(ModelSensitivity(model.key, len(common), alpha, rows, spread, greedy))
    return out


def _with_repair(meta: dict, key: str) -> float | None:
    """Wert des Aufrufs samt Repair; ohne Wartezeiten (Rate-Limit, Backoff), anders als
    duration_s. None, wenn der Anbieter ihn nicht meldet."""
    value = meta.get(key)
    if not isinstance(value, int | float):
        return None
    repair = (meta.get("repair") or {}).get(key)
    return value + (repair if isinstance(repair, int | float) else 0)


def _mean(values) -> float | None:
    known = [v for v in values if isinstance(v, int | float)]
    return sum(known) / len(known) if known else None
