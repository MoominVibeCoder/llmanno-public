"""Gold aus einem QDA-Programm übernehmen (Roadmap 42): ein REFI-QDA-Projekt (.qdpx), das aus
dem llmanno-Export stammt und dort von Menschen kodiert wurde (MAXQDA, ATLAS.ti, QualCoder …).

Die Einheiten findet der Import über die Marker „[ID]“ am Zeilenanfang im Text, den das
QDA-Programm zurückgibt, nicht über die Positionen des Exports: Manche Programme ändern
Zeilenenden. Eine Kodierung zählt für jede Einheit, deren Zeile sie überlappt. Codes werden über
den Namen zugeordnet („K1“ oder „K1 …“); der Zweig „LLM-Evidenz“ bleibt außen vor. Übernommen
wird über den Gold-Nachtrag (Project.update_gold): Lauf-Identitäten und Freeze bleiben.
"""

from __future__ import annotations

import csv
import re
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

import openpyxl

from llmanno.core import data_import
from llmanno.core.export.refi import EVIDENCE_BRANCH, LLM_USER, REVIEW_MODES
from llmanno.core.project import Project

_MARKER = re.compile(r"^\[([^\]\r\n]+)\] ", re.M)
SUFFIX = " (QDA)"
# Weniger Überlappung zählt nicht: Markierungen ragen gern ein, zwei Zeichen in die Nachbarzeile.
MIN_OVERLAP = 3


@dataclass
class QdaCoder:
    name: str
    codings: int = 0
    # Einheit → Code → überlappende Zeichen (mehrere Codes: der größte Anteil zuerst)
    overlap: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))

    def labels(self, multi: bool, separator: str) -> dict[str, str]:
        """Gold-Zelle je Einheit; Single-Label nimmt den Code mit der größten Überlappung."""
        out = {}
        for unit, codes in self.overlap.items():
            ranked = [code for code, _ in codes.most_common()]
            out[unit] = separator.join(sorted(ranked)) if multi else ranked[0]
        return out


@dataclass
class QdaImport:
    coders: dict[str, QdaCoder]
    unknown_codes: Counter[str]  # Code-Namen ohne Gegenstück im Codebuch
    units_found: int  # Einheiten, deren Marker im Text stehen


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _code_map(root: ET.Element, ids: list[str]) -> tuple[dict[str, str | None], dict[str, str]]:
    """GUID → Code-Kürzel (None: kein passender Code) und GUID → Name."""
    by_length = sorted(ids, key=len, reverse=True)
    mapping: dict[str, str | None] = {}
    names: dict[str, str] = {}

    def match(name: str) -> str | None:
        name = name.strip()
        for code in by_length:  # „K10 …“ vor „K1“
            if name.endswith("(Evidenz)"):  # Evidenz-Code, auch außerhalb seines Zweigs
                return None
            if name == code or (name.startswith(code) and name[len(code)] in " :—–-(."):
                return code
        return None

    def walk(element: ET.Element, evidence: bool) -> None:
        for child in element:
            if _local(child.tag) != "Code":
                continue
            name, guid = child.get("name", ""), child.get("guid", "")
            inside = evidence or name == EVIDENCE_BRANCH
            names[guid] = name
            codable = child.get("isCodable", "true").lower() != "false"
            mapping[guid] = match(name) if codable and not inside else None
            walk(child, inside)

    for codes in root.iter():
        if _local(codes.tag) == "Codes":
            walk(codes, False)
    return mapping, names


def read_qdpx(path: Path, project: Project) -> QdaImport:
    cfg = project.config
    assert cfg.codebook is not None
    known = {u.unit_id for u in project.store.units()}
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ValueError(f"{path.name} ist kein REFI-QDA-Projekt (.qdpx).") from exc
    with archive:
        names = archive.namelist()
        qde = next((n for n in names if n.lower().endswith(".qde")), None)
        if qde is None:
            raise ValueError(f"{path.name} enthält kein project.qde.")
        root = ET.fromstring(archive.read(qde))
        texts = {Path(n).name: n for n in names if n.lower().startswith("sources/")}

        def source_text(element: ET.Element) -> str | None:
            inline = next((c for c in element if _local(c.tag) == "PlainTextContent"), None)
            if inline is not None:
                return inline.text or ""
            reference = element.get("plainTextPath", "")
            name = reference.removeprefix("internal://").replace("\\", "/").split("/")[-1]
            file = texts.get(name)
            return archive.read(file).decode("utf-8-sig") if file else None

        users = {
            u.get("guid", ""): u.get("name", "") for u in root.iter() if _local(u.tag) == "User"
        }
        code_of, code_names = _code_map(root, cfg.codebook.allowed_ids())
        coders: dict[str, QdaCoder] = {}
        unknown: Counter[str] = Counter()
        found: set[str] = set()
        for source in (e for e in root.iter() if _local(e.tag) == "TextSource"):
            text = source_text(source)
            if not text:
                continue
            markers = [(m.start(), m.group(1)) for m in _MARKER.finditer(text)]
            ranges = [
                (start, markers[i + 1][0] if i + 1 < len(markers) else len(text), unit)
                for i, (start, unit) in enumerate(markers)
                if unit in known
            ]
            found |= {unit for _, _, unit in ranges}
            for selection in source:
                if _local(selection.tag) != "PlainTextSelection":
                    continue
                begin = int(selection.get("startPosition", "0"))
                end = int(selection.get("endPosition", "0"))
                for coding in selection:
                    if _local(coding.tag) != "Coding":
                        continue
                    target = next((c.get("targetGUID", "") for c in coding
                                   if _local(c.tag) == "CodeRef"), "")  # fmt: skip
                    user = users.get(
                        coding.get("creatingUser") or selection.get("creatingUser", ""), "?"
                    )
                    code = code_of.get(target)
                    if code is None:
                        if target in code_names and code_names[target] != EVIDENCE_BRANCH:
                            if not code_names[target].endswith("(Evidenz)"):
                                unknown[code_names[target]] += 1
                        continue
                    coder = coders.setdefault(user, QdaCoder(user))
                    coder.codings += 1
                    for start, stop, unit in ranges:
                        if (overlap := min(end, stop) - max(begin, start)) >= MIN_OVERLAP:
                            coder.overlap[unit][code] += overlap
    if not found:
        raise ValueError(
            "Im QDA-Projekt stehen keine Texte aus diesem llmanno-Projekt (die Marker „[ID]“ "
            "fehlen). Gold lässt sich nur aus einem Projekt übernehmen, das aus dem "
            "REFI-QDA-Export von llmanno stammt."
        )
    return QdaImport(coders, unknown, len(found))


def default_coders(qda: QdaImport, project: Project) -> list[str]:
    """Neue menschliche Kodierende: nicht das LLM, keine schon vorhandene Gold-Spalte, keine
    Review-Person aus dem Export."""
    data = project.config.data
    existing = (set(data.mapping.gold) | {data.mapping.consensus}) if data else set()
    existing |= {name.removesuffix(SUFFIX) for name in existing if name}  # schon importiert
    reviews = tuple(f"({mode})" for mode in REVIEW_MODES.values())
    return [
        name
        for name, coder in qda.coders.items()
        if coder.overlap and name != LLM_USER and name not in existing
        and not name.endswith(reviews)
    ]  # fmt: skip


def add_qda_gold(
    project: Project, qda: QdaImport, coders: list[str], out: Path
) -> data_import.ImportReport:
    """Schreibt die Daten mit je einer Gold-Spalte „<Name> (QDA)“ nach `out` und übernimmt sie
    per Gold-Nachtrag. Gibt es die Spalte schon (zweiter Import), wird sie ersetzt. Zeilen
    bleiben an ihrem Platz, damit die Einheiten gleich bleiben. CSV-Projekte bekommen wieder
    CSV (als .tsv): Steuerzeichen im Text, die XLSX nicht kennt, bleiben so erhalten."""
    cfg = project.config
    data = cfg.data
    if data is None or project.data_path is None or not project.data_path.is_file():
        raise ValueError("Die Datenkopie des Projekts fehlt; Gold lässt sich nicht nachtragen.")
    if not coders:
        raise ValueError("Keine Kodierenden ausgewählt.")
    sheet = data_import.read_sheet(project.data_path, data.sheet)
    row_of = {u.unit_id: u.row for u in project.store.units()}
    multi = cfg.mode == "multi"
    header = list(sheet.header)
    by_column: dict[int, dict[int, str]] = {}  # Spaltenindex → Zeile → Gold
    added: list[str] = []
    for name in coders:
        column = f"{name}{SUFFIX}"
        if column not in header:
            header.append(column)
        labels = qda.coders[name].labels(multi, cfg.gold.separator)
        by_column[header.index(column)] = {
            row_of[u]: code for u, code in labels.items() if u in row_of
        }
        added.append(column)
    width = len(header)
    rows = {number: cells + [None] * (width - len(cells)) for number, cells in sheet.rows}
    for index, by_row in by_column.items():
        for number, cells in rows.items():
            cells[index] = by_row.get(number)

    out.parent.mkdir(parents=True, exist_ok=True)
    if data_import.is_csv(project.data_path):
        out = out.with_suffix(".tsv")
        _write_tsv(out, header, rows)
        sheet_name = data_import.CSV_SHEET
    else:
        sheet_name = _write_xlsx(out, data.sheet, header, rows)
    gold = [*data.mapping.gold, *(c for c in added if c not in data.mapping.gold)]
    return project.update_gold(out, sheet_name, gold, data.mapping.consensus)


def _write_xlsx(out: Path, name: str, header: list[str], rows: dict[int, list]) -> str:
    workbook = openpyxl.Workbook()
    target = workbook.active
    target.title = name[:31]
    for col, value in enumerate(header, start=1):
        target.cell(row=1, column=col, value=value)
    for number, cells in rows.items():
        for col, value in enumerate(cells, start=1):
            if value is None:
                continue
            cell = target.cell(row=number, column=col, value=value)
            if isinstance(value, str) and value.startswith("="):
                cell.data_type = "s"
    workbook.save(out)
    return target.title


def _write_tsv(out: Path, header: list[str], rows: dict[int, list]) -> None:
    """Gleiche Zeilennummern wie die Quelle: Lücken werden leere Datensätze."""
    with out.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(header)
        for number in range(2, max(rows, default=1) + 1):
            cells = rows.get(number)
            cells = cells or [None] * len(header)
            writer.writerow(["" if v is None else v for v in cells])
