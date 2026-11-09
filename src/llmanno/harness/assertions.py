"""Aussagen-Schicht nach Bernado et al. (2026), Bedingung B5.

1. Opus schreibt einmal Ja/Nein-Aussagen über beobachtbares Verhalten, nur aus dem Manual.
2. Sonnet beantwortet sie je Passage für jeden Zielsatz.
3. Eine L1-logistische Regression je Code (one-vs-rest) setzt die Antworten zu einem Code
   zusammen; C wählt eine Kreuzvalidierung, gruppiert nach Transkript.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GroupKFold
from sklearn.multiclass import OneVsRestClassifier

from llmanno.core.metrics.agreement import cohen_kappa
from llmanno.core.providers.base import ChatRequest, Provider
from llmanno.harness.runtime import COLUMNS, Columns, Passage, _line, chat

C_GRID = (0.03, 0.1, 0.3, 1.0, 3.0)

GENERATE_SYSTEM = """\
You turn a coding manual for teacher talk moves into yes/no assertions that an annotator can \
check for one teacher sentence in its transcript context. Each assertion describes observable \
behaviour (what the teacher says or does relative to the preceding lines), not a code label. \
Cover every code and the boundaries between codes. Keep each assertion short and checkable."""

GENERATE_SCHEMA = {
    "type": "object",
    "properties": {"assertions": {"type": "array", "items": {"type": "string"}}},
    "required": ["assertions"],
    "additionalProperties": False,
}


def generate(
    provider: Provider, model: str, codebook: str, *, n: int = 30, effort: str = "high"
) -> list[str]:
    """Aussagen nur aus dem Manual, ohne Gold (Präregistrierung Teil 2, B5)."""
    response = chat(
        provider,
        ChatRequest(
            model=model,
            messages=[
                {"role": "system", "content": GENERATE_SYSTEM},
                {
                    "role": "user",
                    "content": f"Write about {n} assertions.\n\n<manual>\n{codebook}\n</manual>",
                },
            ],
            temperature=None,
            max_tokens=16000,
            schema=GENERATE_SCHEMA,
            reasoning_effort=effort,
        ),
    )
    return [a.strip() for a in json.loads(response.text)["assertions"] if a.strip()]


def _system(assertions: Sequence[str]) -> str:
    listed = "\n".join(f"A{k:02d}: {a}" for k, a in enumerate(assertions, 1))
    return (
        "For every line marked [CODE], decide which of the following assertions are true for "
        "that teacher sentence in its context. Other lines are context only.\n\n"
        f"<assertions>\n{listed}\n</assertions>"
    )


def _schema(n: int) -> dict:
    ids = [f"A{k:02d}" for k in range(1, n + 1)]
    item = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "true": {"type": "array", "items": {"type": "string", "enum": ids}},
        },
        "required": ["id", "true"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"answers": {"type": "array", "items": item}},
        "required": ["answers"],
        "additionalProperties": False,
    }


@dataclass
class Answers:
    true: dict[str, set[str]]  # Satz-ID → wahre Aussagen; fehlt die ID, war die Antwort ungültig
    cost_usd: float


def answer(
    assertions: Sequence[str],
    passages: Sequence[Passage],
    provider: Provider,
    *,
    model: str = "claude-sonnet-5-5",
    effort: str = "medium",
    log: Path | None = None,
    workers: int = 8,
    cols: Columns = COLUMNS,
) -> Answers:
    system, schema = _system(assertions), _schema(len(assertions))
    lock = threading.Lock()

    def one(p: Passage) -> tuple[dict[str, set[str]], float]:
        text = "\n".join(_line(r, set(p.targets), cols) for _, r in p.lines.iterrows())
        response = chat(
            provider,
            ChatRequest(
                model=model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": text}],
                temperature=None,
                max_tokens=16000,
                schema=schema,
                reasoning_effort=effort,
            ),
        )
        try:
            got = {a["id"]: set(a["true"]) for a in json.loads(response.text)["answers"]}
        except (ValueError, KeyError, TypeError):
            got = {}
        cost = float(response.extra.get("cost_usd", 0.0))
        if log is not None:
            entry = {
                "assertions_sha": hashlib.sha256(system.encode("utf-8")).hexdigest()[:16],
                "passage": p.name, "response": response.text, "cost_usd": cost,
            }  # fmt: skip
            with lock, log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return {i: got[i] for i in p.targets if i in got}, cost

    if log is not None:
        log.parent.mkdir(parents=True, exist_ok=True)
    true: dict[str, set[str]] = {}
    cost = 0.0
    with ThreadPoolExecutor(workers) as pool:
        for got, c in pool.map(one, passages):
            true |= got
            cost += c
    return Answers(true, cost)


def features(answers: Mapping[str, set[str]], ids: Sequence[str], n: int) -> np.ndarray:
    names = [f"A{k:02d}" for k in range(1, n + 1)]
    return np.array([[name in answers.get(i, set()) for name in names] for i in ids], dtype=float)


class Aggregator:
    """L1-logistische Regression je Code, C per gruppierter Kreuzvalidierung auf κ."""

    def fit(
        self, x: np.ndarray, y: Sequence[str], groups: Sequence[str], *, folds: int = 5
    ) -> Aggregator:
        y, groups = np.asarray(y), np.asarray(groups)
        scores = {}
        for c in C_GRID:
            pred = np.empty(len(y), dtype=object)
            for train, test in GroupKFold(n_splits=min(folds, len(set(groups)))).split(
                x, y, groups
            ):
                model = self._model(c).fit(x[train], y[train])
                pred[test] = model.predict(x[test])
            scores[c] = cohen_kappa(list(y), list(pred))
        self.c = max(C_GRID, key=lambda c: (scores[c], -c))  # bei Gleichstand das kleinere C
        self.cv_kappa = scores
        self.model = self._model(self.c).fit(x, y)
        return self

    @staticmethod
    def _model(c: float) -> OneVsRestClassifier:
        return OneVsRestClassifier(
            LogisticRegression(l1_ratio=1.0, solver="liblinear", C=c, max_iter=1000)
        )

    def predict(self, x: np.ndarray) -> list[str]:
        """Der Code mit der höchsten Wahrscheinlichkeit."""
        return list(self.model.predict(x))
