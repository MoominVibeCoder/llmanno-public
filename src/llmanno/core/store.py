"""SQLite-Store eines Projekts: einzige Quelle der Wahrheit für Laufzustand und Audit-Log."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from llmanno.core.config import LOW_EFFORTS

# Audit-Ereignis der Sensitivitätsläufe (core.sensitivity); sie stehen nie in der runs-Tabelle.
SENSITIVITY_STARTED = "sensitivity_started"

# Eintrag i hebt das Schema von Version i auf i + 1. Nur anhängen, nie ändern.
_MIGRATIONS = [
    # 1: Audit-Log, per Trigger append-only
    """
    CREATE TABLE audit (
        id      INTEGER PRIMARY KEY,
        ts      TEXT NOT NULL,
        event   TEXT NOT NULL,
        details TEXT NOT NULL
    );
    CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit
    BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
    CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit
    BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
    """,
    # 2: Einheiten, Aufrufe, inhaltsadressierte Prompt-Texte
    """
    CREATE TABLE units (
        unit_id TEXT PRIMARY KEY,
        row     INTEGER NOT NULL,
        grp     TEXT,
        ord     REAL NOT NULL,
        speaker TEXT,
        text    TEXT NOT NULL,
        meta    TEXT NOT NULL,
        gold    TEXT NOT NULL,
        source  TEXT NOT NULL
    );
    CREATE TABLE texts (
        sha256 TEXT PRIMARY KEY,
        text   TEXT NOT NULL
    );
    CREATE TABLE calls (
        id          INTEGER PRIMARY KEY,
        item_id     TEXT NOT NULL,
        model_key   TEXT NOT NULL,
        run         INTEGER NOT NULL,
        config_hash TEXT NOT NULL,
        seed        INTEGER,
        system_sha  TEXT NOT NULL REFERENCES texts (sha256),
        user_sha    TEXT NOT NULL REFERENCES texts (sha256),
        status      TEXT NOT NULL CHECK (status IN ('ok', 'invalid', 'error')),
        answer      TEXT,
        raw         TEXT NOT NULL,
        error       TEXT,
        flags       TEXT NOT NULL,
        request     TEXT NOT NULL,
        meta        TEXT NOT NULL,
        ts          TEXT NOT NULL,
        UNIQUE (item_id, model_key, run, config_hash)
    );
    """,
    # 3: Laufstarts mit den Einstellungen, nach denen ihre Ergebnisse aggregiert werden
    """
    CREATE TABLE runs (
        id          INTEGER PRIMARY KEY,
        model_key   TEXT NOT NULL,
        config_hash TEXT NOT NULL,
        settings    TEXT NOT NULL,
        ts          TEXT NOT NULL
    );
    """,
    # 4: Dev/Test-Zuordnung; Einheiten ohne Gold stehen nicht darin
    """
    CREATE TABLE splits (
        item_id TEXT PRIMARY KEY,
        part    TEXT NOT NULL CHECK (part IN ('dev', 'test'))
    );
    """,
    # 5: Review-Queue und Reviews; Reviews sind eine eigene, append-only Annotationsschicht
    """
    CREATE TABLE review_queue (
        item_id          TEXT PRIMARY KEY,
        position         INTEGER NOT NULL,
        reasons          TEXT NOT NULL,
        draw_probability REAL NOT NULL
    );
    CREATE TABLE reviews (
        id               INTEGER PRIMARY KEY,
        item_id          TEXT NOT NULL,
        reviewer         TEXT NOT NULL,
        mode             TEXT NOT NULL CHECK (mode IN ('blind', 'assisted')),
        codes            TEXT NOT NULL,
        note             TEXT NOT NULL,
        reasons          TEXT NOT NULL,
        draw_probability REAL,
        ts               TEXT NOT NULL
    );
    CREATE TRIGGER reviews_no_update BEFORE UPDATE ON reviews
    BEGIN SELECT RAISE(ABORT, 'reviews are append-only'); END;
    CREATE TRIGGER reviews_no_delete BEFORE DELETE ON reviews
    BEGIN SELECT RAISE(ABORT, 'reviews are append-only'); END;
    """,
]
SCHEMA_VERSION = len(_MIGRATIONS)


@dataclass(frozen=True)
class AuditEntry:
    ts: datetime
    event: str
    details: dict[str, Any]


@dataclass(frozen=True)
class Unit:
    unit_id: str
    row: int  # Zeilennummer in der Quelldatei, wie Excel sie anzeigt
    group: str | None
    order: float
    speaker: str | None
    text: str
    meta: dict[str, Any] = field(default_factory=dict)
    gold: dict[str, Any] = field(default_factory=dict)
    source: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CallRecord:
    item_id: str
    model_key: str
    run: int
    config_hash: str
    seed: int | None
    system_sha: str
    user_sha: str
    status: str  # ok | invalid | error
    answer: dict[str, Any] | None
    raw: list[str]
    error: str | None
    flags: list[str]
    request: dict[str, Any]
    meta: dict[str, Any]
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class QueueEntry:
    item_id: str
    position: int
    reasons: list[str]  # "random" und/oder Gate-Kriterien
    draw_probability: float  # Wahrscheinlichkeit, in die Queue zu kommen (Gate: 1)


@dataclass(frozen=True)
class Review:
    item_id: str
    reviewer: str
    mode: str  # blind | assisted
    codes: list[str]
    note: str = ""
    reasons: list[str] = field(default_factory=list)  # Stand der Queue beim Review
    draw_probability: float | None = None
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class Store:
    def __init__(self, con: sqlite3.Connection) -> None:
        self._con = con
        self._con.execute("PRAGMA foreign_keys = ON")
        # WAL: der Lauf schreibt aus einem Worker-Thread (eigene Verbindung), während die
        # Oberfläche Ergebnisse liest.
        self._con.execute("PRAGMA journal_mode = WAL")

    @classmethod
    def create(cls, path: Path) -> Store:
        if path.exists():
            raise FileExistsError(f"Store existiert bereits: {path}")
        store = cls(sqlite3.connect(path))
        store._migrate(0)
        return store

    @classmethod
    def open(cls, path: Path) -> Store:
        # Sonst legt sqlite still einen leeren Store an, und das Audit-Log wäre verloren.
        if not path.is_file():
            raise FileNotFoundError(f"Store fehlt: {path}")
        con = sqlite3.connect(path)
        version = con.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            con.close()
            raise ValueError(
                f"Der Store stammt aus einer neueren llmanno-Version (Schema {version}, diese "
                f"Version kennt bis {SCHEMA_VERSION}). Bitte llmanno aktualisieren."
            )
        if version < 1:
            con.close()
            raise ValueError(f"{path.name} ist kein llmanno-Store (Schema {version}).")
        backup = None
        if version < SCHEMA_VERSION:
            # Sicherung vor der Migration; die Backup-API nimmt auch den Inhalt der WAL-Datei mit.
            backup = path.with_name(f"{path.stem}.vor-migration-{version}{path.suffix}")
            try:
                target = sqlite3.connect(backup)
                try:
                    con.backup(target)
                finally:
                    target.close()
            except BaseException:
                con.close()  # sonst bleibt die Datei unter Windows gesperrt
                raise
        store = cls(con)
        if backup is not None:
            store._migrate(version)
            store.log(
                "store_migrated",
                from_version=version,
                to_version=SCHEMA_VERSION,
                backup=backup.name,
            )
        return store

    def _migrate(self, version: int) -> None:
        # DDL und user_version sind in SQLite transaktional: jede Migration ganz oder gar nicht.
        for target, script in enumerate(_MIGRATIONS[version:], start=version + 1):
            try:
                self._con.executescript(f"BEGIN; {script} PRAGMA user_version = {target}; COMMIT;")
            except sqlite3.Error:
                self._con.rollback()
                raise

    def close(self) -> None:
        self._con.close()

    # --- Audit ---------------------------------------------------------------------------------

    def log(self, event: str, **details: Any) -> None:
        with self._con:
            self._con.execute(
                "INSERT INTO audit (ts, event, details) VALUES (?, ?, ?)",
                (datetime.now(UTC).isoformat(), event, _dump(details)),
            )

    def audit(self) -> list[AuditEntry]:
        rows = self._con.execute("SELECT ts, event, details FROM audit ORDER BY id")
        return [AuditEntry(datetime.fromisoformat(ts), ev, json.loads(d)) for ts, ev, d in rows]

    # --- Einheiten -----------------------------------------------------------------------------

    def replace_units(self, units: Iterable[Unit]) -> None:
        if self.count_calls():
            raise RuntimeError(
                "Für dieses Projekt gibt es schon Kodierläufe. Neue Daten bitte in einem neuen "
                "Projekt importieren, damit die bisherigen Ergebnisse nachvollziehbar bleiben."
            )
        rows = [
            (u.unit_id, u.row, u.group, u.order, u.speaker, u.text,
             _dump(u.meta), _dump(u.gold), _dump(u.source))
            for u in units
        ]  # fmt: skip
        with self._con:
            self._con.execute("DELETE FROM units")
            self._con.executemany("INSERT INTO units VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)

    def update_gold(self, values: dict[str, tuple[dict[str, Any], dict[str, Any]]]) -> None:
        """Gold und Quellzeile je Einheit ersetzen; Texte und Aufrufe bleiben unberührt."""
        rows = [(_dump(gold), _dump(source), uid) for uid, (gold, source) in values.items()]
        with self._con:
            self._con.executemany("UPDATE units SET gold = ?, source = ? WHERE unit_id = ?", rows)

    def units(self) -> list[Unit]:
        rows = self._con.execute(
            "SELECT unit_id, row, grp, ord, speaker, text, meta, gold, source"
            " FROM units ORDER BY row"
        )
        return [
            Unit(
                uid, row, grp, ord_, spk, text, json.loads(meta), json.loads(gold), json.loads(src)
            )
            for uid, row, grp, ord_, spk, text, meta, gold, src in rows
        ]

    # --- Prompt-Texte --------------------------------------------------------------------------

    def put_text(self, text: str) -> str:
        sha = sha256_text(text)
        with self._con:
            self._con.execute("INSERT OR IGNORE INTO texts VALUES (?, ?)", (sha, text))
        return sha

    def get_text(self, sha: str) -> str:
        row = self._con.execute("SELECT text FROM texts WHERE sha256 = ?", (sha,)).fetchone()
        if row is None:
            raise KeyError(sha)
        return row[0]

    # --- Aufrufe -------------------------------------------------------------------------------

    def record_call(self, call: CallRecord) -> None:
        """Speichert einen Aufruf. Nur ein früherer Fehlversuch (status error) wird ersetzt."""
        values = (
            call.item_id, call.model_key, call.run, call.config_hash, call.seed,
            call.system_sha, call.user_sha, call.status, _dump(call.answer), _dump(call.raw),
            call.error, _dump(call.flags), _dump(call.request), _dump(call.meta),
            call.ts.isoformat(),
        )  # fmt: skip
        with self._con:
            cursor = self._con.execute(
                """
                INSERT INTO calls (item_id, model_key, run, config_hash, seed, system_sha,
                                   user_sha, status, answer, raw, error, flags, request, meta, ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (item_id, model_key, run, config_hash) DO UPDATE SET
                    seed = excluded.seed, system_sha = excluded.system_sha,
                    user_sha = excluded.user_sha, status = excluded.status,
                    answer = excluded.answer, raw = excluded.raw, error = excluded.error,
                    flags = excluded.flags, request = excluded.request, meta = excluded.meta,
                    ts = excluded.ts
                WHERE calls.status = 'error'
                """,
                values,
            )
            if cursor.rowcount == 0:
                raise RuntimeError(
                    f"Aufruf existiert bereits: {call.item_id} / {call.model_key} / Lauf {call.run}"
                )

    def calls(
        self,
        *,
        model_key: str | None = None,
        config_hash: str | None = None,
        item_id: str | None = None,
        with_raw: bool = True,
    ) -> list[CallRecord]:
        """`with_raw=False`: ohne Roh-Antworten (raw = []). Die sind bei Reasoning-Modellen
        oft der größte Teil der Zeile und werden nur für Detailansicht und Paket gebraucht."""
        raw_column = "raw" if with_raw else "'[]'"
        query = (
            "SELECT item_id, model_key, run, config_hash, seed, system_sha, user_sha, status,"
            f" answer, {raw_column}, error, flags, request, meta, ts FROM calls WHERE 1 = 1"
        )
        params: list[str] = []
        if item_id is not None:
            query += " AND item_id = ?"
            params.append(item_id)
        if model_key is not None:
            query += " AND model_key = ?"
            params.append(model_key)
        if config_hash is not None:
            query += " AND config_hash = ?"
            params.append(config_hash)
        rows = self._con.execute(query + " ORDER BY id", params)
        return [
            CallRecord(
                item, model, run, chash, seed, s_sha, u_sha, status, json.loads(answer),
                json.loads(raw), error, json.loads(flags), json.loads(request), json.loads(meta),
                datetime.fromisoformat(ts),
            )
            for item, model, run, chash, seed, s_sha, u_sha, status, answer, raw, error, flags,
            request, meta, ts in rows
        ]  # fmt: skip

    def _ok_calls(self, model_key: str):
        """Gültige Aufrufe eines Modells, neueste zuerst: (meta, request, Länge von raw)."""
        rows = self._con.execute(
            "SELECT meta, request, length(raw) FROM calls WHERE model_key = ? AND status = 'ok'"
            " ORDER BY id DESC",
            (model_key,),
        )
        for meta, request, length in rows:
            yield json.loads(meta), json.loads(request), length

    def mean_tokens_out(
        self, model_key: str, effort: str | None = None, limit: int = 500
    ) -> float | None:
        """Mittlere Ausgabe (mit Reasoning) der letzten gültigen Antworten eines Modells mit
        diesem gesendeten Reasoning-Aufwand, für die Vorschau vor einem Lauf."""
        values: list[int] = []
        for meta, request, _ in self._ok_calls(model_key):
            if request.get("reasoning_effort") == effort and isinstance(
                tokens := meta.get("tokens_out"), int
            ):
                values.append(tokens)
                if len(values) == limit:
                    break
        return sum(values) / len(values) if values else None

    def hidden_reasoning(self, model_key: str, limit: int = 50) -> bool:
        """Denkt das Modell vor der Antwort? Gemeldete Reasoning-Token oder deutlich mehr
        Ausgabe-Token, als die sichtbare Antwort braucht. Grundlage: die letzten gültigen
        Antworten ohne Repair, nicht abgeschnitten und nicht schon mit niedrigem Aufwand."""
        tokens = chars = seen = 0
        for meta, request, length in self._ok_calls(model_key):
            effort = str(request.get("reasoning_effort") or "").lower()
            if (
                "repair" in meta
                or meta.get("finish_reason") == "length"
                or effort in LOW_EFFORTS
                or not isinstance(meta.get("tokens_out"), int)
            ):
                continue
            if meta.get("tokens_reasoning"):
                return True
            tokens, chars, seen = tokens + meta["tokens_out"], chars + length, seen + 1
            if seen == limit:
                break
        # Sichtbares JSON braucht kaum mehr als ein Token je drei Zeichen.
        return chars > 0 and tokens > 2 * chars / 3

    def answered_items(self, model_key: str, config_hash: str) -> set[str]:
        """Einheiten mit mindestens einer Antwort (gültig oder ungültig) in diesem Lauf."""
        rows = self._con.execute(
            "SELECT DISTINCT item_id FROM calls WHERE model_key = ? AND config_hash = ?"
            " AND status != 'error'",
            (model_key, config_hash),
        )
        return {row[0] for row in rows}

    def called_items(self, answered: bool = False) -> set[str]:
        """Einheiten mit mindestens einem Aufruf, ohne die Aufrufe zu laden. `answered`: nur
        Aufrufe mit Antwort (gültig oder ungültig), ohne Fehler wie Netz oder Key."""
        query = "SELECT DISTINCT item_id FROM calls"
        if answered:
            query += " WHERE status != 'error'"
        return {row[0] for row in self._con.execute(query)}

    # --- Läufe --------------------------------------------------------------------------------

    def start_run(self, model_key: str, config_hash: str, settings: dict[str, Any]) -> None:
        with self._con:
            self._con.execute(
                "INSERT INTO runs (model_key, config_hash, settings, ts) VALUES (?, ?, ?, ?)",
                (model_key, config_hash, _dump(settings), datetime.now(UTC).isoformat()),
            )

    def latest_run(self, model_key: str) -> tuple[str, dict[str, Any]] | None:
        """config_hash und Einstellungen des zuletzt gestarteten Laufs eines Modells.

        Zählt der Start, nicht der letzte Aufruf: Wer zu einer früheren, schon vollständigen
        Konfiguration zurückkehrt, sieht wieder deren Ergebnisse."""
        row = self._con.execute(
            "SELECT config_hash, settings FROM runs WHERE model_key = ? ORDER BY id DESC LIMIT 1",
            (model_key,),
        ).fetchone()
        if row:
            return row[0], json.loads(row[1])
        # Stores aus der Zeit vor der runs-Tabelle: letzter Aufruf, ohne Einstellungen.
        # Aufrufe der Sensitivität zählen nicht, sonst stünde eine Prompt-Variante als Ergebnis da.
        sensitivity = {
            json.loads(details).get("config_hash")
            for (details,) in self._con.execute(
                "SELECT details FROM audit WHERE event = ?", (SENSITIVITY_STARTED,)
            )
        }
        rows = self._con.execute(
            "SELECT config_hash FROM calls WHERE model_key = ? ORDER BY id DESC", (model_key,)
        )
        return next(((h, {}) for (h,) in rows if h not in sensitivity), None)

    def latest_config_hash(self, model_key: str) -> str | None:
        latest = self.latest_run(model_key)
        return latest[0] if latest else None

    # --- Split --------------------------------------------------------------------------------

    def replace_split(self, parts: dict[str, str]) -> None:
        with self._con:
            self._con.execute("DELETE FROM splits")
            self._con.executemany("INSERT INTO splits VALUES (?, ?)", sorted(parts.items()))

    def split(self) -> dict[str, str]:
        return dict(self._con.execute("SELECT item_id, part FROM splits ORDER BY item_id"))

    # --- Review --------------------------------------------------------------------------------

    def replace_queue(self, entries: Iterable[QueueEntry]) -> None:
        rows = [(e.item_id, e.position, _dump(e.reasons), e.draw_probability) for e in entries]
        with self._con:
            self._con.execute("DELETE FROM review_queue")
            self._con.executemany("INSERT INTO review_queue VALUES (?, ?, ?, ?)", rows)

    def queue(self) -> list[QueueEntry]:
        rows = self._con.execute(
            "SELECT item_id, position, reasons, draw_probability FROM review_queue"
            " ORDER BY position"
        )
        return [QueueEntry(i, pos, json.loads(r), p) for i, pos, r, p in rows]

    def add_review(self, review: Review) -> None:
        with self._con:
            self._con.execute(
                "INSERT INTO reviews (item_id, reviewer, mode, codes, note, reasons,"
                " draw_probability, ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    review.item_id, review.reviewer, review.mode, _dump(review.codes),
                    review.note, _dump(review.reasons), review.draw_probability,
                    review.ts.isoformat(),
                ),
            )  # fmt: skip

    def reviews(self) -> list[Review]:
        """Alle Reviews in Eingabereihenfolge; spätere ersetzen frühere derselben Person."""
        rows = self._con.execute(
            "SELECT item_id, reviewer, mode, codes, note, reasons, draw_probability, ts"
            " FROM reviews ORDER BY id"
        )
        return [
            Review(i, who, mode, json.loads(c), note, json.loads(r), p, datetime.fromisoformat(ts))
            for i, who, mode, c, note, r, p, ts in rows
        ]

    def revision(self) -> tuple:
        """Ändert sich mit jedem neuen oder überschriebenen Aufruf (Fehler werden beim
        Fortsetzen ersetzt), jedem Lauf-Start und jedem Review."""
        return tuple(
            self._con.execute(
                "SELECT (SELECT COUNT(*) FROM calls), (SELECT MAX(ts) FROM calls),"
                " (SELECT MAX(id) FROM runs), (SELECT MAX(id) FROM reviews)"
            ).fetchone()
        )

    def count_calls(self) -> int:
        return self._con.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
