"""Projektsperre und Warnung vor riskanten Speicherorten.

Zwei Programme mit demselben Projekt überschreiben sich gegenseitig `project.json`. Die Sperre
ist eine Datei im Projektordner mit Rechner, Prozess und Zeit. Im selben Prozess öffnen Lauf,
Split und Auswertung das Projekt zusätzlich mit eigener Verbindung; das wird gezählt.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

LOCK_FILE = "project.lock"
_held: dict[Path, int] = {}  # Projektordner → offene Projekte in diesem Prozess
_mutex = threading.Lock()  # Lauf, Split und Auswertung öffnen und schließen aus Threads


class ProjectLocked(OSError):
    def __init__(self, folder: Path, holder: dict) -> None:
        since = str(holder.get("since", "?"))
        try:
            since = datetime.fromisoformat(since).strftime("%d.%m.%Y %H:%M")
        except ValueError:
            pass
        super().__init__(
            f"Das Projekt ist schon geöffnet (Rechner {holder.get('host', '?')}, seit {since}), "
            "vielleicht in einem anderen Fenster. Zwei Programme mit demselben Projekt "
            "überschreiben sich gegenseitig die Konfiguration: das andere Fenster erst schließen. "
            "Ist das Programm dort abgestürzt, lässt sich die Sperre aufheben."
        )
        self.folder = folder
        self.holder = holder


def acquire(folder: Path, *, force: bool = False) -> None:
    with _mutex:
        _acquire(folder.resolve(), force)


def _acquire(key: Path, force: bool) -> None:
    if _held.get(key):
        _held[key] += 1
        return
    path = key / LOCK_FILE
    me = {
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "since": datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
    }
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(me, handle)
    except FileExistsError:
        holder = _read(path)
        if not force and holder is not None and not _stale(holder):
            raise ProjectLocked(key, holder) from None
        path.write_text(json.dumps(me), encoding="utf-8")
    _held[key] = 1


def release(folder: Path) -> None:
    with _mutex:
        _release(folder.resolve())


def _release(key: Path) -> None:
    count = _held.get(key, 0) - 1
    if count > 0:
        _held[key] = count
        return
    _held.pop(key, None)
    path = key / LOCK_FILE
    holder = _read(path)
    if holder and holder.get("pid") == os.getpid() and holder.get("host") == socket.gethostname():
        path.unlink(missing_ok=True)


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None  # unlesbar oder halb geschrieben: wie keine Sperre
    return data if isinstance(data, dict) else None


def _stale(holder: dict) -> bool:
    """Verwaist: derselbe Rechner, der Prozess läuft nicht mehr. Fremde Rechner sind nicht
    prüfbar; deren Sperre hebt nur der Mensch auf."""
    if holder.get("host") != socket.gethostname():
        return False
    pid = holder.get("pid")
    return not isinstance(pid, int) or not _alive(pid)


def _alive(pid: int) -> bool:
    if pid == os.getpid():
        return True
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return kernel32.GetLastError() == 5  # Zugriff verweigert: Prozess existiert
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# Ordner, die ein Synchronisationsdienst spiegelt; der Dienst überträgt `store.sqlite` und die
# WAL-Datei getrennt, und gleichzeitiges Öffnen auf zwei Rechnern zerstört den Store.
_SYNCED = ("onedrive", "dropbox", "nextcloud", "owncloud", "sciebo", "google drive",
           "googledrive", "icloud", "seafile", "box sync")  # fmt: skip


def storage_warning(folder: Path) -> str | None:
    """Hinweis, wenn der Projektordner auf einem Netzlaufwerk oder in einem Cloud-Ordner liegt."""
    resolved = folder.resolve()
    if str(resolved).startswith("\\\\") or _network_drive(resolved):
        return (
            "Der Projektordner liegt auf einem Netzlaufwerk. Die Datenbank (SQLite mit WAL) ist "
            "dort nicht zuverlässig; bitte lokal arbeiten und Kopien ablegen."
        )
    parts = [p.casefold() for p in resolved.parts]
    if any(name in part for part in parts for name in _SYNCED):
        return (
            "Der Projektordner liegt in einem synchronisierten Cloud-Ordner. Der Dienst überträgt "
            "Datenbank und Protokolldatei getrennt; geöffnet auf zwei Rechnern geht der Store "
            "kaputt. Bitte lokal arbeiten und das Replikationspaket teilen."
        )
    return None


def _network_drive(path: Path) -> bool:
    if sys.platform != "win32" or not path.drive:
        return False
    import ctypes

    return ctypes.windll.kernel32.GetDriveTypeW(path.drive + "\\") == 4  # DRIVE_REMOTE
