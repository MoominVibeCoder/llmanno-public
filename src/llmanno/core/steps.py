"""Der Arbeitsablauf als Schritte: was erledigt ist und was als Nächstes kommt (Leiste im
Hauptfenster). Nur günstige Prüfungen, denn die Leiste folgt jeder Änderung."""

from __future__ import annotations

from dataclasses import dataclass

from llmanno.core.engine import current_runs
from llmanno.core.project import Project
from llmanno.core.units import build_items
from llmanno.core.validation import count_test_evaluations, freeze_intact

# Exporte, die den Abschluss markieren (nicht Präregistrierung oder Codesystem allein)
RESULT_EXPORTS = {"replikationspaket", "xlsx", "qdpx"}


@dataclass(frozen=True)
class Step:
    key: str  # data, codebook, models, split, pilot, dev, freeze, full, test, review, export
    done: bool
    broken: bool = False  # nur freeze: eingefroren, aber seither geändert


def workflow(project: Project) -> list[Step]:
    """Mit Gold: Split, Übungsteil, Freeze und Test-Auswertung. Ohne Gold misst eine blinde
    Review der Zufallsstichprobe die Qualität, also steht dort die Prüfliste."""
    cfg, store = project.config, project.store
    gold = cfg.data is not None and bool(cfg.data.mapping.gold or cfg.data.mapping.consensus)
    items = {i.item_id for i in build_items(store.units(), cfg.coding.units)} if cfg.data else set()
    coded, touched = coded_items(project)
    parts = store.split()
    dev = {i for i, part in parts.items() if part == "dev"} & items
    steps = [
        Step("data", cfg.data is not None),
        Step("codebook", cfg.codebook is not None and bool(cfg.codebook.active_codes())),
        Step("models", bool(cfg.panel)),
    ]
    if gold:
        steps.append(Step("split", bool(parts)))
    steps.append(Step("pilot", bool(touched)))
    if gold:
        steps.append(Step("dev", bool(dev) and dev <= coded))
        intact = freeze_intact(project)
        steps.append(Step("freeze", intact is True, broken=intact is False))
    steps.append(Step("full", bool(items) and items <= coded))
    if gold:
        steps.append(Step("test", count_test_evaluations(project) > 0))
    else:
        # Die Qualität misst die blind reviewte Zufallsstichprobe (ohne Zufall: die Prüfliste).
        queue = store.queue()
        sample = {e.item_id for e in queue if "random" in e.reasons} or {e.item_id for e in queue}
        blind = {r.item_id for r in store.reviews() if r.mode == "blind"}
        steps.append(Step("review", bool(sample) and sample <= blind))
    steps.append(
        Step(
            "export",
            any(
                e.event == "exported" and e.details.get("format") in RESULT_EXPORTS
                for e in store.audit()
            ),
        )
    )
    return steps


def coded_items(project: Project) -> tuple[set[str], set[str]]:
    """Mit dem jetzigen Instrument beantwortete Einheiten: (von allen Panel-Modellen, von
    mindestens einem). Sensitivitätsvarianten und frühere Instrumente zählen nicht."""
    cfg = project.config
    if cfg.data is None or cfg.codebook is None or not cfg.panel:
        return set(), set()
    runs = current_runs(project)
    answered = [
        project.store.answered_items(m.key, runs[m.key]) if m.key in runs else set()
        for m in cfg.panel
    ]
    return set.intersection(*answered), set.union(*answered)


def next_step(steps: list[Step]) -> Step | None:
    return next((s for s in steps if not s.done), None)
