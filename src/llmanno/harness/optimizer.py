"""Ein Optimierer-Schritt: vorschlagen, prüfen, erproben, annehmen.

Der Optimierer (Opus) sieht die Fehler eines Kodierlaufs auf Dev-Train samt Gold und schlägt je
Dimension eine neue Fassung vor. Eine Prüfung ohne Modell verwirft Vorschläge, die Codes
verlieren oder Text aus Dev-Val/Prüfteil übernehmen. Sonnet erprobt jeden Vorschlag auf
Dev-Train (Kipp-Tabelle), der beste geht auf Dev-Val; angenommen wird nur über einer festen
Marge gegen die beste bisherige Fassung (Ladder). Jeder Schritt landet in der Bibliothek.
Tragen mindestens zwei Vorschläge, führt ein Komponist sie zusammen; danach urteilt eine
Reflexion über jede Änderung und hält höchstens eine Lehre fest (wie Harness²).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from scipy.stats import binomtest

from llmanno.core.providers.base import ChatRequest, Provider
from llmanno.harness.runtime import COLUMNS, Columns, Passage, chat
from llmanno.harness.tree import FILES, Harness, diff

NGRAM = 8  # so viele Wörter am Stück aus Dev-Val/Prüfteil gelten als übernommen
MAX_GROWTH = 2.0  # immer geladener Text darf höchstens doppelt so lang werden wie in B0
MIN_TRANSCRIPTS = 2  # Stützregel: eine Verwechslung muss in so vielen Transkripten vorkommen
# Ein Kandidat geht nur auf Dev-Val, wenn falsch→richtig richtig→falsch signifikant überwiegt
# (einseitiger Vorzeichentest). Probe 2026-10-28: Schritt 3 hatte 36:31, also Rauschen, und
# hätte sonst einen Blick auf Dev-Val verbraucht.
SIGN_ALPHA = 0.05


def beats_noise(flips: Mapping[str, int], alpha: float = SIGN_ALPHA) -> bool:
    fixed, broken = flips.get("fixed", 0), flips.get("broken", 0)
    if fixed <= broken:
        return False
    return binomtest(fixed, fixed + broken, 0.5, alternative="greater").pvalue < alpha


@dataclass(frozen=True)
class Condition:
    name: str
    editable: tuple[str, ...]
    dimensions: tuple[str, ...]


B2 = Condition("B2", ("codebook.md",), ("codebuch",))
B3 = Condition("B3", FILES, ("ablauf", "codebuch"))

DIMENSION_TEXT = {
    "ablauf": (
        "Your dimension is PROCEDURE: how the coder works through a line. Change the "
        "instructions in system.md and procedural notes (order of checks, what context to "
        "consult, defaults when no criterion is met, self-checks before rare codes). Do not "
        "rewrite code definitions; a parallel proposal owns them."
    ),
    "codebuch": (
        "Your dimension is CODEBOOK KNOWLEDGE: what a trained human coder knows. Sharpen "
        "definitions, boundary rules between confused codes, precedence and exclusion rules, "
        "and annotation conventions. Do not change procedure; a parallel proposal owns it."
    ),
}

PROPOSE_SYSTEM = """\
You improve the instructions ("harness") that an LLM uses to code teacher talk moves in math \
lesson transcripts. The goal is agreement with trained human coders. You see the current \
harness files and the coder's errors against the human gold standard on development data.

Rules:
- Write general rules that a human coder would recognise, never facts about single cases. \
Every rule must be supported by at least two error cases from at least two different transcripts.
- Keep every code label and its meaning. You may clarify, operationalise, add boundary and \
precedence rules; you may not redefine what a code measures.
- Do not quote the cases at length; short schematic examples are fine.
- Keep the files concise: every word is sent with every request. If you add, cut elsewhere.
- Making no change is a valid answer when the errors do not support one: return no files.
- Only edit the files you are allowed to edit. Return each changed file in full.
For every change write a note: file, what changed, which confusions it targets written as \
GOLD -> CODED with the code labels (for example NONE -> REVOICE), and why."""


def proposal_schema(editable: Sequence[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "files": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "enum": list(editable)},
                        "content": {"type": "string"},
                    },
                    "required": ["name", "content"],
                    "additionalProperties": False,
                },
            },
            "notes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string"},
                        "change": {"type": "string"},
                        "target": {"type": "string"},
                        "rationale": {"type": "string"},
                    },
                    "required": ["file", "change", "target", "rationale"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["files", "notes"],
        "additionalProperties": False,
    }


def evidence(
    passages: Sequence[Passage],
    pred: Mapping[str, str | None],
    gold: Mapping[str, str],
    *,
    max_cases: int = 80,
    context: int = 3,
    cols: Columns = COLUMNS,
) -> str:
    """Fehlerbild für den Optimierer: Häufigkeiten, Verwechslungen, Fälle mit Kontext."""
    ids = [i for p in passages for i in p.targets]
    gold_n, pred_n = Counter(gold[i] for i in ids), Counter(pred.get(i) for i in ids)
    pairs = Counter((gold[i], pred.get(i)) for i in ids if pred.get(i) != gold[i])
    out = ["# Code frequencies (gold vs coded)"]
    out += [f"- {c}: gold {gold_n[c]}, coded {pred_n.get(c, 0)}" for c in sorted(gold_n)]
    out += ["", "# Confusions (gold -> coded: count)"]
    out += [f"- {g} -> {p}: {n}" for (g, p), n in pairs.most_common()]
    out += ["", "# Error cases (context, then the coded line)"]
    # Fälle reihum über die Verwechslungen, damit seltene Paare nicht verdrängt werden
    queues = {pair: [] for pair in pairs}
    for p in passages:
        lines = p.lines.reset_index(drop=True)
        for i in p.targets:
            if pred.get(i) != gold[i]:
                k = int(lines.index[lines[cols.id] == i][0])
                window = lines.iloc[max(0, k - context) : k + 1]
                text = "\n".join(f"  {r[cols.role]}: {r[cols.text]}" for _, r in window.iterrows())
                queues[(gold[i], pred.get(i))].append(
                    f"[{p.name}] gold {gold[i]}, coded {pred.get(i)}\n{text}"
                )
    cases: list[str] = []
    while len(cases) < max_cases and any(queues.values()):
        for pair in [pq for pq, _ in pairs.most_common()]:
            if queues[pair] and len(cases) < max_cases:
                cases.append(queues[pair].pop(0))
    return "\n".join(out + cases)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def _group(passage_name: str) -> str:
    return passage_name.rsplit(" #", 1)[0]


def support(
    passages: Sequence[Passage], pred: Mapping[str, str | None], gold: Mapping[str, str]
) -> dict[tuple[str, str | None], set[str]]:
    """In welchen Transkripten jede Verwechslung (gold, kodiert) vorkommt; für die Stützregel."""
    out: dict[tuple[str, str | None], set[str]] = {}
    for p in passages:
        for i in p.targets:
            if pred.get(i) != gold[i]:
                out.setdefault((gold[i], pred.get(i)), set()).add(_group(p.name))
    return out


_PAIR = re.compile(r"([A-Z][A-Z_]+)\s*->\s*([A-Z][A-Z_]+)")


def check(
    new: Harness,
    base: Harness,
    codes: Sequence[str],
    forbidden: Sequence[str],
    *,
    notes: Sequence[Mapping[str, str]] = (),
    support_map: Mapping[tuple[str, str | None], set[str]] | None = None,
) -> list[str]:
    """Prüfung ohne Modell; jede gefundene Verletzung als Satz für den zweiten Versuch."""
    problems = []
    book = new.files.get("codebook.md", "")
    missing = [c for c in codes if c not in book]
    if missing:
        problems.append(f"codebook.md must keep every code label; missing: {', '.join(missing)}")
    size = lambda h: sum(len(h.files.get(n, "")) for n in FILES)  # noqa: E731
    if size(new) > MAX_GROWTH * size(base):
        problems.append(
            f"The files are {size(new)} characters long; "
            f"the limit is {int(MAX_GROWTH * size(base))}."
        )
    grams = set()
    for text in forbidden:
        w = _words(text)
        grams |= {tuple(w[k : k + NGRAM]) for k in range(len(w) - NGRAM + 1)}
    for name, text in new.files.items():
        w = _words(text)
        if any(tuple(w[k : k + NGRAM]) in grams for k in range(len(w) - NGRAM + 1)):
            problems.append(f"{name} quotes held-out transcript text; write general rules instead.")
    # Stützregel: jede Änderung zielt auf eine Verwechslung aus mindestens zwei Transkripten
    if support_map is not None:
        for note in notes:
            pairs = _PAIR.findall(note.get("target", ""))
            if not any(len(support_map.get(pair, ())) >= MIN_TRANSCRIPTS for pair in pairs):
                problems.append(
                    f"The change '{note.get('change', '')[:80]}' must target a confusion written "
                    f"as GOLD -> CODED that occurs in at least {MIN_TRANSCRIPTS} transcripts."
                )
    return problems


def flips(
    old: Mapping[str, str | None], new: Mapping[str, str | None], gold: Mapping[str, str]
) -> dict[str, int]:
    """Kipp-Tabelle: falsch→richtig (fixed), richtig→falsch (broken), sonst geändert."""
    fixed = sum(old[i] != gold[i] and new[i] == gold[i] for i in gold)
    broken = sum(old[i] == gold[i] and new[i] != gold[i] for i in gold)
    changed = sum(old[i] != new[i] for i in gold)
    return {"fixed": fixed, "broken": broken, "other": changed - fixed - broken}


def flip_pairs(
    old: Mapping[str, str | None], new: Mapping[str, str | None], gold: Mapping[str, str]
) -> dict[str, int]:
    """Welche Verwechslungen ein Kandidat behebt bzw. neu erzeugt, die häufigsten zuerst."""
    pairs: Counter[str] = Counter()
    for i in gold:
        if old[i] != gold[i] and new[i] == gold[i]:
            pairs[f"fixed {gold[i]} -> {old[i]}"] += 1
        elif old[i] == gold[i] and new[i] != gold[i]:
            pairs[f"broken {gold[i]} -> {new[i]}"] += 1
    return dict(pairs.most_common(12))


@dataclass
class Candidate:
    dimension: str
    harness: Harness | None
    notes: list[dict] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    flips: dict[str, int] = field(default_factory=dict)
    flip_pairs: dict[str, int] = field(default_factory=dict)
    pred: dict[str, str | None] = field(default_factory=dict, repr=False)
    val_kappa: float | None = None

    @property
    def net(self) -> int:
        return self.flips.get("fixed", 0) - self.flips.get("broken", 0)


def _files_block(current: Harness, editable: Sequence[str]) -> str:
    return "\n\n".join(
        f'<file name="{n}" editable="{n in editable}">\n' + current.files.get(n, "") + "\n</file>"
        for n in FILES
    )


def _ask(
    provider: Provider,
    model: str,
    system: str,
    task: str,
    current: Harness,
    condition: Condition,
    dimension: str,
    *,
    codes: Sequence[str],
    forbidden: Sequence[str],
    base: Harness,
    support_map: Mapping[tuple[str, str | None], set[str]],
    effort: str,
) -> Candidate:
    """Eine Fassung erfragen; verwirft die Prüfung sie, gibt es einen zweiten Versuch."""
    feedback = ""
    for _ in range(2):
        response = chat(
            provider,
            ChatRequest(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": task + feedback},
                ],
                temperature=None,
                max_tokens=32000,
                schema=proposal_schema(condition.editable),
                reasoning_effort=effort,
            ),
        )
        data = json.loads(response.text)
        changes = {f["name"]: f["content"] for f in data["files"]}
        if not changes:
            return Candidate(dimension, None, data["notes"])
        new = Harness(current.files | changes, current.config, parent=current.sha)
        problems = check(new, base, codes, forbidden, notes=data["notes"], support_map=support_map)
        if not problems:
            return Candidate(dimension, new, data["notes"])
        feedback = "\n\n# Your previous proposal was rejected\n" + "\n".join(problems)
    return Candidate(dimension, None, data["notes"], problems)


def propose(
    provider: Provider,
    model: str,
    current: Harness,
    condition: Condition,
    dimension: str,
    evidence_text: str,
    library: str,
    *,
    codes: Sequence[str],
    forbidden: Sequence[str],
    base: Harness,
    support_map: Mapping[tuple[str, str | None], set[str]],
    effort: str = "high",
) -> Candidate:
    task = (
        f"{DIMENSION_TEXT[dimension]}\n\nYou may edit: {', '.join(condition.editable)}.\n\n"
        f"# Current harness\n{_files_block(current, condition.editable)}\n\n"
        f"# Earlier steps\n{library or '(none)'}\n\n"
        f"# Errors on development data\n{evidence_text}"
    )
    return _ask(
        provider, model, PROPOSE_SYSTEM, task, current, condition, dimension,
        codes=codes, forbidden=forbidden, base=base, support_map=support_map, effort=effort,
    )  # fmt: skip


COMPOSE_SYSTEM = (
    PROPOSE_SYSTEM
    + """

You now COMPOSE: several candidate edits of the same harness were each tried on the development \
data. For each you see its diff, its notes and its flips (fixed = wrong before, right after; \
broken = right before, wrong after; per confusion). Build one harness that keeps the edits whose \
flips show they helped, drops or narrows edits that broke more than they fixed, and resolves \
conflicts between candidates. Do not add new rules that no candidate tried."""
)


def compose(
    provider: Provider,
    model: str,
    current: Harness,
    condition: Condition,
    tried: Sequence[Candidate],
    *,
    codes: Sequence[str],
    forbidden: Sequence[str],
    base: Harness,
    support_map: Mapping[tuple[str, str | None], set[str]],
    effort: str = "high",
) -> Candidate:
    parts = [
        f"## Candidate {c.dimension}\nflips: {json.dumps(c.flips)}\n"
        f"flips by confusion: {json.dumps(c.flip_pairs)}\nnotes: {json.dumps(c.notes)}\n"
        f"diff:\n{diff(current, c.harness)}"
        for c in tried
    ]
    task = (
        f"You may edit: {', '.join(condition.editable)}.\n\n"
        f"# Current harness\n{_files_block(current, condition.editable)}\n\n"
        "# Candidates tried on the development data\n" + "\n\n".join(parts)
    )
    return _ask(
        provider, model, COMPOSE_SYSTEM, task, current, condition, "zusammen",
        codes=codes, forbidden=forbidden, base=base, support_map=support_map, effort=effort,
    )  # fmt: skip


REFLECT_SYSTEM = """\
You read the record of one improvement step of a coding harness for teacher talk moves: the \
candidate edits, their notes, and their flips on development data (fixed / broken, by \
confusion). Write a verdict for every change: helped, hurt, no observed effect, or confounded. \
Prefer confounded when a candidate changed several things at once and the flips cannot be \
attributed to one of them; an invented attribution misleads every later step. Then write at \
most one durable lesson for later steps: the cue (the kind of confusion it applies to), the \
lesson (an operation on the harness, naming the file), the evidence (which flips support it), \
and your confidence. If the record supports no lesson, set lesson to null."""

REFLECT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "candidate": {"type": "string"},
                    "change": {"type": "string"},
                    "verdict": {
                        "type": "string",
                        "enum": ["helped", "hurt", "no observed effect", "confounded"],
                    },
                    "evidence": {"type": "string"},
                },
                "required": ["candidate", "change", "verdict", "evidence"],
                "additionalProperties": False,
            },
        },
        "lesson": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "properties": {
                        "cue": {"type": "string"},
                        "lesson": {"type": "string"},
                        "evidence": {"type": "string"},
                        "confidence": {"type": "string"},
                    },
                    "required": ["cue", "lesson", "evidence", "confidence"],
                    "additionalProperties": False,
                },
            ]
        },
    },
    "required": ["verdicts", "lesson"],
    "additionalProperties": False,
}


def reflect(
    provider: Provider, model: str, candidates: Sequence[Candidate], *, effort: str = "high"
) -> dict:
    """Urteile je Änderung und höchstens eine Lehre, nur aus den Kipp-Tabellen von Dev-Train."""
    record = [
        {"candidate": c.dimension, "notes": c.notes, "flips": c.flips,
         "flips_by_confusion": c.flip_pairs}
        for c in candidates
        if c.harness is not None
    ]  # fmt: skip
    if not record:
        return {"verdicts": [], "lesson": None}
    response = chat(
        provider,
        ChatRequest(
            model=model,
            messages=[
                {"role": "system", "content": REFLECT_SYSTEM},
                {"role": "user", "content": json.dumps(record, ensure_ascii=False, indent=1)},
            ],
            temperature=None,
            max_tokens=16000,
            schema=REFLECT_SCHEMA,
            reasoning_effort=effort,
        ),
    )
    return json.loads(response.text)


def library_text(library: Path) -> str:
    """Was der Optimierer aus früheren Schritten sieht: Vorschläge, Kipp-Tabellen, Annahme,
    Urteile und Lehren, aber nie κ aus Dev-Val; sonst passte er sich über die Schritte an
    Dev-Val an."""
    if not library.exists():
        return ""
    entries = [json.loads(x) for x in library.read_text(encoding="utf-8").splitlines() if x]
    return "\n".join(json.dumps({k: v for k, v in e.items() if k != "val_kappa"}) for e in entries)


@dataclass
class StepResult:
    candidates: list[Candidate]
    accepted: Candidate | None
    best_val: float
    reflection: dict


def step(
    *,
    current: Harness,
    current_pred: Mapping[str, str | None],
    best_val: float,
    condition: Condition,
    train: Sequence[Passage],
    gold_train: Mapping[str, str],
    coder: Callable[[Harness, Sequence[Passage]], dict[str, str | None]],
    val_kappa: Callable[[Harness], float],
    optimizer: Provider,
    optimizer_model: str,
    codes: Sequence[str],
    forbidden: Sequence[str],
    base: Harness,
    library: Path,
    margin: float,
    step_no: int,
) -> StepResult:
    """Vorschlagen je Dimension, Zusammenführen, Erproben. Der Kandidat mit dem besten Saldo auf
    Dev-Train geht auf Dev-Val (ein Blick) und wird nur angenommen, wenn er die beste Fassung um
    `margin` schlägt. Danach Reflexion in die Bibliothek."""
    lib = library_text(library)
    ev = evidence(train, current_pred, gold_train)
    supp = support(train, current_pred, gold_train)
    common = dict(codes=codes, forbidden=forbidden, base=base, support_map=supp)

    def probe(cand: Candidate) -> Candidate:
        if cand.harness is not None:
            cand.pred = coder(cand.harness, train)
            cand.flips = flips(current_pred, cand.pred, gold_train)
            cand.flip_pairs = flip_pairs(current_pred, cand.pred, gold_train)
        return cand

    candidates = [
        probe(propose(optimizer, optimizer_model, current, condition, dim, ev, lib, **common))
        for dim in condition.dimensions
    ]
    helpful = [c for c in candidates if c.harness is not None and c.net > 0]
    if len(helpful) >= 2:
        candidates.append(
            probe(compose(optimizer, optimizer_model, current, condition, helpful, **common))
        )
    tried = [c for c in candidates if c.harness is not None]
    best = max(tried, key=lambda c: c.net, default=None)
    accepted = None
    if best is not None and beats_noise(best.flips):
        best.val_kappa = val_kappa(best.harness)
        if best.val_kappa >= best_val + margin:
            accepted, best_val = best, best.val_kappa
    reflection = reflect(optimizer, optimizer_model, candidates)
    library.parent.mkdir(parents=True, exist_ok=True)
    with library.open("a", encoding="utf-8") as f:
        for c in candidates:
            entry = {
                "step": step_no, "condition": condition.name, "dimension": c.dimension,
                "parent": current.sha, "sha": c.harness.sha if c.harness else None,
                "notes": c.notes, "problems": c.problems, "flips_dev_train": c.flips,
                "flips_by_confusion": c.flip_pairs, "val_kappa": c.val_kappa,
                "accepted": c is accepted,
            }  # fmt: skip
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        f.write(json.dumps({"step": step_no, "reflection": reflection}, ensure_ascii=False) + "\n")
    return StepResult(candidates, accepted, best_val, reflection)


@dataclass
class RunResult:
    final: Harness
    best_val: float
    steps: list[StepResult]


def run(
    *,
    base: Harness,
    base_pred: Mapping[str, str | None],
    base_val: float,
    steps: int,
    save: Callable[[Harness], None] = lambda h: None,
    **kwargs,
) -> RunResult:
    """Feste Zahl sequenzieller Schritte (Hauptanalyse V₃). Ohne Annahme bleibt die Fassung,
    und der nächste Schritt sieht den Fehlschlag in der Bibliothek."""
    current, current_pred, best_val, history = base, dict(base_pred), base_val, []
    for no in range(1, steps + 1):
        result = step(
            current=current, current_pred=current_pred, best_val=best_val, base=base,
            step_no=no, **kwargs,
        )  # fmt: skip
        for c in result.candidates:
            if c.harness is not None:
                save(c.harness)
        if result.accepted is not None:
            current, current_pred = result.accepted.harness, result.accepted.pred
        best_val = result.best_val
        history.append(result)
    return RunResult(current, best_val, history)
