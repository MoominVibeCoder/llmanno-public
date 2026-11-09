"""XLSX-Export: Ergebnisse pro Einheit (wide), alle Läufe (long), Codes und Laufinformationen."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE, Cell
from openpyxl.worksheet.worksheet import Worksheet

from llmanno.core.aggregate import ItemResult
from llmanno.core.engine import result_basis, results
from llmanno.core.examples import seen_example_ids
from llmanno.core.project import Project
from llmanno.core.prompt import TEMPLATE_VERSION
from llmanno.core.review import review_status
from llmanno.core.store import CallRecord, Review
from llmanno.core.units import Item, build_items
from llmanno.core.validation import hidden_test_items, note_test_exposure

# Schlechtester Status gewinnt, damit unsichere Belege in der Tabelle auffallen.
_EVIDENCE_RANK = {"exact": 0, "normalized": 1, "fuzzy": 2, "missing": 3}
NOTE = (
    "Begründungen sind nachträgliche Plausibilisierungen des Modells, keine kausalen "
    "Erklärungen seiner Entscheidung. Prüfbar sind die Signalwörter."
)


def export_xlsx(
    project: Project,
    path: Path,
    *,
    only_dev: bool = False,
    others: Mapping[str, Project] | None = None,
) -> None:
    """`only_dev`: vor dem Freeze ohne Ergebnisse, Läufe und Reviews der Test-Einheiten.
    `others`: weitere Codesysteme (core.codesystems) als eigene Blätter, „Ergebnisse – Name“."""
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    done = [_add_sheets(workbook, project, only_dev, "")]
    for name, other in (others or {}).items():
        done.append(_add_sheets(workbook, other, only_dev, f" – {name}"))
    workbook.save(path)
    for exported, item_ids in done:  # erst nach dem Speichern: sonst gäbe es keine Datei
        note_test_exposure(exported, "xlsx", item_ids)
        exported.store.log("exported", format="xlsx", file=path.name, n_items=len(item_ids))


_SHEET_FORBIDDEN = re.compile(r"[\\/*?:\[\]]")


def sheet_title(workbook, title: str) -> str:
    """Excel: höchstens 31 Zeichen, ohne \\ / * ? : [ ], eindeutig."""
    title = _SHEET_FORBIDDEN.sub("_", title)[:31]
    taken = {name.casefold() for name in workbook.sheetnames}
    candidate, number = title, 1
    while candidate.casefold() in taken:
        number += 1
        candidate = f"{title[: 31 - len(str(number)) - 1]}~{number}"
    return candidate


def _add_sheets(
    workbook, project: Project, only_dev: bool, suffix: str
) -> tuple[Project, list[str]]:
    cfg = project.config
    units = project.store.units()
    hidden = hidden_test_items(project) if only_dev else set()
    items = [i for i in build_items(units, cfg.coding.units) if i.item_id not in hidden]
    by_item = results(project, [i.item_id for i in items])

    def sheet(title: str) -> Worksheet:
        return workbook.create_sheet(sheet_title(workbook, f"{title}{suffix}"))

    sources = {u.unit_id: u.source for u in units}
    _results_sheet(sheet("Ergebnisse"), project, items, by_item, sources)
    _runs_sheet(sheet("Läufe"), project, hidden)
    _codes_sheet(sheet("Codes"), project)
    if reviews := [r for r in project.store.reviews() if r.item_id not in hidden]:
        _reviews_sheet(sheet("Reviews"), reviews)
    _info_sheet(sheet("Info"), project)
    return project, [i.item_id for i in items]


def _results_sheet(
    sheet: Worksheet,
    project: Project,
    items: list[Item],
    by_item: dict[str, ItemResult],
    sources: dict[str, dict[str, Any]],
) -> None:
    cfg = project.config
    holistic = cfg.coding.units.mode == "holistic"
    source_columns = [] if holistic else list(next(iter(sources.values()), {}))
    shown = seen_example_ids(project)
    ranked = [
        f"llm_{field}_{i}" for i in range(1, cfg.k + 1) for field in ("code", "p_sc", "p_verbal")
    ]
    header = (
        ["item_id"]
        + (["einheiten", "text"] if holistic else source_columns)
        + ["llm_status", "llm_final"]
        + ranked
        + ["llm_begruendung", "llm_signalwoerter", "llm_evidenz_status", "llm_entropie"]
        + ["llm_stimmen", "llm_flags", "llm_n_gueltig", "llm_n_ungueltig", "llm_n_fehler"]
        + ["split", "review_status"]
        + (["beispiel_im_prompt"] if shown else [])  # Gold im Prompt: nicht auswerten
    )
    parts = project.store.split()
    reviewed = review_status(project)
    append_row(sheet, header)
    for item in items:
        result = by_item[item.item_id]
        if holistic:
            first = [item.item_id, "; ".join(ln.unit_id for ln in item.lines), item.target]
        else:
            source = sources[item.item_id]
            first = [item.item_id] + [source.get(c) for c in source_columns]
        scores: list[Any] = []
        for i in range(cfg.k):
            if i < len(result.ranking):
                s = result.ranking[i]
                scores += [s.code, round(s.p_sc, 4), round(s.p_verbal, 4)]
            else:
                scores += [None, None, None]
        append_row(
            sheet,
            first
            + [result.status, "; ".join(result.final) or None]
            + scores
            + [
                result.rationale,
                _evidence_text(result),
                _worst_status(result),
                None if result.entropy is None else round(result.entropy, 4),
                "; ".join(f"{m}: {', '.join(v)}" for m, v in result.votes.items()) or None,
                "; ".join(result.flags) or None,
                result.n_valid,
                result.n_invalid,
                result.n_error,
                parts.get(item.item_id),
                reviewed.get(item.item_id),
            ]
            + ([int(item.item_id in shown)] if shown else []),
        )
    sheet.freeze_panes = "B2"


def _evidence_text(result: ItemResult) -> str | None:
    parts = [
        f"{code}: " + " | ".join(span.text for span in spans)
        for code, spans in result.evidence.items()
        if spans
    ]
    return "; ".join(parts) or None


def _worst_status(result: ItemResult) -> str | None:
    statuses = [span.status for spans in result.evidence.values() for span in spans]
    return max(statuses, key=_EVIDENCE_RANK.__getitem__) if statuses else None


def _runs_sheet(sheet: Worksheet, project: Project, hidden: set[str]) -> None:
    k = project.config.k
    ranked = [f"{field}_{i}" for i in range(1, k + 1) for field in ("code", "konfidenz")]
    sheet.append(
        ["item_id", "modell", "lauf", "seed", "status", "config_hash"]
        + ranked
        + ["begruendung", "signalwoerter", "flags", "fehler", "tokens_in", "tokens_out"]
        + ["latenz_s", "modell_gemeldet", "zeit"]
    )
    for call in project.store.calls(with_raw=False):
        if call.item_id not in hidden:
            append_row(sheet, _run_row(call, k))
    sheet.freeze_panes = "B2"


def _run_row(call: CallRecord, k: int) -> list[Any]:
    candidates = (call.answer or {}).get("candidates", [])
    ranked: list[Any] = []
    for i in range(k):
        cand = candidates[i] if i < len(candidates) else None
        ranked += [cand["code"], round(cand["confidence"], 2)] if cand else [None, None]
    evidence = "; ".join(
        f"{c['code']}: " + " | ".join(f"{e['text']} [{e['status']}]" for e in c["evidence"])
        for c in candidates
        if c["evidence"]
    )
    return (
        [call.item_id, call.model_key, call.run, call.seed, call.status, call.config_hash]
        + ranked
        + [
            (call.answer or {}).get("rationale"),
            evidence or None,
            "; ".join(call.flags) or None,
            call.error,
            call.meta.get("tokens_in"),
            call.meta.get("tokens_out"),
            call.meta.get("latency_s"),
            call.meta.get("model_returned"),
            call.ts.isoformat(timespec="seconds"),
        ]
    )


def _reviews_sheet(sheet: Worksheet, reviews: list[Review]) -> None:
    """Alle Reviews, auch ersetzte; `gueltig` markiert die jüngste je Einheit, Person, Modus."""
    latest = {(r.item_id, r.reviewer, r.mode): r for r in reviews}
    sheet.append(
        ["item_id", "person", "modus", "codes", "notiz", "queue_gruende"]
        + ["ziehwahrscheinlichkeit", "gueltig", "zeit"]
    )
    for r in reviews:
        mode = "blind" if r.mode == "blind" else "LLM-gestützt korrigiert"
        current = latest[(r.item_id, r.reviewer, r.mode)] is r
        append_row(
            sheet,
            [
                r.item_id, r.reviewer, mode, ";".join(r.codes), r.note, ";".join(r.reasons),
                r.draw_probability, "ja" if current else "nein", r.ts.isoformat(timespec="seconds"),
            ],
        )  # fmt: skip
    sheet.freeze_panes = "B2"


def _codes_sheet(sheet: Worksheet, project: Project) -> None:
    sheet.append(["code", "label", "rang", "aktiv"])
    codebook = project.config.codebook
    if codebook is None:
        return
    for code in codebook.codes:
        append_row(sheet, [code.id, code.label, code.rank, code.active])
    if codebook.abstain:
        append_row(sheet, [codebook.abstain_id, codebook.abstain_label, None, True])


def _info_sheet(sheet: Worksheet, project: Project) -> None:
    cfg = project.config
    rows: list[tuple[str, Any]] = [
        ("Projekt", cfg.name),
        ("Exportiert", datetime.now(UTC).isoformat(timespec="seconds")),
        ("Software", f"llmanno {version('llmanno')}"),
        ("Prompt-Template", f"Version {TEMPLATE_VERSION}, Sprache {cfg.prompt_language}"),
        ("Kodiermodus", f"{cfg.mode}, k = {cfg.k}"),
        ("Einheiten", cfg.coding.units.model_dump_json()),
        ("Kodierparameter", cfg.coding.model_dump_json(exclude={"units"})),
    ]
    if cfg.data:
        rows += [
            ("Daten", f"{cfg.data.source_name}, Blatt {cfg.data.sheet}"),
            ("Daten SHA-256", cfg.data.file_sha256),
            ("Einheiten SHA-256", cfg.data.units_sha256),
        ]
    if cfg.codebook:
        rows += [("Codebuch", cfg.codebook.source_name), ("Codebuch SHA-256", cfg.codebook.sha256)]
    basis = result_basis(project) if cfg.panel and cfg.codebook else None
    for model in cfg.panel:
        used = basis.runs.get(model.key) if basis else None
        state = {"config_hash": used, "ausgeschlossen": bool(basis and model.key in basis.excluded)}
        rows.append((f"Modell {model.key}", json.dumps({**state, **model.model_dump()})))
    rows.append(("Hinweis", NOTE))
    for key, value in rows:
        append_row(sheet, [key, value])
    sheet.column_dimensions["A"].width = 22
    sheet.column_dimensions["B"].width = 100


def append_row(sheet: Worksheet, values: list[Any]) -> None:
    # Zellen vorab bauen: sheet[sheet.max_row] liefe je Zeile über alle Zellen (quadratisch).
    cells = [Cell(sheet, value=_safe(v)) for v in values]
    for cell in cells:
        # Texte, die mit "=" beginnen, wären sonst Formeln (Formel-Injection aus den Daten).
        if isinstance(cell.value, str) and cell.value.startswith("="):
            cell.data_type = "s"
    sheet.append(cells)


EXCEL_CELL_LIMIT = 32767
TRUNCATED = " …[gekürzt]"


def _safe(value: Any) -> Any:
    if isinstance(value, dict | list):
        value = json.dumps(value, ensure_ascii=False)
    if isinstance(value, str):
        value = ILLEGAL_CHARACTERS_RE.sub("", value)
        # Längere Zellen meldet Excel als beschädigte Datei; der volle Text steht im Store.
        if len(value) > EXCEL_CELL_LIMIT:
            value = value[: EXCEL_CELL_LIMIT - len(TRUNCATED)] + TRUNCATED
    return value
