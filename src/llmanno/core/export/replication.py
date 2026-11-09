"""Replikationspaket (PLAN 3.13): alles, um die Kodierung nachzuvollziehen und neu zu rechnen.

Datenschutz-Schalter `include_texts`: Ohne Rohtexte enthält das Paket nur IDs, Codes und
Kennzahlen; Texte, Metadaten, Roh-Antworten, Begründungen, Signalwörter und Review-Notizen
bleiben draußen. Sprecher und Review-Personen stehen dann als Pseudonyme im Paket. Hashes
einzelner Texte oder Prompts fehlen, denn kurze Texte („Ja.“) ließen sich per Wörterbuch
erraten; die gemeinsame Prüfsumme aller Einheiten steht in project.json. Fehlermeldungen im
Protokoll verlieren den zitierten Antworttext des Anbieters. API-Schlüssel stehen nie im
Projekt und damit nie im Paket.
"""

from __future__ import annotations

import csv
import io
import json
import platform
import re
import zipfile
from dataclasses import replace
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, distributions, requires, version
from pathlib import Path
from typing import Any

from llmanno.core.aggregate import decision
from llmanno.core.demo import demo_note, is_demo
from llmanno.core.engine import prepare_prompts, results
from llmanno.core.evaluation import EvaluationReport
from llmanno.core.examples import seen_example_ids
from llmanno.core.export.evaluation import metric_rows
from llmanno.core.export.guide import guide_markdown
from llmanno.core.export.report import model_size, output_modes, run_models, server_note
from llmanno.core.history import codebook_history, unified_diff
from llmanno.core.i18n import output_language
from llmanno.core.project import CODEBOOK_FILE, CONFIG_FILE, Project
from llmanno.core.prompt import TEMPLATE_VERSION, render, render_system
from llmanno.core.pseudonyms import PSEUDONYM_FILE
from llmanno.core.store import Unit, sha256_text
from llmanno.core.validation import note_test_exposure

ROOT = "replikation"
# Metadaten eines Aufrufs, die ohne Rohtexte ins Paket dürfen. Alles andere (etwa die
# Thinking-Spur, die den Text zitiert) bleibt draußen, auch künftige Felder.
SAFE_META = {
    "model_returned", "tokens_in", "tokens_out", "latency_s", "duration_s", "finish_reason",
    "output_mode", "system_fingerprint",
}  # fmt: skip


# Fehlermeldungen der Anbieter zitieren deren Antwort, und die kann Prompt-Text enthalten.
_RESPONSE = re.compile(r"(HTTP \d{3}|Antwort ist kein JSON|Unerwartete Antwort): .*", re.S)


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return _RESPONSE.sub(r"\1 (Antworttext entfernt)", value)
    if isinstance(value, dict):
        return {k: _redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def _pseudonyms(values: list[Any], prefix: str) -> dict[Any, str]:
    """Wert → fortlaufendes Pseudonym in der Reihenfolge des ersten Auftretens."""
    out: dict[Any, str] = {}
    for value in values:
        if value is not None and value not in out:
            out[value] = f"{prefix}{len(out) + 1:02d}"
    return out


def id_pseudonyms(units: list[Unit], holistic: bool) -> tuple[dict, dict, dict]:
    """Einheiten-, Gruppen- und Item-ID → Pseudonym (E01, G01 …) in Zeilenreihenfolge.

    Der Rerun rechnet dieselbe Zuordnung aus den Originaldaten nach: gleiche Einheiten-Prüfsumme
    heißt gleiche Zeilen in gleicher Reihenfolge. Die Zuordnung selbst steht nie im Paket."""
    ordered = sorted(units, key=lambda u: u.row)
    unit_ids = _pseudonyms([u.unit_id for u in ordered], "E")
    groups = _pseudonyms([u.group for u in ordered], "G")
    return unit_ids, groups, groups if holistic else unit_ids


def _csv(rows: list[list[Any]]) -> str:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerows(rows)
    return buffer.getvalue()


def _jsonl(records: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in records)


def _environment(project: Project) -> str:
    conditions = [
        f"{r.fact.key}: Digest {r.fact.digest or '–'}, {model_size(r.fact)}"
        f"{server_note(r.fact)}, Ausgabeformat {output_modes(r.fact)}"
        for r in run_models(project)
        if r.fact
    ]
    packages = sorted(
        f"{d.metadata['Name']}=={d.version}" for d in distributions() if d.metadata["Name"]
    )
    # Die Laufzeit-Abhängigkeiten ausdrücklich: In einer gebündelten Exe fehlen sonst still
    # Pakete, deren Metadaten nicht mitkamen.
    runtime = []
    for requirement in requires("llmanno") or []:
        name = re.split(r"[ ;<>=!~\[]", requirement, maxsplit=1)[0]
        try:
            runtime.append(f"{name}=={version(name)}")
        except PackageNotFoundError:
            runtime.append(f"{name}: Version nicht ermittelbar")
    return "\n".join(
        [
            f"llmanno {version('llmanno')}",
            f"Prompt-Template {TEMPLATE_VERSION}",
            f"Python {platform.python_version()} ({platform.python_implementation()})",
            f"System {platform.platform()}",
            f"Erstellt {datetime.now(UTC).isoformat(timespec='seconds')}",
            "",
            "Messbedingungen der Läufe:",
            *(conditions or ["–"]),
            "",
            "Laufzeit-Abhängigkeiten:",
            *runtime,
            "",
            "Alle Pakete:",
            *packages,
            "",
        ]
    )


def _readme(project: Project, include_texts: bool, pseudonymize_ids: bool) -> str:
    cfg = project.config
    freeze = cfg.freeze.instrument_hash if cfg.freeze else "nicht eingefroren"
    ids = (
        "Einheiten- und Gruppen-IDs ebenfalls als Pseudonyme (E01, G01 …); der Rerun stellt die "
        "Zuordnung aus den Originaldaten wieder her."
        if pseudonymize_ids
        else "Einheiten- und Gruppen-IDs stehen unverändert im Paket; sie sollten keine Namen "
        "enthalten."
    )
    texts = (
        "**enthalten**: Daten, Prompts, Roh-Antworten, Begründungen und Signalwörter."
        if include_texts
        else "**nicht enthalten**: nur IDs, Codes und Kennzahlen; Sprecher und Review-Personen als "
        "Pseudonyme (S01, R01 …), keine Hashes einzelner Texte. Für einen Rerun die Originaldaten "
        f"getrennt bereitstellen (`--data`). {ids}"
    )
    data = f" --data {cfg.data.source_name}" if cfg.data and not include_texts else ""
    if cfg.coding.pseudonyms_sha256 and not include_texts:
        data += f" --pseudonyme {PSEUDONYM_FILE}"
        texts += (
            " Namen wurden vor dem Senden ersetzt; die Namensliste fehlt im Paket und "
            f"braucht der Rerun aus dem Projektordner (`--pseudonyme {PSEUDONYM_FILE}`)."
        )
    prompts = "System-Nachricht des ersten Laufs" + (
        ", Beispiel einer User-Nachricht" if include_texts else ""
    )
    return f"""# Replikationspaket: {cfg.name}

Erstellt mit llmanno {version("llmanno")}, Prompt-Template Version {TEMPLATE_VERSION}.
Instrument-Hash: `{freeze}`

Rohtexte: {texts}

## Inhalt

| Datei | Inhalt |
|---|---|
| `projekt/{CONFIG_FILE}` | vollständige Konfiguration (Codes, Modelle, Parameter, Split, Freeze) |
| `projekt/{CODEBOOK_FILE}` | Codebuch wörtlich, wie es im Prompt steht |
| `prompts/` | {prompts} |
| `codebook_history/` | alle geladenen Fassungen des Codebuchs und ihre Unterschiede |
| `codes.csv` | Code-Liste |
| `paket.json` | Rohtexte ja/nein, IDs pseudonym ja/nein (liest der Rerun) |
| `units.csv` | Einheiten mit Gruppe, Reihenfolge, Sprecher (ohne Rohtexte pseudonym) und Gold |
| `calls.jsonl` | jeder Aufruf: Parameter, Seed, Status, Entscheidung, Konfidenzen, Token, Latenz |
| `results.csv` | finales Ergebnis je Einheit |
| `audit.jsonl` | Projektprotokoll (Import, Läufe, Freeze, Test-Auswertungen, Exporte) |
| `metrics.json` | Kennwerte der zuletzt berechneten Auswertung, falls vorhanden |
| `guide_llm.md` | vorausgefüllte GUIDE-LLM-Checkliste |
| `environment.txt` | Software-Versionen |

## Neu rechnen

```
llmanno-rerun {ROOT}.zip --out rerun{data}
```

Der Rerun legt ein neues Projekt an, kodiert mit denselben Einstellungen und Seeds und
vergleicht jede Entscheidung mit `calls.jsonl`. Abweichende Modell-Digests werden gemeldet:
Ein anderes Modell-Build kann bei gleichem Namen anders antworten. Externe Anbieter brauchen
`--consent`.

Begründungen des Modells sind Plausibilisierungen, keine Erklärungen seiner Entscheidung.
"""


def _readme_en(project: Project, include_texts: bool, pseudonymize_ids: bool) -> str:
    cfg = project.config
    freeze = cfg.freeze.instrument_hash if cfg.freeze else "not frozen"
    ids = (
        "Unit and group IDs are pseudonymized as well (E01, G01 …); the rerun restores the "
        "mapping from the original data."
        if pseudonymize_ids
        else "Unit and group IDs are included unchanged; they should not contain names."
    )
    texts = (
        "**included**: data, prompts, raw responses, rationales and signal words."
        if include_texts
        else "**not included**: only IDs, codes and figures; speakers and reviewers as "
        "pseudonyms (S01, R01 …), no hashes of individual texts. For a rerun, provide the "
        f"original data separately (`--data`). {ids}"
    )
    data = f" --data {cfg.data.source_name}" if cfg.data and not include_texts else ""
    if cfg.coding.pseudonyms_sha256 and not include_texts:
        data += f" --pseudonyme {PSEUDONYM_FILE}"
        texts += (
            " Names were replaced before sending; the list of names is not in the package, "
            f"the rerun needs it from the project folder (`--pseudonyme {PSEUDONYM_FILE}`)."
        )
    prompts = "system message of the first run" + (
        ", example user message" if include_texts else ""
    )
    return f"""# Replication package: {cfg.name}

Created with llmanno {version("llmanno")}, prompt template version {TEMPLATE_VERSION}.
Instrument hash: `{freeze}`

Raw texts: {texts}

## Contents

| File | Content |
|---|---|
| `projekt/{CONFIG_FILE}` | complete configuration (codes, models, parameters, split, freeze) |
| `projekt/{CODEBOOK_FILE}` | codebook verbatim, as it appears in the prompt |
| `prompts/` | {prompts} |
| `codebook_history/` | all loaded versions of the codebook and their differences |
| `codes.csv` | code list |
| `paket.json` | raw texts yes/no, pseudonymized IDs yes/no (read by the rerun) |
| `units.csv` | units with group, order, speaker (pseudonymized without raw texts) and gold |
| `calls.jsonl` | every call: parameters, seed, status, decision, confidences, tokens, latency |
| `results.csv` | final result per unit |
| `audit.jsonl` | project log (import, runs, freeze, test evaluations, exports) |
| `metrics.json` | metrics of the most recent evaluation, if available |
| `guide_llm.md` | prefilled GUIDE-LLM reporting checklist |
| `environment.txt` | software versions |

Column names in the CSV files are German (e.g. `gruppe` = group, `reihenfolge` = order,
`sprecher` = speaker, `zeile` = row, `begruendung` = rationale, `signalwoerter` = signal words).

## Recode

```
llmanno-rerun {ROOT}.zip --out rerun{data}
```

The rerun creates a new project, codes with the same settings and seeds and compares every
decision with `calls.jsonl`. Different model digests are reported: another build of a model
may answer differently under the same name. External providers require `--consent`.

Model rationales are post-hoc plausibilizations, not explanations of the model's decision.
"""


def _demo_banner(project: Project, lang: str) -> str:
    if not is_demo(project):
        return ""
    with output_language(lang):
        return f"> **{demo_note()}**\n\n"


def _safe_meta(meta: dict[str, Any]) -> dict[str, Any]:
    safe = {k: v for k, v in meta.items() if k in SAFE_META}
    if isinstance(meta.get("repair"), dict):
        safe["repair"] = _safe_meta(meta["repair"])
    return safe


def _call_record(call, include_texts: bool, mode: str, system_sha: bool) -> dict[str, Any]:
    """`system_sha`: Ohne Rohtexte fehlt er, sobald Beispiele aus den Daten im Prompt standen;
    er deckte sonst deren Texte ab."""
    record: dict[str, Any] = {
        "item_id": call.item_id,
        "model_key": call.model_key,
        "run": call.run,
        "config_hash": call.config_hash,
        "seed": call.seed,
        "status": call.status,
        "flags": call.flags,
        "request": call.request,
        "meta": call.meta if include_texts else _safe_meta(call.meta),
        "ts": call.ts.isoformat(),
        "system_sha": call.system_sha if system_sha else None,
        # Der User-Prompt eines kurzen Texts ließe sich nachbauen und am Hash erkennen.
        "user_sha": call.user_sha if include_texts else None,
        "decision": decision(call.answer, mode) if call.answer else None,
        "candidates": [
            {"code": c["code"], "confidence": c["confidence"]}
            for c in (call.answer or {}).get("candidates", [])
        ],
    }
    if include_texts:
        record.update(answer=call.answer, raw=call.raw, error=call.error)
    return record


def export_replication(
    project: Project,
    path: Path,
    *,
    include_texts: bool = False,
    pseudonymize_ids: bool = False,
    report: EvaluationReport | None = None,
    lang: str = "de",
) -> None:
    """`lang`: Sprache von README.md und guide_llm.md; die Daten selbst sind sprachneutral."""
    if include_texts and pseudonymize_ids:
        raise ValueError(
            "IDs lassen sich nur ohne Rohtexte pseudonymisieren; die Daten tragen die echten."
        )
    cfg = project.config
    assert cfg.data is not None and cfg.codebook is not None
    spec, items = prepare_prompts(project)
    units = project.store.units()
    calls = project.store.calls()
    reviews = project.store.reviews()
    audit = [{"ts": e.ts.isoformat(), "event": e.event, **e.details} for e in project.store.audit()]
    persons = _pseudonyms([r.reviewer for r in reviews] + [e.get("reviewer") for e in audit], "R")
    unit_alias, group_alias, item_alias = (
        id_pseudonyms(units, cfg.coding.units.mode == "holistic")
        if pseudonymize_ids
        else ({}, {}, {})
    )

    others: dict[str, str] = {}

    def item(item_id: str) -> str:
        """Pseudonym der aktuellen Einheitenart; Aufrufe aus einem früheren Modus (Einheit
        statt Gruppe oder umgekehrt) bekommen ihres aus der anderen Liste, nie die echte ID."""
        if not pseudonymize_ids:
            return item_id
        known = item_alias.get(item_id) or unit_alias.get(item_id) or group_alias.get(item_id)
        return known or others.setdefault(item_id, f"X{len(others) + 1:02d}")

    def with_aliases(value: Any) -> Any:
        """Jede item_id, auch verschachtelt (Beispiele in der Lauf-Konfiguration)."""
        if isinstance(value, dict):
            return {
                k: item(v) if k == "item_id" and isinstance(v, str) else with_aliases(v)
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [with_aliases(v) for v in value]
        return value

    audit = [with_aliases(e) for e in audit]
    keep_system = include_texts or not seen_example_ids(project)
    packed = cfg
    if pseudonymize_ids and cfg.coding.examples:  # Beispiel-IDs wie alle IDs im Paket
        examples = [e.model_copy(update={"item_id": item(e.item_id)}) for e in cfg.coding.examples]
        packed = cfg.model_copy(
            update={"coding": cfg.coding.model_copy(update={"examples": examples})}
        )
        spec = replace(
            spec, examples=tuple((replace(i, item_id=item(i.item_id)), c) for i, c in spec.examples)
        )
    if not include_texts:
        audit = [
            _redact({**e, "reviewer": persons.get(e["reviewer"])} if "reviewer" in e else e)
            for e in audit
        ]
    files: dict[str, str | bytes] = {
        "README.md": _demo_banner(project, lang)
        + (_readme_en if lang == "en" else _readme)(project, include_texts, pseudonymize_ids),
        "paket.json": json.dumps(
            {"rohtexte": include_texts, "ids_pseudonym": pseudonymize_ids}, indent=2
        ),
        f"projekt/{CONFIG_FILE}": packed.model_dump_json(indent=2),
        f"projekt/{CODEBOOK_FILE}": project.codebook_text or "",
        # Beispiele aus Dev sind Daten: ihre Texte nur mit Rohtexten
        "prompts/system_lauf0.txt": render_system(
            spec, cfg.coding.base_seed, example_texts=include_texts and not spec.names_missing
        ),
        "environment.txt": _environment(project),
        "guide_llm.md": guide_markdown(project, report, lang),
        "audit.jsonl": _jsonl(audit),
        "calls.jsonl": _jsonl(
            [
                {
                    **_call_record(c, include_texts, cfg.mode, keep_system),
                    "item_id": item(c.item_id),
                }
                for c in calls
            ]
        ),
    }
    codes = [["code", "label", "rang", "aktiv"]] + [
        [c.id, c.label or "", c.rank, c.active] for c in cfg.codebook.codes
    ]
    if cfg.codebook.abstain:
        codes.append([cfg.codebook.abstain_id, cfg.codebook.abstain_label, None, True])
    files["codes.csv"] = _csv(codes)

    gold_columns = [*cfg.data.mapping.gold, *filter(None, [cfg.data.mapping.consensus])]
    # Metadaten und Analyse-Variablen können Personenmerkmale sein: nur mit Rohtexten.
    meta_columns = cfg.data.mapping.meta if include_texts else []
    analysis_columns = cfg.data.mapping.analysis if include_texts else []
    speakers = _pseudonyms([u.speaker for u in units], "S")
    header = ["unit_id", "zeile", "gruppe", "reihenfolge"]
    header += (
        ["text_sha256", "sprecher", "text", *meta_columns, *analysis_columns]
        if include_texts
        else ["sprecher"]
    )
    rows: list[list[Any]] = [header + [f"gold_{c}" for c in gold_columns]]
    for u in units:
        row = [unit_alias.get(u.unit_id, u.unit_id), u.row, group_alias.get(u.group, u.group)]
        row.append(u.order)
        if include_texts:
            row += [sha256_text(u.text), u.speaker, u.text, *(u.meta.get(c) for c in meta_columns)]
            row += [u.source.get(c) for c in analysis_columns]
        else:
            row.append(speakers.get(u.speaker))
        rows.append(row + [u.gold.get(c) for c in gold_columns])
    files["units.csv"] = _csv(rows)

    by_item = results(project, [i.item_id for i in items])
    parts = project.store.split()
    header = ["item_id", "split", "status", "final", "codes_topk", "p_sc", "p_verbal", "flags"]
    rows = [header + (["begruendung", "signalwoerter"] if include_texts else [])]
    for entry in items:
        r = by_item[entry.item_id]
        row = [
            item(entry.item_id),
            parts.get(entry.item_id),
            r.status,
            ";".join(r.final),
            ";".join(s.code for s in r.ranking),
            ";".join(f"{s.p_sc:.4f}" for s in r.ranking),
            ";".join(f"{s.p_verbal:.4f}" for s in r.ranking),
            ";".join(r.flags),
        ]
        if include_texts:
            evidence = "; ".join(
                f"{code}: {', '.join(s.text for s in spans)}" for code, spans in r.evidence.items()
            )
            row += [r.rationale, evidence]
        rows.append(row)
    files["results.csv"] = _csv(rows)

    if reviews:
        header = ["item_id", "person", "modus", "codes", "queue_gruende", "ziehwahrscheinlichkeit"]
        rows = [header + ["zeit"] + (["notiz"] if include_texts else [])]
        for r in reviews:
            person = r.reviewer if include_texts else persons[r.reviewer]
            row = [item(r.item_id), person, r.mode, ";".join(r.codes), ";".join(r.reasons)]
            row += [r.draw_probability, r.ts.isoformat()]
            rows.append(row + ([r.note] if include_texts else []))
        files["reviews.csv"] = _csv(rows)

    if report is not None:
        files["metrics.json"] = json.dumps(
            {
                "subset": report.subset,
                "instrument_hash": report.instrument_hash,
                "freeze_intact": report.freeze_intact,
                "test_evaluations": report.test_evaluations,
                "resamples": report.resamples,
                "seed": report.seed,
                "warnings": report.warnings,
                "rows": [
                    dict(zip(["bereich", "vergleich", "kennwert", "wert", "ki_unten", "ki_oben",
                              "n"], row, strict=True))
                    for row in metric_rows(report)
                ],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )  # fmt: skip

    # Das Codebuch ist kein Datenschutzfall: Fassungen und Unterschiede immer dabei.
    versions = codebook_history(project)
    for before, current in zip([None, *versions], versions, strict=False):
        if current.text is not None:
            name = f"codebook_history/v{current.number:02d}_{current.sha256[:12]}.txt"
            files[name] = current.text
        if before is not None and (diff := unified_diff(before, current)):
            files[f"codebook_history/v{current.number:02d}.diff"] = diff

    if include_texts and not spec.names_missing:
        files["prompts/beispiel_user.txt"] = render(spec, items[0], cfg.coding.base_seed).user
    if include_texts:
        shas = {c.system_sha for c in calls} | {c.user_sha for c in calls}
        files["texts.jsonl"] = _jsonl(
            [{"sha256": sha, "text": project.store.get_text(sha)} for sha in sorted(shas)]
        )
        data = project.data_path
        if data is not None and data.is_file():
            files[f"projekt/{data.name}"] = data.read_bytes()
        names = project.folder / PSEUDONYM_FILE  # Klarnamen: nur mit den Rohtexten
        if cfg.coding.pseudonyms_sha256 and names.is_file():
            files[f"projekt/{PSEUDONYM_FILE}"] = names.read_bytes()

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            data_bytes = content.encode("utf-8") if isinstance(content, str) else content
            archive.writestr(f"{ROOT}/{name}", data_bytes)
    note_test_exposure(project, "replikationspaket")
    project.store.log(
        "exported",
        format="replikationspaket",
        file=path.name,
        include_texts=include_texts,
        pseudonymize_ids=pseudonymize_ids,
        lang=lang,
        n_calls=len(calls),
        with_metrics=report is not None,
    )
