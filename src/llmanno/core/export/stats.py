"""Export für Statistiksoftware: SPSS (.sav), Stata (.dta), R (.rds).

Eine Zeile pro Einheit. Codes werden in SPSS und Stata als Zahlen mit Wertelabels abgelegt
(Wertelabels gibt es dort nur für numerische Variablen). In R stehen sie als Text, weil pyreadr
keine Faktoren schreibt; ein mitgeliefertes Skript macht daraus Faktoren in Codebuch-Reihenfolge.
Bei Multi-Label gibt es je Code eine 0/1-Variable. Originale Spaltennamen bleiben als
Variablenlabels erhalten.

Mit Review-Queue kommen die Spalten für das R-Paket `dsl` (Egami et al. 2023) dazu:
`dsl_labeled` (blind reviewt), `dsl_sample_prob` (Wahrscheinlichkeit, in die Queue zu kommen:
1 per Gate, sonst die der Zufallsstichprobe) und je Code `dsl_llm_<code>` und
`dsl_review_<code>` als 0/1 (Review fehlt bei nicht gelabelten Einheiten).
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Literal

import pandas as pd
import pyreadr
import pyreadstat

from llmanno.core.engine import prepare_prompts, results
from llmanno.core.examples import seen_example_ids
from llmanno.core.gold import build_gold
from llmanno.core.project import Project
from llmanno.core.review import RANDOM, review_status
from llmanno.core.validation import hidden_test_items, note_test_exposure

Format = Literal["sav", "dta", "rds"]
_NAME_LIMIT = {"sav": 64, "dta": 32, "rds": 10_000}
_TRANSLIT = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"}
)


def _variable_name(raw: str, taken: set[str], limit: int) -> str:
    """Gültiger Variablenname: Buchstabe am Anfang, nur Buchstaben, Ziffern und _."""
    ascii_name = unicodedata.normalize("NFKD", raw.strip().translate(_TRANSLIT))
    ascii_name = ascii_name.encode("ascii", "ignore").decode()
    name = re.sub(r"\W+", "_", ascii_name, flags=re.ASCII).strip("_") or "v"
    if not name[0].isalpha():
        name = f"v_{name}"
    name = name[:limit]
    candidate, n = name, 2
    while candidate.lower() in taken:
        suffix = f"_{n}"
        candidate, n = name[: limit - len(suffix)] + suffix, n + 1
    taken.add(candidate.lower())
    return candidate


def build_frame(
    project: Project, fmt: Format, *, only_dev: bool = False
) -> tuple[pd.DataFrame, dict[str, str], dict[str, dict]]:
    """DataFrame, Variablenlabels und Wertelabels für das Zielformat."""
    cfg = project.config
    assert cfg.codebook is not None
    _, items = prepare_prompts(project)
    hidden = hidden_test_items(project) if only_dev else set()
    items = [i for i in items if i.item_id not in hidden]
    units = {u.unit_id: u for u in project.store.units()}
    by_item = results(project, [i.item_id for i in items])
    multi = cfg.mode == "multi"
    codes = cfg.codebook.allowed_ids()
    labels_of = {c.id: c.label for c in cfg.codebook.codes}
    if cfg.codebook.abstain:
        labels_of[cfg.codebook.abstain_id] = cfg.codebook.abstain_label
    numbers = {code: i for i, code in enumerate(codes, start=1)}
    value_labels = {numbers[c]: f"{c}: {labels_of.get(c, '')}".rstrip(": ") for c in codes}
    gold = (
        build_gold(cfg, list(units.values()), items) if cfg.data and cfg.data.mapping.gold else None
    )

    limit = _NAME_LIMIT[fmt]
    taken: set[str] = set()
    columns: dict[str, list] = {}
    variable_labels: dict[str, str] = {}
    code_variables: list[str] = []

    def add(raw: str, values: list, label: str | None = None, code: bool = False) -> None:
        name = _variable_name(raw, taken, limit)
        columns[name] = values
        variable_labels[name] = label or raw
        if code:
            code_variables.append(name)

    def code_value(code: str | None) -> object:
        if code is None or code not in numbers:  # nicht mehr im Codebuch
            return None
        return code if fmt == "rds" else numbers[code]

    holistic = cfg.coding.units.mode == "holistic"
    add("item_id", [i.item_id for i in items], "Einheit")
    if not holistic:
        sources = [units[i.item_id].source for i in items]
        for column in list(sources[0]) if sources else []:
            add(column, [_plain(s.get(column)) for s in sources])

    item_results = [by_item[i.item_id] for i in items]
    add("llm_status", [r.status for r in item_results], "LLM: Status")
    if multi:
        for code in codes:
            add(
                f"llm_{code}",
                [int(code in r.final) if r.status == "ok" else None for r in item_results],
                f"LLM: {code} vergeben (0/1)",
            )
    else:
        add("llm_code", [code_value(r.final[0]) if r.final else None for r in item_results],
            "LLM: finaler Code", code=True)  # fmt: skip
        for rank in range(cfg.k):
            add(
                f"llm_code_{rank + 1}",
                [
                    code_value(r.ranking[rank].code) if len(r.ranking) > rank else None
                    for r in item_results
                ],
                f"LLM: Kandidat {rank + 1}",
                code=True,
            )
    top = [r.ranking[0] if r.ranking else None for r in item_results]
    add("llm_p_sc", [s.p_sc if s else None for s in top], "LLM: Self-Consistency-Konfidenz Top-1")
    add(
        "llm_p_verbal",
        [s.p_verbal if s else None for s in top],
        "LLM: verbalisierte Konfidenz Top-1",
    )
    add("llm_entropie", [r.entropy for r in item_results], "LLM: normalisierte Entropie")
    add("llm_flags", ["; ".join(r.flags) for r in item_results], "LLM: Flags")
    # Wer nur das Test-Set auswerten will, filtert hier statt von Hand zusammenzuführen.
    if parts := project.store.split():
        add("split", [parts.get(i.item_id) for i in items], "Dev/Test-Split")
    if shown := seen_example_ids(project):  # ihr Gold stand im Prompt
        add(
            "beispiel",
            [int(i.item_id in shown) for i in items],
            "Beispiel im Prompt (Few-Shot): aus Kennwerten ausschließen",
        )
    if reviewed := review_status(project):
        add(
            "review_status",
            [reviewed.get(i.item_id) for i in items],
            "Review: blind, assistiert oder offen (in der Queue)",
        )

    if gold is not None:
        for column in gold.columns:
            values = [gold.labels[column][i.item_id] for i in items]
            if multi:
                for code in codes:
                    dummies = [None if v is None else int(code in v) for v in values]
                    add(f"gold_{column}_{code}", dummies, f"Gold {column}: {code} (0/1)")
            else:
                add(f"gold_{column}", [code_value(v) for v in values], f"Gold: {column}", code=True)

    dsl_notes: list[str] = []
    if queue := {e.item_id: e for e in project.store.queue()}:
        dsl_notes = _dsl_columns(project, items, item_results, queue, codes, multi, add)

    frame = pd.DataFrame(columns)
    frame.attrs["dsl_notes"] = dsl_notes
    if fmt == "rds":
        # Für das R-Skript: welche Spalten Codes sind und in welcher Reihenfolge.
        return frame, variable_labels, {name: {"levels": codes} for name in code_variables}
    return frame, variable_labels, {name: value_labels for name in code_variables}


def _dsl_columns(project, items, item_results, queue, codes, multi, add) -> list[str]:
    """Spalten für dsl; liefert Hinweise, wenn die Annahmen von dsl verletzt sein könnten."""
    latest = {}
    for review in project.store.reviews():
        if review.mode == "blind" and review.item_id in queue:
            latest[review.item_id] = review
    random_only = [e.draw_probability for e in queue.values() if e.reasons == [RANDOM]]
    created = [e for e in project.store.audit() if e.event == "review_queue_created"]
    p_random = created[-1].details["p_random"] if created else (random_only or [0.0])[0]
    if not p_random:
        # Ohne Zufallsanteil haben nicht gewählte Einheiten Wahrscheinlichkeit 0: dsl braucht > 0.
        return ["Keine dsl-Spalten: Die Prüfliste hat keine Zufallsstichprobe (Anteil 0 %)."]

    def probability(item_id: str) -> float:
        # Stand der aktuellen Queue: Nach einem Neubau gehören frühere Ziehungen nicht mehr
        # zum Design (sonst verzerrte Gewichte, siehe review.weighted_reviews).
        return queue[item_id].draw_probability if item_id in queue else p_random

    notes = []
    if missing := len(set(queue) - set(latest)):
        notes.append(
            f"Achtung: {missing} von {len(queue)} Einheiten der Prüfliste (Queue) haben keine "
            "blinde Review. "
            "dsl setzt bekannte Labeling-Wahrscheinlichkeiten voraus; sie stimmen nur, wenn die "
            "Prüfliste vollständig abgearbeitet ist."
        )
    add(
        "dsl_labeled",
        [int(i.item_id in latest) for i in items],
        "dsl: blind reviewt (0/1)",
    )
    add(
        "dsl_sample_prob",
        [probability(i.item_id) for i in items],
        "dsl: Wahrscheinlichkeit, in die Prüfliste (Review-Queue) zu kommen",
    )
    for code in codes:
        add(
            f"dsl_llm_{code}",
            [
                (int(code in r.final) if multi else int(r.final[:1] == [code]))
                if r.status == "ok"
                else None
                for r in item_results
            ],
            f"dsl: LLM vergibt {code} (0/1)",
        )
        add(
            f"dsl_review_{code}",
            [int(code in latest[i.item_id].codes) if i.item_id in latest else None for i in items],
            f"dsl: Review vergibt {code} (0/1)",
        )
    return notes


def _plain(value: object) -> object:
    """Statistikformate kennen keine gemischten Spalten: alles außer Zahlen wird Text."""
    if value is None or isinstance(value, int | float) and not isinstance(value, bool):
        return value
    return str(value)


def export_stats(project: Project, path: Path, fmt: Format, *, only_dev: bool = False) -> None:
    frame, variable_labels, value_labels = build_frame(project, fmt, only_dev=only_dev)
    # Gemischte Spalten (Zahlen und Text) vereinheitlichen, sonst scheitern die Writer.
    for name in frame.columns:
        series = frame[name]
        if series.dtype == object and series.map(lambda v: isinstance(v, str)).any():
            frame[name] = series.map(lambda v: None if v is None else str(v))
    if fmt == "sav":
        pyreadstat.write_sav(
            frame, str(path), column_labels=variable_labels, variable_value_labels=value_labels
        )
    elif fmt == "dta":
        pyreadstat.write_dta(
            frame,
            str(path),
            column_labels={k: v[:80] for k, v in variable_labels.items()},  # Stata: max. 80 Zeichen
            variable_value_labels=value_labels,
        )
    else:
        pyreadr.write_rds(str(path), frame)
        levels = next(iter(value_labels.values()), {}).get("levels", [])
        script = _r_script(path.name, list(value_labels), levels)
        if "dsl_labeled" in frame.columns:
            script += _dsl_hint(frame)
        script += "".join(f"# {note}\n" for note in frame.attrs.get("dsl_notes", []))
        path.with_suffix(".R").write_text(script, "utf-8")
    note_test_exposure(project, fmt, [] if only_dev else None)  # nur Dev: kein Test darin
    project.store.log("exported", format=fmt, file=path.name, n_items=len(frame))


def _dsl_hint(frame: pd.DataFrame) -> str:
    code = next(c.removeprefix("dsl_review_") for c in frame.columns if c.startswith("dsl_review_"))
    # Beispiel-Einheiten: ihr Gold stand im Prompt, das LLM wirkte dort zu gut
    data = "subset(daten, beispiel == 0)" if "beispiel" in frame.columns else "daten"
    return (
        "\n# Design-based Supervised Learning (Egami et al. 2023): Anteil bzw. Regression mit den\n"
        "# blinden Reviews als Gold und den LLM-Labels als Vorhersage, gewichtet mit der\n"
        "# Ziehwahrscheinlichkeit. Beispiel für den Anteil von "
        f"{code}:\n"
        '# install.packages("dsl")  # bzw. remotes::install_github("naoki-egami/dsl")\n'
        "# library(dsl)\n"
        f'# fit <- dsl(model = "lm", formula = dsl_review_{code} ~ 1,\n'
        f'#            predicted_var = "dsl_review_{code}", prediction = "dsl_llm_{code}",\n'
        '#            sample_prob = "dsl_sample_prob", labeled = "dsl_labeled",\n'
        f"#            data = {data})\n"
        "# summary(fit)\n"
    )


def _r_script(file_name: str, code_columns: list[str], levels: list[str]) -> str:
    def vector(values: list[str]) -> str:
        return "c(" + ", ".join(f'"{v}"' for v in values) + ")"

    return (
        "# Erzeugt von llmanno: liest die Daten und macht aus den Code-Spalten Faktoren\n"
        "# in der Reihenfolge des Codebuchs.\n"
        f'daten <- readRDS("{file_name}")\n'
        f"stufen <- {vector(levels)}\n"
        f"for (spalte in {vector(code_columns)}) {{\n"
        "  daten[[spalte]] <- factor(daten[[spalte]], levels = stufen)\n"
        "}\n"
    )
