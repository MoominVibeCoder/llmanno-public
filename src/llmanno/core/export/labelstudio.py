"""Export für Label Studio: Aufgaben (JSON) und die passende Oberfläche (Labeling Config, XML).

Je Einheit eine Aufgabe mit Zieltext und Kontext. Auf Wunsch stehen die LLM-Ergebnisse als
Vorschläge („predictions“) darin: die finalen Codes als Auswahl und die Signalwörter als Spans.
Vorschläge lenken das Urteil wie eine assistierte Review; für blinde Kodierung ohne sie
exportieren. Gold steht nicht im Export.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from xml.sax.saxutils import quoteattr

from llmanno.core.engine import prepare_prompts, results
from llmanno.core.i18n import t
from llmanno.core.project import Project
from llmanno.core.units import Item, Line
from llmanno.core.validation import hidden_test_items, note_test_exposure


@dataclass(frozen=True)
class LabelStudioSummary:
    tasks: int
    predictions: int
    config: Path


def _context(lines: tuple[Line, ...]) -> str:
    """Wie in REFI-QDA: „[ID] Sprecher: Text“ je Zeile."""
    speaker = [f"{line.speaker}: " if line.speaker else "" for line in lines]
    return "\n".join(f"[{ln.unit_id}] {s}{ln.text}" for ln, s in zip(lines, speaker, strict=True))


def _info(item: Item) -> str:
    """Was der Prompt außer dem Zieltext zeigt: ID und Sprecher je Zeile, Metadaten."""
    lines = [f"[{line.unit_id}] {line.speaker or ''}".rstrip() for line in item.lines]
    meta = "; ".join(f"{k}: {v}" for k, v in item.meta.items() if v not in (None, ""))
    return "\n".join([*lines, meta] if meta else lines)


def labeling_config(codes: list[str], multi: bool) -> str:
    choices = "\n".join(f"    <Choice value={quoteattr(c)}/>" for c in codes)
    labels = "\n".join(f"    <Label value={quoteattr(c)}/>" for c in codes)
    unit, before, target, after = (
        quoteattr(t("Einheit", "Unit")),
        quoteattr(t("Kontext davor", "Context before")),
        quoteattr(t("Zieltext", "Target text")),
        quoteattr(t("Kontext danach", "Context after")),
    )
    return (
        "<View>\n"
        f"  <Header value={unit}/>\n"
        '  <Text name="info" value="$info"/>\n'
        f"  <Header value={before}/>\n"
        '  <Text name="before" value="$before"/>\n'
        f"  <Header value={target}/>\n"
        '  <Text name="text" value="$text"/>\n'
        f"  <Header value={after}/>\n"
        '  <Text name="after" value="$after"/>\n'
        f'  <Choices name="code" toName="text" choice="{"multiple" if multi else "single"}">\n'
        f"{choices}\n"
        "  </Choices>\n"
        '  <Labels name="evidence" toName="text">\n'
        f"{labels}\n"
        "  </Labels>\n"
        "</View>\n"
    )


def export_labelstudio(
    project: Project, path: Path, *, predictions: bool = True, only_dev: bool = False
) -> LabelStudioSummary:
    """Schreibt die Aufgaben nach `path` und die Labeling Config daneben (gleicher Name, .xml).
    `only_dev`: vor dem Freeze ohne Vorschläge für Test-Einheiten (die Texte bleiben)."""
    cfg = project.config
    assert cfg.codebook is not None
    _, items = prepare_prompts(project)
    hidden = hidden_test_items(project) if only_dev else set()
    by_item = results(project, [i.item_id for i in items]) if predictions and cfg.panel else {}
    model = "llmanno " + version("llmanno") + ": " + ", ".join(m.key for m in cfg.panel)
    codes = cfg.codebook.allowed_ids()
    tasks, predicted = [], []
    for item in items:
        task: dict = {
            "data": {
                "unit": item.item_id,
                "text": item.target,
                "info": _info(item),
                "before": _context(item.before),
                "after": _context(item.after),
            }
        }
        result = by_item.get(item.item_id)
        if result is not None and result.status == "ok" and item.item_id not in hidden:
            final = [c for c in result.final if c in codes]
            scores = {s.code: s.p_sc for s in result.ranking}
            # Verschiedene Schreibweisen („Wort“, Wort.) landen an derselben Stelle: einmal.
            bounds = dict.fromkeys(
                (code, s.start, s.end)
                for code in final
                for s in result.evidence.get(code, [])
                if s.start is not None and s.end is not None
            )
            spans = [
                {
                    "from_name": "evidence",
                    "to_name": "text",
                    "type": "labels",
                    "value": {
                        "start": start,
                        "end": end,
                        "text": item.target[start:end],
                        "labels": [code],
                    },
                }
                for code, start, end in bounds
            ]
            choice = {
                "from_name": "code", "to_name": "text", "type": "choices",
                "value": {"choices": final},
            }  # fmt: skip
            task["predictions"] = [
                {
                    "model_version": model,
                    "score": min((scores.get(c, 0.0) for c in final), default=0.0),
                    "result": [choice, *spans],
                }
            ]
            predicted.append(item.item_id)
        tasks.append(task)
    path.write_text(json.dumps(tasks, ensure_ascii=False, indent=1), encoding="utf-8")
    config = path.with_suffix(".xml")
    config.write_text(labeling_config(codes, cfg.mode == "multi"), encoding="utf-8")
    note_test_exposure(project, "labelstudio", predicted)
    project.store.log(
        "exported", format="labelstudio", file=path.name, tasks=len(tasks),
        predictions=len(predicted),
    )  # fmt: skip
    return LabelStudioSummary(len(tasks), len(predicted), config)
