"""Gold-Wächter: der einzige Weg zum Gold im Harness, mit Rollen, Blickzähler und Protokoll.

Im Kern greift die Test-Sperre nur in `evaluation.evaluate`; Skripte könnten das Gold sonst
direkt lesen. Rollen:
- Optimierer B2/B3 sieht Labels, aber nur aus Dev-Train;
- die Annahme sieht nur Kennzahlen aus Dev-Val, und jeder Blick wird gezählt;
- der Prüfteil ist erst nach dem Freeze offen und nur einmal je Fassung.
Kodierer und Optimierer B1/B4 bekommen den Wächter gar nicht.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from llmanno.core.metrics.agreement import cohen_kappa

SPLITS = ("dev_train", "dev_val", "test")


class GoldGuard:
    def __init__(
        self,
        gold: Mapping[str, str],
        split: Mapping[str, str],
        log: Path,
        *,
        max_val_looks: int,
    ) -> None:
        unknown = set(split.values()) - set(SPLITS)
        if unknown:
            raise ValueError(f"Unbekannte Teile: {sorted(unknown)}")
        self._gold, self._split, self.log = dict(gold), dict(split), log
        self.max_val_looks = max_val_looks
        self.frozen = False
        self.val_looks = sum(1 for e in self._entries() if e["action"] == "dev_val_score")
        self._tested = {e["sha"] for e in self._entries() if e["action"] == "test_score"}

    def _entries(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines() if x]

    def _write(self, action: str, ids: Sequence[str], **extra: object) -> None:
        self.log.parent.mkdir(parents=True, exist_ok=True)
        entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "action": action, "n": len(ids)}
        with self.log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry | extra, ensure_ascii=False) + "\n")

    def _only(self, ids: Sequence[str], allowed: str) -> None:
        wrong = sorted({self._split.get(i, "?") for i in ids} - {allowed})
        if wrong:
            raise PermissionError(f"Nur {allowed} erlaubt, angefragt auch: {', '.join(wrong)}")

    def ids(self, part: str) -> list[str]:
        """Welche Einheiten zu einem Teil gehören; ohne Gold, für alle Rollen."""
        return [i for i, s in self._split.items() if s == part]

    def train_labels(self, ids: Sequence[str]) -> dict[str, str]:
        """Labels für den Optimierer (B2/B3): ausschließlich Dev-Train."""
        self._only(ids, "dev_train")
        self._write("dev_train_labels", ids)
        return {i: self._gold[i] for i in ids}

    def val_score(self, pred: Mapping[str, str | None], *, sha: str) -> float:
        """κ auf Dev-Val für die Annahme; keine Labels, und die Zahl der Blicke ist begrenzt."""
        ids = self.ids("dev_val")
        if self.val_looks >= self.max_val_looks:
            raise PermissionError(f"Alle {self.max_val_looks} Blicke auf Dev-Val verbraucht.")
        self.val_looks += 1
        kappa = cohen_kappa([self._gold[i] for i in ids], [pred.get(i) for i in ids])
        self._write("dev_val_score", ids, sha=sha, look=self.val_looks, kappa=round(kappa, 4))
        return kappa

    def test_score(self, pred: Mapping[str, str | None], *, sha: str) -> float:
        """κ auf dem Prüfteil: erst nach dem Freeze und einmal je Fassung."""
        if not self.frozen:
            raise PermissionError("Der Prüfteil ist bis zum Freeze gesperrt.")
        if sha in self._tested:
            raise PermissionError(f"Fassung {sha} wurde auf dem Prüfteil schon ausgewertet.")
        ids = self.ids("test")
        self._tested.add(sha)
        kappa = cohen_kappa([self._gold[i] for i in ids], [pred.get(i) for i in ids])
        self._write("test_score", ids, sha=sha, kappa=round(kappa, 4))
        return kappa
