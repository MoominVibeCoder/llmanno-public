"""Kodieren mit einer Harness-Fassung: Passagen ziehen, je Passage oder je Satz anfragen."""

from __future__ import annotations

import hashlib
import json
import random
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from llmanno.core.providers.base import ChatRequest, ChatResponse, Provider, ProviderError
from llmanno.harness.tree import Harness

RETRIES = 6


@dataclass(frozen=True)
class Passage:
    name: str
    lines: pd.DataFrame  # Kontext davor und alle Zeilen bis zum letzten Zielsatz
    targets: tuple[str, ...]  # IDs der zu kodierenden Sätze


@dataclass(frozen=True)
class Columns:
    id: str = "ID"
    group: str = "Transkript"
    role: str = "Rolle"
    text: str = "Text"


COLUMNS = Columns()  # TalkMoves-Import von llmanno


def draw_passages(
    df: pd.DataFrame,
    *,
    target_role: str,
    n: int,
    per_group: int,
    seed: int,
    context: int,
    groups: Sequence[str] | None = None,
    cols: Columns = COLUMNS,
) -> list[Passage]:
    """Je Gruppe `per_group` sich nicht überlappende Abschnitte mit `n` Zielsätzen."""
    rng = random.Random(seed)
    out = []
    for group in groups or sorted(df[cols.group].unique()):
        t = df[df[cols.group] == group].reset_index(drop=True)
        rows = list(t.index[t[cols.role] == target_role])
        if len(rows) < n * per_group:
            raise ValueError(f"{group}: nur {len(rows)} Zielsätze für {per_group} × {n}")
        starts: list[int] = []
        while len(starts) < per_group:
            s = rng.randrange(0, len(rows) - n + 1)
            if all(abs(s - o) >= n for o in starts):
                starts.append(s)
        for k, s in enumerate(sorted(starts)):
            first, last = rows[s], rows[s + n - 1]
            ids = tuple(t.loc[rows[s : s + n], cols.id])
            out.append(
                Passage(f"{group} #{k + 1}", t.iloc[max(0, first - context) : last + 1], ids)
            )
    return out


def passages_from_ids(
    df: pd.DataFrame,
    targets: dict[str, Sequence[str]],
    *,
    context: int,
    cols: Columns = COLUMNS,
) -> list[Passage]:
    """Passagen aus einer festen Zuteilung (Name → Ziel-IDs), etwa prereg/passagen.csv."""
    out = []
    for name, ids in targets.items():
        first = df.index[df[cols.id] == ids[0]][0]
        last = df.index[df[cols.id] == ids[-1]][0]
        group = df.at[first, cols.group]
        t = df[df[cols.group] == group]
        start = t.index.get_loc(first)
        lines = t.iloc[max(0, start - context) : t.index.get_loc(last) + 1]
        out.append(Passage(name, lines, tuple(ids)))
    return out


def _line(
    row: pd.Series, targets: set[str], cols: Columns, hints: Mapping[str, str] | None = None
) -> str:
    tag = f"[CODE] {row[cols.id]}" if row[cols.id] in targets else "[context]"
    hint = (hints or {}).get(row[cols.id]) if row[cols.id] in targets else None
    return f"{tag} {row[cols.role]}: {row[cols.text]}" + (f"\n    (checks: {hint})" if hint else "")


def requests(
    harness: Harness,
    passages: Sequence[Passage],
    cols: Columns = COLUMNS,
    *,
    prefix: Callable[[Passage], str] | None = None,
    hints: Mapping[str, str] | None = None,
) -> list[str]:
    """Nutzertexte je Anfrage: eine je Passage oder eine je Satz mit `context` Zeilen davor.

    `prefix` stellt je Passage einen Block voran, etwa Präzedenzfälle (B0+F). Er steht im
    Nutzertext, damit der System-Prompt gleich bleibt und aus dem Cache kommt.
    `hints` hängt an Zielzeilen eine Notiz an (Satz-ID → Text), etwa die Antworten auf
    Grenzfall-Aussagen (B6)."""
    out = []
    for p in passages:
        head = f"{prefix(p)}\n\n" if prefix else ""
        if harness.config["unit"] == "passage":
            body = "\n".join(_line(r, set(p.targets), cols, hints) for _, r in p.lines.iterrows())
            out.append(head + body)
            continue
        lines = p.lines.reset_index(drop=True)
        for target in p.targets:
            i = int(lines.index[lines[cols.id] == target][0])
            window = lines.iloc[max(0, i - harness.config["context"]) : i + 1]
            rendered = "\n".join(_line(r, {target}, cols, hints) for _, r in window.iterrows())
            out.append(head + rendered)
    return out


def chat(provider: Provider, request: ChatRequest, *, sleep=time.sleep) -> ChatResponse:
    """Ein Aufruf mit Wiederholung bei vorübergehenden Fehlern (429, 5xx, Überlastung)."""
    for attempt in range(RETRIES):
        try:
            return provider.chat(request)
        except ProviderError as exc:
            if not exc.retryable or attempt == RETRIES - 1:
                raise
            sleep(exc.retry_after or 5 * (attempt + 1))
    raise AssertionError("unreachable")


def schema(codes: list[str]) -> dict:
    item = {
        "type": "object",
        "properties": {"id": {"type": "string"}, "code": {"type": "string", "enum": codes}},
        "required": ["id", "code"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"codes": {"type": "array", "items": item}},
        "required": ["codes"],
        "additionalProperties": False,
    }


@dataclass
class Result:
    codes: dict[str, str | None]
    cost_usd: float
    truncated: int
    requests: int


def code(
    harness: Harness,
    passages: Sequence[Passage],
    codes: list[str],
    provider: Provider,
    *,
    log: Path | None = None,
    workers: int = 8,
    cols: Columns = COLUMNS,
    prefix: Callable[[Passage], str] | None = None,
    hints: Mapping[str, str] | None = None,
) -> Result:
    """Kodiert alle Zielsätze; ungültige oder fehlende Antworten bleiben None.

    Jeder Aufruf landet mit Fassung, Anfrage-Hash und Antwort im Protokoll (JSONL); das ist
    das Material, das der Optimierer später liest.
    """
    system = harness.system_prompt(codes)
    texts = requests(harness, passages, cols, prefix=prefix, hints=hints)
    lock = threading.Lock()

    def one(text: str) -> tuple[dict[str, str], float, bool]:
        request = ChatRequest(
            model=harness.config["model"],
            messages=[{"role": "system", "content": system}, {"role": "user", "content": text}],
            temperature=None,
            max_tokens=harness.config["max_tokens"],
            schema=schema(codes),
            reasoning_effort=harness.config["effort"],
        )
        response = chat(provider, request)
        try:
            got = {c["id"]: c["code"] for c in json.loads(response.text)["codes"]}
        except (ValueError, KeyError, TypeError):
            got = {}
        cost = float(response.extra.get("cost_usd", 0.0))
        if log is not None:
            entry = {
                "harness": harness.sha,
                "request_sha": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
                "request": text,
                "response": response.text,
                "finish_reason": response.finish_reason,
                "tokens_in": response.tokens_in,
                "tokens_out": response.tokens_out,
                "cost_usd": cost,
            }
            with lock, log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return got, cost, response.finish_reason == "length"

    if log is not None:
        log.parent.mkdir(parents=True, exist_ok=True)
    found: dict[str, str] = {}
    cost, truncated = 0.0, 0
    with ThreadPoolExecutor(workers) as pool:
        for got, c, cut in pool.map(one, texts):
            found |= got
            cost += c
            truncated += cut
    targets = [t for p in passages for t in p.targets]
    # Nur gültige Codes zu angefragten Sätzen zählen; erfundene IDs fallen weg
    result = {t: found[t] if found.get(t) in codes else None for t in targets}
    return Result(result, cost, truncated, len(texts))
