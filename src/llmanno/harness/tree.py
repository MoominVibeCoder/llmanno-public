"""Eine Harness-Fassung: ein Ordner mit Textdateien und harness.json, mit Hash und Diff."""

from __future__ import annotations

import difflib
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Immer geladene Texte, in dieser Reihenfolge im System-Prompt
FILES = ("system.md", "codebook.md", "rules.md", "notes.md")
CONFIG = "harness.json"
DEFAULTS: dict[str, Any] = {
    "model": "claude-sonnet-5-5",
    "effort": "medium",
    "unit": "passage",  # oder "sentence"
    "context": 3,  # Zeilen vor dem ersten Zielsatz bzw. vor jedem Satz
    "max_tokens": 16000,
}
B0_SYSTEM = (
    "You code teacher talk moves in math lesson transcripts with the codebook below.\n"
    "Code every line marked [CODE] with exactly one code. Other lines are context only."
)


@dataclass(frozen=True)
class Harness:
    files: dict[str, str]
    config: dict[str, Any] = field(default_factory=lambda: dict(DEFAULTS))
    parent: str | None = None  # Hash der Elternfassung

    @property
    def sha(self) -> str:
        """Identität der Fassung: Texte und Einstellungen, nicht die Herkunft."""
        payload = json.dumps([sorted(self.files.items()), sorted(self.config.items())])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def system_prompt(self, codes: list[str]) -> str:
        parts = [self.files.get("system.md", "").strip(), f"Allowed codes: {', '.join(codes)}."]
        for name, tag in (
            ("codebook.md", "codebook"),
            ("rules.md", "rules"),
            ("notes.md", "notes"),
        ):
            if text := self.files.get(name, "").strip():
                parts.append(f"<{tag}>\n{text}\n</{tag}>")
        return "\n\n".join(p for p in parts if p)


def from_codebook(codebook: str, **config: Any) -> Harness:
    """Startfassung B0: das menschliche Manual unverändert, Anweisung wie im Pilot."""
    return Harness({"system.md": B0_SYSTEM, "codebook.md": codebook}, DEFAULTS | config)


def load(path: Path) -> Harness:
    data = json.loads((path / CONFIG).read_text(encoding="utf-8"))
    files = {n: (path / n).read_text(encoding="utf-8") for n in FILES if (path / n).exists()}
    return Harness(files, DEFAULTS | data.get("config", {}), data.get("parent"))


def save(harness: Harness, path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        target = path / name
        if name in harness.files:
            target.write_text(harness.files[name], encoding="utf-8")
        elif target.exists():
            target.unlink()
    meta = {"sha": harness.sha, "parent": harness.parent, "config": harness.config}
    (path / CONFIG).write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def diff(old: Harness, new: Harness) -> str:
    """Unified Diff aller Texte und Einstellungen, als Vermerk und für die Bibliothek."""
    out: list[str] = []
    for name in FILES:
        a, b = old.files.get(name, ""), new.files.get(name, "")
        if a != b:
            out += difflib.unified_diff(
                a.splitlines(), b.splitlines(), f"a/{name}", f"b/{name}", lineterm=""
            )
    for key in sorted(set(old.config) | set(new.config)):
        if old.config.get(key) != new.config.get(key):
            out.append(f"{CONFIG}: {key} {old.config.get(key)!r} -> {new.config.get(key)!r}")
    return "\n".join(out)
