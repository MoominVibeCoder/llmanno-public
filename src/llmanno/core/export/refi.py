"""REFI-QDA-Export (PLAN 3.12): Projekt (.qdpx) für MAXQDA, ATLAS.ti, QualCoder, NVivo und
Codesystem (.qdc).

Abbildung:
- eine TextSource je Gruppe, ohne Gruppe je Einheit; jede Zeile als „[ID] Sprecher: Text“
- Users: „LLM-Panel“, jede Gold-Spalte und jede Review-Person; so rechnen die QDA-Programme
  die Übereinstimmung LLM gegen Mensch mit ihren eigenen Funktionen nach
- Codesystem: die Codes und ein eigener Zweig „LLM-Evidenz“ für die Signalwörter, damit die
  Spans den segmentbasierten Vergleich der Hauptcodes nicht verfälschen
- je Einheit eine Notiz mit Top-k, beiden Konfidenzen, Begründung und Flags

Positionen zählen Unicode-Zeichen (Python-Indizes); Zeilenenden sind nur "\\n", weil manche
Programme bei "\\r\\n" die Positionen verschieben.
"""

from __future__ import annotations

import re
import uuid
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from xml.etree import ElementTree as ET

from llmanno.core.aggregate import ItemResult
from llmanno.core.config import ProjectConfig
from llmanno.core.engine import prepare_prompts, results
from llmanno.core.gold import GoldTable, build_gold
from llmanno.core.project import Project
from llmanno.core.review import latest_reviews
from llmanno.core.store import Review, Unit
from llmanno.core.units import Item, group_sequences
from llmanno.core.validation import hidden_test_items, note_test_exposure

NS = "urn:QDA-XML:project:1.0"
CODEBOOK_NS = "urn:QDA-XML:codebook:1.0"
LLM_USER = "LLM-Panel"
EVIDENCE_BRANCH = "LLM-Evidenz"
REVIEW_MODES = {"blind": "Review blind", "assisted": "Review assistiert"}
# Gut unterscheidbare Farben (Okabe-Ito), danach von vorn.
# In XML 1.0 verboten; kommt etwa aus pdftotext (Seitenumbruch \x0c) in Codebüchern vor.
_XML_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")
COLORS = ("#E69F00", "#56B4E9", "#009E73", "#F0E442", "#0072B2", "#D55E00", "#CC79A7", "#999999")


@dataclass(frozen=True)
class RefiSummary:
    sources: int
    codings: int
    users: list[str]


class _Ids:
    """Stabile GUIDs: Ein erneuter Export desselben Projekts erzeugt dieselben Kennungen."""

    def __init__(self, cfg: ProjectConfig) -> None:
        self._seed = f"llmanno:{cfg.name}:{cfg.created.isoformat()}"

    def __call__(self, *parts: object) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, ":".join([self._seed, *map(str, parts)])))


def _tag(name: str, ns: str = NS) -> str:
    return f"{{{ns}}}{name}"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _codebook(
    container: ET.Element, cfg: ProjectConfig, ids: Callable[..., str], ns: str
) -> dict[str, str]:
    """Schreibt die Codes in `container` (<Codes> oder den Zweig eines Codesystems) und liefert
    Code-Kürzel → GUID (Evidenz-Codes als „evidence:K1“)."""
    assert cfg.codebook is not None
    cb = cfg.codebook
    codes = [(c.id, c.label) for c in cb.active_codes()]
    if cb.abstain:
        codes.append((cb.abstain_id, cb.abstain_label))
    guids: dict[str, str] = {}
    parent_of = {c.id: c.parent for c in cb.active_codes() if c.parent}
    groups: dict[str, ET.Element] = {}
    for index, (code, label) in enumerate(codes):
        guids[code] = ids("code", code)
        holder = container
        if group := parent_of.get(code):  # Code-Gruppe als nicht kodierbarer Obercode
            if group not in groups:
                groups[group] = ET.SubElement(
                    container, _tag("Code", ns), guid=ids("group", group), name=group,
                    isCodable="false", color="#999999",
                )  # fmt: skip
            holder = groups[group]
        element = ET.SubElement(
            holder,
            _tag("Code", ns),
            guid=guids[code],
            name=code,
            isCodable="true",
            color=COLORS[index % len(COLORS)],
        )
        if label and label != code:
            ET.SubElement(element, _tag("Description", ns)).text = label
    branch = ET.SubElement(
        container,
        _tag("Code", ns),
        guid=ids("code", EVIDENCE_BRANCH),
        name=EVIDENCE_BRANCH,
        isCodable="false",
        color="#BBBBBB",
    )
    ET.SubElement(branch, _tag("Description", ns)).text = (
        "Vom LLM genannte Signalwörter, im Text verortet. Getrennt von den Hauptcodes, damit sie "
        "den Vergleich der Kodierungen nicht verfälschen."
    )
    for index, (code, _) in enumerate(codes):
        if cb.abstain and code == cb.abstain_id:
            continue
        guids[f"evidence:{code}"] = ids("code", "evidence", code)
        ET.SubElement(
            branch,
            _tag("Code", ns),
            guid=guids[f"evidence:{code}"],
            name=f"{code} (Evidenz)",
            isCodable="true",
            color=COLORS[index % len(COLORS)],
        )
    return guids


def export_qdc(project: Project, path: Path) -> None:
    cfg = project.config
    if cfg.codebook is None:
        raise ValueError("Es gibt noch kein Codebuch.")
    ET.register_namespace("", CODEBOOK_NS)
    root = ET.Element(_tag("CodeBook", CODEBOOK_NS), origin=f"llmanno {version('llmanno')}")
    _codebook(ET.SubElement(root, _tag("Codes", CODEBOOK_NS)), cfg, _Ids(cfg), CODEBOOK_NS)
    for element in root.iter():
        if element.text:
            element.text = _XML_ILLEGAL.sub("", element.text)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    project.store.log("exported", format="qdc", file=path.name)


def _layout(units: list[Unit], grouped: bool) -> list[tuple[str, str, dict[str, int]]]:
    """Quellen als (Name, Text, Unit-ID → Anfang des Zeilentexts)."""
    sequences = group_sequences(units) if grouped else {u.unit_id: [u] for u in units}
    sources = []
    for name, seq in sequences.items():
        parts, starts, position = [], {}, 0
        for unit in seq:
            header = f"[{unit.unit_id}] " + (f"{unit.speaker}: " if unit.speaker else "")
            starts[unit.unit_id] = position + len(header)
            parts.append(f"{header}{unit.text}\n")
            position += len(parts[-1])
        sources.append((str(name), "".join(parts), starts))
    return sources


def _span_mapper(item: Item, starts: dict[str, int]):
    """Position im Zieltext der Einheit (Zeilen mit "\\n" verbunden) → Position in der Quelle."""
    offsets, position = [], 0
    for line in item.lines:
        offsets.append((position, starts[line.unit_id], len(line.text)))
        position += len(line.text) + 1

    def to_source(target: int) -> int:
        for begin, source, length in reversed(offsets):
            if target >= begin:
                return source + min(target - begin, length)
        return offsets[0][1]

    return to_source


def _memo(item_id: str, result: ItemResult) -> str:
    lines = [f"LLM-Ergebnis für Einheit {item_id}", f"Final: {', '.join(result.final)}"]
    ranking = (f"{s.code} (p_sc {s.p_sc:.2f}, p_verbal {s.p_verbal:.2f})" for s in result.ranking)
    lines.append("Top-k: " + "; ".join(ranking))
    if result.rationale:
        lines.append(
            "Begründung (LLM-Plausibilisierung, keine Erklärung der Entscheidung): "
            + result.rationale
        )
    if result.flags:
        lines.append("Flags: " + ", ".join(result.flags))
    return "\n".join(lines)


@dataclass
class _System:
    """Ein Codesystem im Export: eigene Codes, Ergebnisse, Gold und Reviews."""

    name: str
    project: Project
    ids: Callable[..., str]
    items: list[Item]
    results: dict[str, ItemResult]
    gold: GoldTable
    reviews: dict[str, list[Review]]
    abstain: str | None
    code_ids: dict[str, str] = field(default_factory=dict)
    by_source: dict[int, list[Item]] = field(default_factory=dict)
    codings: int = 0


def _system(name: str, project: Project, ids: Callable[..., str], only_dev: bool) -> _System:
    cfg = project.config
    if cfg.data is None or cfg.codebook is None:
        raise ValueError("Es fehlen noch Daten oder das Codebuch.")
    _, items = prepare_prompts(project)
    hidden = hidden_test_items(project) if only_dev else set()
    items = [i for i in items if i.item_id not in hidden]
    reviews: dict[str, list[Review]] = {}
    for review in sorted(
        (r for r in latest_reviews(project).values() if r.item_id not in hidden),
        key=lambda r: (r.item_id, r.ts),
    ):
        reviews.setdefault(review.item_id, []).append(review)
    by_item = results(project, [i.item_id for i in items])
    gold = build_gold(cfg, project.store.units(), items)
    abstain = cfg.codebook.abstain_id if cfg.codebook.abstain else None
    return _System(name, project, ids, items, by_item, gold, reviews, abstain)


def _review_user(review: Review) -> str:
    return f"{review.reviewer} ({REVIEW_MODES[review.mode]})"


def _same_units(units: list[Unit]) -> list[tuple]:
    return [(u.unit_id, u.group, u.order, u.speaker, u.text) for u in units]


def export_qdpx(
    project: Project,
    path: Path,
    *,
    only_dev: bool = False,
    others: Mapping[str, Project] | None = None,
) -> RefiSummary:
    """`only_dev`: vor dem Freeze ohne Kodierungen der Test-Einheiten (die Texte bleiben).
    `others`: weitere Codesysteme (core.codesystems) in derselben Datei, je mit eigenem
    Code-Zweig; sie müssen dieselben Einheiten kodieren."""
    cfg = project.config
    assert cfg.data is not None and cfg.codebook is not None
    others = others or {}
    if cfg.name in others:
        raise ValueError(f"Zwei Codesysteme heißen „{cfg.name}“.")
    units = project.store.units()
    for name, other in others.items():
        if _same_units(other.store.units()) != _same_units(units):
            raise ValueError(
                f"Das Codesystem „{name}“ kodiert andere Daten als „{cfg.name}“; bitte einzeln "
                "exportieren."
            )
    # Reihenfolge und Kennungen hängen nicht davon ab, welches Codesystem gewählt ist: nach
    # Anlage geordnet, das zuerst angelegte (das Hauptprojekt) gibt die Kennungen vor.
    ordered = sorted({cfg.name: project, **others}.items(), key=lambda kv: kv[1].config.created)
    base = ordered[0][1].config
    ids = _Ids(base)
    now = _now()
    combined = bool(others)
    systems = []
    for name, p in ordered:
        # Gemeinsam: Kennungen je Codesystem; einzeln wie bisher, damit sie stabil bleiben.
        scoped = (lambda *parts, n=name: ids(n, *parts)) if combined else ids
        try:
            systems.append(_system(name, p, scoped, only_dev))
        except ValueError as exc:
            if not combined:
                raise
            raise ValueError(f"Codesystem „{name}“: {exc}") from exc

    users = [LLM_USER]
    for system in systems:
        users += [c for c in system.gold.columns if c not in users]
    for system in systems:
        for reviews in system.reviews.values():
            users += [u for u in map(_review_user, reviews) if u not in users]
    user_ids = {name: ids("user", name) for name in users}

    ET.register_namespace("", NS)
    root = ET.Element(
        _tag("Project"),
        name=base.name,
        origin=f"llmanno {version('llmanno')}",
        creatingUserGUID=user_ids[LLM_USER],
        creationDateTime=now,
    )
    users_el = ET.SubElement(root, _tag("Users"))
    for name in users:
        ET.SubElement(users_el, _tag("User"), guid=user_ids[name], name=name)
    codes_el = ET.SubElement(ET.SubElement(root, _tag("CodeBook")), _tag("Codes"))
    for system in systems:
        holder = codes_el
        if combined:  # je Codesystem ein nicht kodierbarer Obercode
            holder = ET.SubElement(
                codes_el, _tag("Code"), guid=system.ids("system"), name=system.name,
                isCodable="false", color="#666666",
            )  # fmt: skip
        system.code_ids = _codebook(holder, system.project.config, system.ids, NS)

    grouped = cfg.data.mapping.group is not None
    layout = _layout(units, grouped)
    source_of = {uid: n for n, (_, _, starts) in enumerate(layout) for uid in starts}
    for system in systems:
        for item in system.items:
            system.by_source.setdefault(source_of[item.lines[0].unit_id], []).append(item)

    sources_el = ET.SubElement(root, _tag("Sources"))
    notes: list[tuple[str, str, str]] = []  # (guid, name, text)
    files: dict[str, str] = {}

    def selection(parent, system, key, start, end, user, codes, note=None):
        element = ET.SubElement(
            parent,
            _tag("PlainTextSelection"),
            guid=system.ids("selection", *key),
            name=key[0],
            startPosition=str(start),
            endPosition=str(end),
            creatingUser=user_ids[user],
            creationDateTime=now,
        )
        for code in codes:
            coding = ET.SubElement(
                element,
                _tag("Coding"),
                guid=system.ids("coding", *key, code),
                creatingUser=user_ids[user],
                creationDateTime=now,
            )
            ET.SubElement(coding, _tag("CodeRef"), targetGUID=system.code_ids[code])
            system.codings += 1
        if note is not None:
            ET.SubElement(element, _tag("NoteRef"), targetGUID=note)

    for index, (name, text, starts) in enumerate(layout):
        source_guid = ids("source", name)
        files[f"Sources/{source_guid}.txt"] = text
        source = ET.SubElement(
            sources_el,
            _tag("TextSource"),
            guid=source_guid,
            name=name,
            plainTextPath=f"internal://{source_guid}.txt",
            creatingUser=user_ids[LLM_USER],
            creationDateTime=now,
        )
        for system, item in ((s, i) for s in systems for i in s.by_source.get(index, [])):
            first, last = item.lines[0], item.lines[-1]
            start, end = starts[first.unit_id], starts[last.unit_id] + len(last.text)
            result = system.results[item.item_id]
            code_ids = system.code_ids
            # Ergebnisse stammen aus dem letzten Lauf; Codes, die das Codebuch inzwischen
            # nicht mehr führt, haben keinen Platz im Codesystem.
            final = [c for c in result.final if c in code_ids]
            if result.status == "ok" and final:
                note_guid = system.ids("note", item.item_id)
                title = f"LLM {system.name}: {item.item_id}" if combined else f"LLM {item.item_id}"
                notes.append((note_guid, title, _memo(item.item_id, result)))
                key = (item.item_id, LLM_USER)
                selection(source, system, key, start, end, LLM_USER, final, note_guid)
                to_source = _span_mapper(item, starts)
                seen = set()
                for code in final:
                    if code == system.abstain or f"evidence:{code}" not in code_ids:
                        continue
                    for span in result.evidence.get(code, []):
                        if span.start is None or span.end is None:
                            continue
                        bounds = (to_source(span.start), to_source(span.end))
                        if (code, bounds) in seen:
                            continue
                        seen.add((code, bounds))
                        key = (item.item_id, LLM_USER, "evidence", code, *bounds)
                        selection(source, system, key, *bounds, LLM_USER, [f"evidence:{code}"])
            for column in system.gold.columns:
                label = system.gold.labels[column][item.item_id]
                if label is not None:
                    codes = sorted(label) if isinstance(label, frozenset) else [label]
                    codes = [c for c in codes if c in code_ids]
                    if codes:
                        selection(source, system, (item.item_id, column), start, end, column, codes)
            for review in system.reviews.get(item.item_id, []):
                user = _review_user(review)
                codes = [c for c in review.codes if c in code_ids]
                if codes:
                    selection(source, system, (item.item_id, user), start, end, user, codes)

    notes_el = ET.SubElement(root, _tag("Notes"))
    for system in reversed(systems):
        title = f"Codebuch {system.name} (verbatim)" if combined else "Codebuch (verbatim)"
        notes.insert(0, (system.ids("note", "codebook"), title, system.project.codebook_text or ""))
    for guid, name, text in notes:
        note = ET.SubElement(
            notes_el,
            _tag("Note"),
            guid=guid,
            name=name,
            creatingUser=user_ids[LLM_USER],
            creationDateTime=now,
        )
        ET.SubElement(note, _tag("PlainTextContent")).text = text
    hashes = [
        (s.name, s.project.config.freeze.instrument_hash if s.project.config.freeze else None)
        for s in systems
    ]
    instrument = "; ".join(
        (f"{name}: " if combined else "") + (value or "nicht eingefroren") for name, value in hashes
    )
    ET.SubElement(root, _tag("Description")).text = (
        f"Exportiert aus llmanno {version('llmanno')}. Instrument-Hash: {instrument}. "
        "Begründungen in den Notizen sind LLM-Plausibilisierungen, keine Erklärungen."
    )

    for element in root.iter():
        if element.text:
            element.text = _XML_ILLEGAL.sub("", element.text)
        for key, value in element.attrib.items():
            element.attrib[key] = _XML_ILLEGAL.sub("", value)
    xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("project.qde", xml)
        for name, text in files.items():
            archive.writestr(name, text.encode("utf-8"))
    summary = RefiSummary(len(layout), sum(s.codings for s in systems), users)
    for system in systems:
        note_test_exposure(system.project, "qdpx", [i.item_id for i in system.items])
        system.project.store.log(
            "exported", format="qdpx", file=path.name, sources=summary.sources,
            codings=system.codings,
        )  # fmt: skip
    return summary
