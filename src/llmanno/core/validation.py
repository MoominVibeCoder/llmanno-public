"""Dev/Test-Split, Freeze und Instrument-Hash (PLAN, Abschnitt 3.8).

Ablauf: Split erzeugen → Codebuch und Prompt nur auf Dev schärfen → einfrieren → Vollauf →
Test einmal auswerten. Jede Test-Auswertung wird mit Hash protokolliert; der Bericht nennt,
wie oft das Test-Set angesehen wurde.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import Counter
from collections.abc import Collection, Hashable
from dataclasses import dataclass
from datetime import UTC, datetime

from llmanno.core.codesystems import main_split
from llmanno.core.config import FreezeState, ProjectConfig
from llmanno.core.engine import Engine, PreparedModel, fingerprint, prepare_prompts
from llmanno.core.gold import GoldTable, Label, build_gold, gold_parts
from llmanno.core.metrics.agreement import majority
from llmanno.core.project import Project

TEST_EVALUATED = "test_evaluated"
TEST_EXPOSED = "test_exposed"


def reference_labels(
    project: Project, gold: GoldTable, item_ids: list[str]
) -> dict[str, Label | None]:
    """Referenz für die Schichtung: gewählte Spalte, sonst Konsens, sonst erste Kodierperson,
    sonst Mehrheit."""
    chosen = project.config.split.reference
    column = (
        chosen
        if chosen in gold.labels
        else gold.consensus or (gold.coders[0] if gold.coders else None)
    )
    if column is not None:
        return {i: gold.labels[column][i] for i in item_ids}
    return {i: majority([gold.labels[c][i] for c in gold.coders]) for i in item_ids}


def grouped_units(cfg: ProjectConfig) -> bool:
    """Gruppenweise ziehen (Split und Bootstrap), sobald eine Gruppen-Spalte gesetzt ist.

    Einheiten eines Transkripts hängen zusammen, auch isoliert kodiert: sonst werden die
    Intervalle zu schmal, Wald-Test und PPI zu liberal. Im Kontextmodus immer (ein Prompt
    sähe sonst Zeilen aus dem anderen Teil), isoliert außer bei independent_units,
    holistisch nie (jede Einheit ist schon eine Gruppe)."""
    if cfg.data is None or cfg.data.mapping.group is None:
        return False
    mode = cfg.coding.units.mode
    if cfg.freeze is not None:  # eingefroren: wie im Analyseplan
        return cfg.freeze.grouped if cfg.freeze.grouped is not None else mode == "context"
    return mode == "context" or (mode == "isolated" and not cfg.split.independent_units)


def cluster_key(cfg: ProjectConfig, item_id: str, group: str | None) -> Hashable:
    """Cluster einer Einheit. Isoliert kodiert bildet eine Einheit ohne Gruppe ihr eigenes
    Cluster; im Kontextmodus teilen Einheiten ohne Gruppe einen Kontext und bleiben eins."""
    if group is None and cfg.coding.units.mode != "context":
        return ("Einheit", item_id)
    return group


def split_gaps(project: Project) -> tuple[list[str], list[str], Counter[str]]:
    """Einheiten mit Gold, die der Split nicht erfasst.

    (Gold nur mit nicht zugeordneten Werten, zugeordnetes Gold außerhalb des Splits,
    nicht zugeordnete Werte). Der Split zieht nur Einheiten mit zugeordnetem Gold; wer
    danach zuordnet, hat Einheiten, die weder in Dev noch in Test stehen."""
    cfg = project.config
    _, items = prepare_prompts(project)
    units = project.store.units()
    gold = build_gold(cfg, units, items)
    raw = {u.unit_id: any(gold_parts(u.gold.get(c), cfg) for c in gold.columns) for u in units}
    parts = project.store.split()
    unmapped, outside = [], []
    for item in items:
        if item.item_id in gold.inconsistent:
            continue
        if any(gold.labels[c][item.item_id] is not None for c in gold.columns):
            if parts and item.item_id not in parts:
                outside.append(item.item_id)
        elif any(raw[line.unit_id] for line in item.lines):
            unmapped.append(item.item_id)
    return unmapped, outside, gold.unmapped


def create_split(project: Project) -> dict[str, str]:
    cfg = project.config
    if cfg.freeze is not None:
        raise PermissionError(
            "Das Instrument ist eingefroren; der Split lässt sich nicht mehr ändern."
        )
    if count_test_evaluations(project):
        raise PermissionError(
            "Das Test-Set wurde schon ausgewertet. Ein neuer Split würde Einheiten, die schon "
            "angesehen wurden, ins Test-Set mischen."
        )
    if cfg.coding.examples:
        raise ValueError(
            "Erst die Beispiele aus Dev entfernen (Tab Prompt & Einheiten): Ein neuer Split "
            "könnte sie ins Test-Set schieben."
        )
    _, items = prepare_prompts(project)
    units = project.store.units()
    gold = build_gold(cfg, units, items)
    if not gold.columns:
        raise ValueError(
            "Ohne Gold-Spalten gibt es nichts aufzuteilen: im Tab Daten die Spalten mit "
            "menschlicher Kodierung als Gold wählen."
        )
    item_ids = [i.item_id for i in items]
    eligible = [i for i in item_ids if any(gold.labels[c][i] is not None for c in gold.columns)]
    if len(eligible) < 2:
        raise ValueError(
            "Zu wenige Einheiten mit Gold für eine Aufteilung: im Tab Daten Gold nachtragen."
        )

    rng = random.Random(cfg.split.seed)
    share = cfg.split.dev_share
    group_of = {u.unit_id: u.group for u in units}
    grouped = grouped_units(cfg) and any(group_of.get(i) for i in eligible)
    dev: set[str] = set()
    if grouped:
        # Gruppen ganz zuteilen: Im Kontextmodus sieht der Prompt Nachbarzeilen; läge eine
        # Gruppe in beiden Teilen, sähe Dev Teile des Test-Sets. Auch isoliert kodiert prüft
        # der Test so auf neuen Transkripten statt auf Geschwistern der Dev-Einheiten.
        groups: dict[Hashable, list[str]] = {}
        for i in eligible:
            groups.setdefault(cluster_key(cfg, i, group_of.get(i)), []).append(i)
        order = list(groups)
        rng.shuffle(order)
        for g in order:
            if len(dev) >= share * len(eligible):
                break
            dev.update(groups[g])
    elif cfg.split.stratify:
        labels = reference_labels(project, gold, eligible)
        strata: dict[str, list[str]] = {}
        for i in eligible:
            strata.setdefault(_stratum(labels[i]), []).append(i)
        for key in sorted(strata):
            members = strata[key]
            rng.shuffle(members)
            dev.update(members[: round(share * len(members))])
    else:
        shuffled = eligible[:]
        rng.shuffle(shuffled)
        dev.update(shuffled[: round(share * len(shuffled))])
    if not dev or len(dev) == len(eligible):
        raise ValueError("Mit diesem Anteil wäre Dev oder Test leer.")

    parts = {i: "dev" if i in dev else "test" for i in eligible}
    # Weiteres Codesystem mit Gold für dieselben Einheiten: dieselbe Aufteilung wie im
    # Hauptprojekt, sonst schärfte man ein Codebuch an Einheiten, die im anderen Test sind.
    shared = (base := main_split(project)) is not None and set(base) == set(eligible)
    if shared and base is not None:
        parts = base
        dev = {i for i, part in parts.items() if part == "dev"}
    project.store.replace_split(parts)
    project.store.log(
        "split_created",
        n_dev=len(dev),
        n_test=len(parts) - len(dev),
        dev_share=share,
        seed=cfg.split.seed,
        grouped=grouped,
        stratified=cfg.split.stratify and not grouped,
        from_main_project=shared,
        split_sha256=split_sha256(parts),
    )
    return parts


def _stratum(label: Label | None) -> str:
    if label is None:
        return ""
    if isinstance(label, frozenset):
        return "|".join(sorted(label))
    return str(label)


def split_by_reference(project: Project) -> dict[str, Counter[str]]:
    """Je Referenzcode, wie viele Einheiten in Dev und Test liegen (Kontrolle der Schichtung)."""
    parts = project.store.split()
    if not parts:
        return {}
    _, items = prepare_prompts(project)
    gold = build_gold(project.config, project.store.units(), items)
    if not gold.columns:
        return {}
    ids = [i.item_id for i in items if i.item_id in parts]
    labels = reference_labels(project, gold, ids)
    table: dict[str, Counter[str]] = {}
    for i in ids:
        table.setdefault(_stratum(labels[i]) or "–", Counter())[parts[i]] += 1
    return dict(sorted(table.items()))


@dataclass(frozen=True)
class GoldBasis:
    """Grundlage des Stichprobenrechners (metrics.sample_size)."""

    counts: list[int]  # Häufigkeit je Referenz-Kategorie; Multi-Label: je Code-Menge
    from_gold: bool  # sonst die Codes als gleich häufig angenommen
    current_test: int  # Test-Einheiten im aktuellen Split


def gold_basis(project: Project) -> GoldBasis | None:
    """Verteilung der Referenz des Splits, auf der Ebene, auf der die Evaluation κ rechnet
    (Multi-Label: ganze Code-Mengen). None: nur eine Kategorie, κ nicht bestimmbar."""
    cfg = project.config
    assert cfg.codebook is not None
    labels: list[Label] = []
    if cfg.data is not None:
        _, items = prepare_prompts(project)
        gold = build_gold(cfg, project.store.units(), items)
        if gold.columns:
            ids = [i.item_id for i in items]
            labels = [v for v in reference_labels(project, gold, ids).values() if v is not None]
    counts = list((Counter(labels) or Counter(cfg.codebook.allowed_ids())).values())
    if len(counts) < 2:
        return None
    current = sum(1 for part in project.store.split().values() if part == "test")
    return GoldBasis(counts, bool(labels), current)


def split_sha256(parts: dict[str, str]) -> str | None:
    if not parts:
        return None
    payload = json.dumps(sorted(parts.items()), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def model_hashes(project: Project) -> dict[str, str]:
    """Lauf-Identität je Panel-Modell; fragt die Anbieter nach Digests, kodiert aber nichts."""
    engine = Engine(project)
    prepared: list[PreparedModel] = []
    try:
        engine.prepare(prepared)
        return {m.config.key: m.config_hash for m in prepared}
    finally:
        for model in prepared:
            model.provider.close()


def instrument_hash(hashes: dict[str, str], project: Project, split: str | None) -> str:
    coding = project.config.coding
    payload = {
        "models": dict(sorted(hashes.items())),
        "n_runs": coding.n_runs,
        "adaptive": coding.adaptive,
        "adaptive_min_agree": coding.adaptive_min_agree,
        "split_sha256": split,
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def freeze(project: Project, hashes: dict[str, str] | None = None) -> FreezeState:
    """`hashes` aus `model_hashes`, wenn die Anbieter schon gefragt wurden (GUI: im
    Hintergrund); sonst fragt `freeze` selbst."""
    if project.config.freeze is not None:
        raise PermissionError("Das Instrument ist schon eingefroren.")
    if project.dirty:
        project.save()
    if hashes is None:
        hashes = model_hashes(project)
    split = split_sha256(project.store.split())
    grouped = grouped_units(project.config)
    state = FreezeState(
        grouped=grouped,
        instrument_hash=instrument_hash(hashes, project, split),
        fingerprint=fingerprint(project),
        model_hashes=hashes,
        split_sha256=split,
        frozen_at=datetime.now(UTC),
    )
    project.update(freeze=state)
    project.save()
    project.store.log("frozen", **state.model_dump(mode="json"))
    return state


def unfreeze(project: Project, reason: str) -> None:
    if project.config.freeze is None:
        return
    if not reason.strip():
        raise ValueError("Bitte begründen, warum der Freeze aufgehoben wird; das steht im Bericht.")
    previous = project.config.freeze.instrument_hash
    project.update(freeze=None)
    project.save()
    project.store.log("unfrozen", reason=reason.strip(), instrument_hash=previous)


def freeze_intact(project: Project) -> bool | None:
    """None ohne Freeze; sonst, ob Konfiguration und Split noch dem Freeze entsprechen.

    Prüft ohne Anbieter. Geänderte Modell-Digests erkennt erst der Laufstart."""
    state = project.config.freeze
    if state is None:
        return None
    return (
        fingerprint(project) == state.fingerprint
        and split_sha256(project.store.split()) == state.split_sha256
    )


def hidden_test_items(project: Project) -> set[str]:
    """Test-Einheiten, die ein Export vor dem Freeze auslassen kann („nur Dev“)."""
    parts = project.store.split()
    if not parts or project.config.freeze is not None:
        return set()
    return {i for i, p in parts.items() if p == "test"}


def exposed_test_items(project: Project) -> set[str]:
    """Kodierte Test-Einheiten, die ein vollständiger Export vor dem Freeze zeigte."""
    test = hidden_test_items(project)
    return test & project.store.called_items() if test else set()


def note_test_exposure(
    project: Project, export: str, exported: Collection[str] | None = None
) -> None:
    """Exporte enthalten LLM-Ergebnisse und Gold auch für Test-Einheiten. Vor dem Freeze ließe
    sich damit außerhalb des Programms das Test-Set auswerten; das wird protokolliert und im
    Bericht genannt, statt die Exporte zu sperren. `exported`: die Einheiten im Export."""
    coded = exposed_test_items(project)
    if exported is not None:
        coded &= set(exported)
    if coded:
        project.store.log(TEST_EXPOSED, export=export, n_test_units=len(coded))


def count_test_exposures(project: Project) -> int:
    return sum(1 for e in project.store.audit() if e.event == TEST_EXPOSED)


def count_test_evaluations(project: Project) -> int:
    return sum(1 for e in project.store.audit() if e.event == TEST_EVALUATED)
