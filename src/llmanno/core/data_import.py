"""XLSX oder CSV lesen und per Spalten-Mapping in Einheiten überführen, mit Prüfbericht.

Mehrere Dateien oder Blätter (etwa ein Transkript je Stunde) führt merge_sources zu einer
XLSX-Datei zusammen; importiert wird dann diese eine Datei wie jede andere.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Literal

import openpyxl
from openpyxl.cell import WriteOnlyCell
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

from llmanno.core.config import ColumnMapping
from llmanno.core.store import Unit


@dataclass(frozen=True)
class Sheet:
    name: str
    header: list[str]
    rows: list[tuple[int, list[Any]]]  # (Excel-Zeilennummer, Werte)
    notes: tuple[str, ...] = ()  # Auffälligkeiten beim Lesen, landen im Prüfbericht


@dataclass(frozen=True)
class Issue:
    severity: Literal["error", "warning"]  # error: Zeile wird übersprungen
    row: int | None
    message: str


@dataclass(frozen=True)
class ImportReport:
    units: list[Unit]
    issues: list[Issue]

    @property
    def n_skipped(self) -> int:
        return sum(1 for i in self.issues if i.severity == "error" and i.row is not None)


CSV_SHEET = "CSV"  # eine CSV-Datei hat genau ein „Blatt“
SOURCE_COLUMN, SOURCE_ID_COLUMN = "Quelle", "Quelle_ID"


def is_csv(path: Path) -> bool:
    return path.suffix.lower() in (".csv", ".tsv")


def sheet_names(path: Path) -> list[str]:
    if is_csv(path):
        return [CSV_SHEET]
    workbook = openpyxl.load_workbook(path, read_only=True)
    try:
        return workbook.sheetnames
    finally:
        workbook.close()


def read_sheet(path: Path, name: str | None = None) -> Sheet:
    if is_csv(path):
        return _read_csv(path)
    # data_only: Formelzellen liefern ihren berechneten Wert, nicht die Formel.
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        worksheet = workbook[name] if name else workbook.worksheets[0]
        values = worksheet.iter_rows(values_only=True)
        first = next(values, None)
        if first is None:
            raise ValueError(f"Blatt „{worksheet.title}“ ist leer.")
        header = _unique_header(first)
        rows = []
        for number, row in enumerate(values, start=2):
            cells = [_json_safe(v) for v in row]
            if any(v is not None and v != "" for v in cells):
                cells += [None] * (len(header) - len(cells))
                rows.append((number, cells[: len(header)]))
        return Sheet(worksheet.title, header, rows)
    finally:
        workbook.close()


_INTEGER = re.compile(r"-?(0|[1-9][0-9]*)")
_DECIMAL = re.compile(r"-?[0-9]+\.[0-9]+")


def _read_csv(path: Path) -> Sheet:
    """CSV lesen: Trennzeichen (, ; Tab) aus der Kopfzeile, UTF-8 oder Windows-Kodierung,
    Zahlen als Zahlen, sofern sie dabei unverändert bleiben (siehe _csv_value)."""
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    # Trennzeichen aus der Kopfzeile: Spaltennamen enthalten es kaum, Texte schon (csv.Sniffer
    # rät bei ungleich langen Zeilen falsch). Ohne Trennzeichen ist die Datei einspaltig.
    head = text.split("\n", 1)[0]
    delimiter = max(",;\t", key=head.count)
    if not head.count(delimiter):
        delimiter = "\x1f"  # kommt in Texten nicht vor: Kommas bleiben im Text
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    first = next(reader, None)
    if not first:
        raise ValueError(f"{path.name} ist leer.")
    rows = []
    for number, row in enumerate(reader, start=2):
        cells = [_csv_value(v) for v in row]
        if any(v is not None for v in cells):
            rows.append((number, cells))
    # Überzählige Felder bekommen eigene Spalten wie in Excel, statt still zu verschwinden.
    width = max([len(first), *(len(cells) for _, cells in rows)])
    header = _unique_header(tuple(first) + (None,) * (width - len(first)))
    notes = ()
    if longer := [number for number, cells in rows if len(cells) > len(first)]:
        notes = (
            f"{len(longer)} Zeilen haben mehr Felder als die Kopfzeile (etwa Zeile "
            f"{longer[0]}); die überzähligen stehen in eigenen Spalten. Steht das Trennzeichen "
            "ungeschützt im Text, die Datei mit Anführungszeichen um die Texte speichern.",
        )
    rows = [(n, cells + [None] * (width - len(cells))) for n, cells in rows]
    return Sheet(CSV_SHEET, header, rows, notes)


def _csv_value(text: str) -> Any:
    """Zahl nur, wenn sie zurückgeschrieben genauso aussieht: „1.10“, „2.0“ oder „007“
    bleiben Text, sonst fielen IDs oder Codes wie 1.1 und 1.10 zusammen."""
    value = text.strip()
    if not value:
        return None
    if _INTEGER.fullmatch(value):
        return int(value)
    if _DECIMAL.fullmatch(value) and str(float(value)) == value and not float(value).is_integer():
        return float(value)
    return text


def merge_sources(sources: list[tuple[Path, str]], out: Path) -> list[str]:
    """Mehrere Dateien oder Blätter zu einer XLSX-Datei. Vorn stehen „Quelle“ (Datei, bei
    mehreren Blättern einer Datei mit Blatt; als Gruppe gedacht) und „Quelle_ID“ (Quelle und
    Zeile, eindeutig auch dann, wenn jede Datei ihre Zeilen ab 1 zählt). Spalten, die nicht
    überall vorkommen, bleiben dort leer. Liefert Hinweise."""
    if len(sources) < 2:
        raise ValueError("Zum Zusammenführen mindestens zwei Dateien oder Blätter wählen.")
    per_file = Counter(path for path, _ in sources)
    sheets, labels = [], []
    for path, name in sources:
        sheets.append(read_sheet(path, name))
        label = f"{path.stem} › {name}" if per_file[path] > 1 else path.stem
        candidate, n = label, 2
        while candidate in labels:
            candidate, n = f"{label} ({n})", n + 1
        labels.append(candidate)
    header: list[str] = []
    for sheet in sheets:
        header += [c for c in sheet.header if c not in header]
    taken = [c for c in (SOURCE_COLUMN, SOURCE_ID_COLUMN) if c in header]
    if taken:
        raise ValueError(
            f"Die Spalte „{taken[0]}“ gibt es schon; das Zusammenführen legt sie selbst an. "
            "Bitte umbenennen."
        )
    notes = []
    for label, sheet in zip(labels, sheets, strict=True):
        if missing := [c for c in header if c not in sheet.header]:
            notes.append(f"„{label}“ hat die Spalten {', '.join(missing)} nicht; sie bleiben leer.")
    workbook = openpyxl.Workbook(write_only=True)
    target = workbook.create_sheet("Daten")

    def cell(value: Any) -> WriteOnlyCell:
        if isinstance(value, str):
            value = ILLEGAL_CHARACTERS_RE.sub("", value)  # etwa Seitenumbrüche aus PDFs
        written = WriteOnlyCell(target, value=value)
        if isinstance(value, str) and value.startswith("="):
            written.data_type = "s"  # „=“ am Turn-Anfang (GAT) ist Text, keine Formel
        return written

    target.append([cell(c) for c in (SOURCE_COLUMN, SOURCE_ID_COLUMN, *header)])
    for label, sheet in zip(labels, sheets, strict=True):
        for number, cells in sheet.rows:
            record = dict(zip(sheet.header, cells, strict=True))
            values = [label, f"{label}:{number}", *(record.get(c) for c in header)]
            target.append([cell(v) for v in values])
    workbook.save(out)
    return notes


def _unique_header(cells: tuple[Any, ...]) -> list[str]:
    header: list[str] = []
    for index, cell in enumerate(cells, start=1):
        name = str(cell).strip() if cell not in (None, "") else f"Spalte {index}"
        candidate, n = name, 2
        while candidate in header:
            candidate, n = f"{name} ({n})", n + 1
        header.append(candidate)
    return header


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return int(value)  # Excel speichert 17 als 17.0; IDs und Codes sollen „17“ bleiben
    return value


def _as_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def import_units(sheet: Sheet, mapping: ColumnMapping) -> ImportReport:
    missing = [c for c in mapping.columns() if c not in sheet.header]
    if missing:
        raise ValueError(
            f"Spalten fehlen im Blatt: {', '.join(missing)}. Sind Datei und Blatt richtig gewählt?"
        )

    issues: list[Issue] = [Issue("warning", None, note) for note in sheet.notes]
    if mapping.id is None:
        issues.append(
            Issue(
                "warning",
                None,
                "Keine ID-Spalte gewählt: Einheiten heißen nach ihrer Zeilennummer (Z2, Z3, …). "
                "Diese IDs ändern sich, sobald Zeilen eingefügt oder sortiert werden.",
            )
        )

    units: list[Unit] = []
    seen: set[str] = set()
    for number, cells in sheet.rows:
        record = dict(zip(sheet.header, cells, strict=True))
        unit_id = _as_text(record[mapping.id]) if mapping.id else f"Z{number}"
        if not unit_id:
            issues.append(Issue("error", number, "ID fehlt."))
            continue
        if unit_id in seen:
            issues.append(Issue("error", number, f"Doppelte ID „{unit_id}“."))
            continue

        text = _join_text(record, mapping.text)
        if not text:
            issues.append(Issue("error", number, f"Kein Text in Einheit „{unit_id}“."))
            continue

        order = float(number)  # wie aus dem Store gelesen (Spalte REAL), sonst andere Prüfsumme
        if mapping.order:
            raw_order = record[mapping.order]
            if isinstance(raw_order, int | float) and not isinstance(raw_order, bool):
                order = float(raw_order)
            else:
                issues.append(
                    Issue(
                        "warning",
                        number,
                        f"Reihenfolge „{raw_order}“ ist keine Zahl, Zeilennummer wird verwendet.",
                    )
                )

        seen.add(unit_id)
        units.append(
            Unit(
                unit_id=unit_id,
                row=number,
                group=_optional_text(record, mapping.group),
                order=order,
                speaker=_optional_text(record, mapping.speaker),
                text=text,
                meta={c: record[c] for c in mapping.meta if _as_text(record[c])},
                gold={c: record[c] for c in [*mapping.gold, mapping.consensus] if c},
                source=record,
            )
        )
    if mapping.order:
        issues += _order_issues(units)
    return ImportReport(units, issues)


def _order_issues(units: list[Unit]) -> list[Issue]:
    """Reihenfolgen, die den Kontext still vertauschen können."""
    issues = []
    if fractional := next((u for u in units if not u.order.is_integer()), None):
        issues.append(
            Issue(
                "warning",
                fractional.row,
                f"Die Reihenfolge enthält Dezimalzahlen (z. B. {fractional.order:g}). Ist sie als "
                "Gliederung gemeint (1.2 vor 1.10), stimmt sie nicht: Excel liest 1.10 als 1,1. "
                "Für eine eindeutige Reihenfolge ganze Zahlen verwenden.",
            )
        )
    counts = Counter((u.group, u.order) for u in units)
    if duplicates := [key for key, n in counts.items() if n > 1]:
        group, order = duplicates[0]
        issues.append(
            Issue(
                "warning",
                None,
                f"{len(duplicates)} Reihenfolge-Werte kommen in einer Gruppe mehrfach vor (z. B. "
                f"{order:g} in „{group or '–'}“); dort entscheidet die Zeilennummer über den "
                "Kontext.",
            )
        )
    return issues


def _optional_text(record: dict[str, Any], column: str | None) -> str | None:
    return (_as_text(record[column]) or None) if column else None


def _join_text(record: dict[str, Any], columns: list[str]) -> str:
    if len(columns) == 1:
        return _as_text(record[columns[0]])
    parts = [f"{c}: {_as_text(record[c])}" for c in columns if _as_text(record[c])]
    return "\n".join(parts)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def units_sha256(units: list[Unit], *, legacy: bool = False) -> str:
    """Prüfsumme der zugeordneten Felder ohne Gold und ohne die übrige Quellzeile: Gold gehört
    nicht zum Instrument und lässt sich nachtragen (Project.update_gold), nicht zugeordnete
    Spalten sieht das LLM nicht. `legacy` rechnet wie Projekte vor dieser Regel, mit Gold und
    ganzer Zeile; deren gespeicherte Prüfsumme bleibt, damit ihre Läufe gültig bleiben."""

    def content(unit: Unit) -> dict[str, Any]:
        data = asdict(unit)
        if not legacy:
            del data["gold"], data["source"]
        return data

    payload = json.dumps([content(u) for u in units], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
