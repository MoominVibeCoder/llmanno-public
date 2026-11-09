"""Präzedenzfälle (B0+F): die ähnlichsten entschiedenen Sätze aus Dev-Train und Aggregator-Pool.

Suche per BM25 über den Zielsatz und die Zeile davor, ohne Abhängigkeit und ohne Modell. In
den Speicher kommen nur Sätze mit Gold aus Teilen, die der Optimierer sehen darf; Dev-Val und
Prüfteil gehören nie hinein (der Aufrufer stellt das über die Zuteilung sicher).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import pandas as pd

from llmanno.harness.runtime import COLUMNS, Columns, Passage

K1, B = 1.5, 0.75


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", str(text).lower())


@dataclass(frozen=True)
class Case:
    id: str
    group: str
    before: str  # Zeile davor, mit Rolle
    text: str
    gold: str


class Memory:
    def __init__(self, cases: Sequence[Case], k: int = 8) -> None:
        self.cases, self.k = list(cases), k
        self._docs = [Counter(_tokens(f"{c.before} {c.text}")) for c in self.cases]
        self._len = [sum(d.values()) for d in self._docs]
        self._avg = sum(self._len) / max(1, len(self._len))
        df = Counter(t for d in self._docs for t in d)
        n = len(self._docs)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    @classmethod
    def from_frame(
        cls,
        df: pd.DataFrame,
        ids: Sequence[str],
        gold: Mapping[str, str],
        *,
        k: int = 8,
        cols: Columns = COLUMNS,
    ) -> Memory:
        """Fälle für die Ziel-IDs; die Zeile davor stammt aus demselben Transkript."""
        wanted, cases = set(ids), []
        for _, t in df.groupby(cols.group, sort=False):
            rows = t.reset_index(drop=True)
            for i, r in rows.iterrows():
                if r[cols.id] in wanted:
                    prev = rows.iloc[i - 1] if i else None
                    before = f"{prev[cols.role]}: {prev[cols.text]}" if prev is not None else ""
                    cases.append(
                        Case(r[cols.id], r[cols.group], before, str(r[cols.text]), gold[r[cols.id]])
                    )
        return cls(cases, k)

    def search(self, query: str, *, exclude_group: str | None = None) -> list[Case]:
        """Die k besten Fälle; Fälle aus dem Transkript der Anfrage bleiben außen vor."""
        terms = _tokens(query)
        scored = []
        for case, doc, length in zip(self.cases, self._docs, self._len, strict=True):
            if case.group == exclude_group:
                continue
            score = sum(
                self._idf[t] * doc[t] * (K1 + 1) / (doc[t] + K1 * (1 - B + B * length / self._avg))
                for t in terms
                if t in doc
            )
            if score > 0:
                scored.append((score, case.id, case))
        return [c for *_, c in sorted(scored, key=lambda x: (-x[0], x[1]))[: self.k]]

    def block(self, passage: Passage, cols: Columns = COLUMNS) -> str:
        """Präzedenzfälle für alle Zielsätze einer Passage, ohne Doppelte, für den Prompt."""
        lines = passage.lines.reset_index(drop=True)
        group = str(lines.iloc[0][cols.group]) if cols.group in lines else None
        seen: dict[str, Case] = {}
        for target in passage.targets:
            k = int(lines.index[lines[cols.id] == target][0])
            before = f"{lines.iloc[k - 1][cols.text]} " if k else ""
            for case in self.search(before + str(lines.iloc[k][cols.text]), exclude_group=group):
                seen.setdefault(case.id, case)
        out = ["<precedents>", "Decided cases from other lessons (context line, coded line, code):"]
        for c in seen.values():
            out.append(f"- {c.before} | Teacher: {c.text} => {c.gold}" if c.before else
                       f"- Teacher: {c.text} => {c.gold}")  # fmt: skip
        out.append("</precedents>")
        return "\n".join(out)
