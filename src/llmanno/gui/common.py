"""Gemeinsame GUI-Bausteine: Seitenbasis, Hintergrund-Tasks, kleine Widget-Helfer."""

from __future__ import annotations

import re
import weakref
from collections.abc import Callable, Iterable
from html import escape
from pathlib import Path
from typing import Any

import shiboken6
from pydantic import ValidationError
from PySide6.QtCore import (
    QT_TRANSLATE_NOOP,
    QCoreApplication,
    QObject,
    QSettings,
    Qt,
    QThread,
    Signal,
    Slot,
)
from PySide6.QtWidgets import (
    QComboBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from llmanno.core.project import Project

# Sprache der Oberfläche in dieser Sitzung (app.main setzt sie beim Start); die Sprache von
# Report, Präregistrierung und Paket wählt das Menü Sprache getrennt davon.
UI_LANGUAGE = "de"
OUTPUT_LANGUAGE_KEY = "output/language"
ADVANCED_KEY = "ui/advanced"  # „Erweitert“-Bereiche offen (Menü Ansicht)


def output_language_setting() -> str:
    value = str(QSettings().value(OUTPUT_LANGUAGE_KEY, UI_LANGUAGE))
    return value if value in ("de", "en") else "de"


class Page(QWidget):
    """Ein Tab. `changed` meldet geänderte Konfiguration (für Titel und Speichern-Zustand)."""

    changed = Signal()
    # Abschnitte der Anleitung für F1: der erste wird gezeigt, die übrigen als „Siehe auch“
    help_topics: tuple[str, ...] = ()

    def __init__(self) -> None:
        super().__init__()
        self.project: Project | None = None

    def set_project(self, project: Project | None, refresh: bool = True) -> None:
        """`refresh=False`: erst beim Wechsel auf den Tab neu laden (große Projekte)."""
        self.project = project
        if refresh:
            self.refresh()

    def refresh(self) -> None:
        """Liest den Stand neu aus dem Projekt; wird auch beim Wechsel auf den Tab gerufen."""


_collapsibles: weakref.WeakSet[Collapsible] = weakref.WeakSet()


def advanced_setting() -> bool:
    return QSettings().value(ADVANCED_KEY, False) in (True, "true", "1", 1)


def set_advanced(on: bool) -> None:
    """Alle „Erweitert“-Bereiche öffnen oder schließen und das für den nächsten Start merken."""
    QSettings().setValue(ADVANCED_KEY, on)
    for box in list(_collapsibles):
        if shiboken6.isValid(box):  # das Python-Objekt kann sein Widget überleben
            box.toggle.setChecked(on)


class Collapsible(QWidget):
    """Ausklappbarer Bereich „Erweitert“ für Einstellungen, deren Vorgaben für den Einstieg
    passen. Inhalt kommt in `body`; Ansicht → Erweiterte Einstellungen öffnet alle."""

    def __init__(self, title: str = "") -> None:
        super().__init__()
        self.toggle = QToolButton()
        self.toggle.setText(title or self.tr("Erweitert"))
        self.toggle.setCheckable(True)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setAutoRaise(True)
        self.body = QWidget()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toggle)
        layout.addWidget(self.body)
        self.toggle.toggled.connect(self._show)
        self.toggle.setChecked(advanced_setting())
        self._show(self.toggle.isChecked())
        _collapsibles.add(self)

    def _show(self, on: bool) -> None:
        self.body.setVisible(on)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)


class _Worker(QObject):
    done = Signal(object)
    failed = Signal(object)
    progress = Signal(object)
    finished = Signal()

    def __init__(self, fn: Callable[[Callable[[Any], None]], Any]) -> None:
        super().__init__()
        self._fn = fn

    @Slot()
    def run(self) -> None:
        try:
            result = self._fn(self.progress.emit)
        except Exception as exc:  # jeder Fehler muss in der Oberfläche ankommen
            self.failed.emit(exc)
        else:
            self.done.emit(result)
        finally:
            self.finished.emit()


class Task(QObject):
    """Führt `fn(report)` in einem eigenen Thread aus.

    Die Signale werden an dieses Objekt weitergereicht, das im GUI-Thread lebt; dadurch kommen
    `done`, `failed` und `progress` immer im GUI-Thread an.
    """

    done = Signal(object)
    failed = Signal(object)
    progress = Signal(object)
    finished = Signal()

    def __init__(self, parent: QObject, fn: Callable[[Callable[[Any], None]], Any]) -> None:
        super().__init__(parent)
        self._thread = QThread()
        self._worker = _Worker(fn)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.done.connect(self.done)
        self._worker.failed.connect(self.failed)
        self._worker.progress.connect(self.progress)
        self._worker.finished.connect(self._thread.quit)
        self._started = self._finished = False
        # Vor allen anderen Slots: Wer das Ende abwartet, sieht danach den fertigen Zustand.
        self._thread.finished.connect(self._mark_finished)
        self._thread.finished.connect(self.finished)

    def _mark_finished(self) -> None:
        self._finished = True

    def start(self) -> None:
        self._started = True
        self._thread.start()

    def wait(self) -> None:
        self._thread.wait()

    @property
    def running(self) -> bool:
        """Bis `finished` im GUI-Thread angekommen ist, nicht nur bis der Thread endet: Sonst
        meldet `busy` das Ende, bevor Slots wie refresh gelaufen sind."""
        return self._started and not self._finished


def chosen_path(path: str, selected_filter: str) -> Path:
    """Endung passend zum gewählten Filter: Wer „Word (*.docx)“ wählt und den Namen auf
    .md stehen lässt, bekommt Word. Nicht jeder Dialog passt die Endung selbst an."""
    suffixes = re.findall(r"\*(\.\w+)", selected_filter)
    result = Path(path)
    if suffixes and result.suffix.lower() not in suffixes:
        result = result.with_suffix(suffixes[0])
    return result


def heading(tag: str, text: str) -> str:
    """HTML-Überschrift; tr() steht außerhalb eines f-Strings, damit lupdate es findet."""
    return f"<{tag}>{escape(text)}</{tag}>"


# Status einer Einheit oder eines Aufrufs, übersetzt statt als roher Schlüssel.
STATUS_LABELS = {
    "ok": QT_TRANSLATE_NOOP("Status", "gültig"),
    "invalid": QT_TRANSLATE_NOOP("Status", "ungültig"),
    "error": QT_TRANSLATE_NOOP("Status", "Fehler"),
    "pending": QT_TRANSLATE_NOOP("Status", "noch nicht kodiert"),
}


def status_text(status: str) -> str:
    return QCoreApplication.translate("Status", STATUS_LABELS.get(status, status))


def error_text(exc: BaseException) -> str:
    """Lesbare Fehlermeldung; Pydantic-Fehler ohne technisches Beiwerk."""
    if isinstance(exc, ValidationError):
        messages = [e["msg"].removeprefix("Value error, ") for e in exc.errors()]
        return "\n".join(dict.fromkeys(messages))
    return str(exc) or type(exc).__name__


def error_label() -> QLabel:
    label = QLabel()
    label.setWordWrap(True)
    label.setStyleSheet("color: #b3261e")
    return label


def hint_label(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: palette(placeholder-text)")
    return label


def fill_combo(combo: QComboBox, entries: Iterable[tuple[str, Any]], selected: Any = None) -> None:
    combo.blockSignals(True)
    combo.clear()
    for text, data in entries:
        combo.addItem(text, data)
    index = combo.findData(selected)
    combo.setCurrentIndex(index if index >= 0 else 0)
    combo.blockSignals(False)


def optional_columns(columns: list[str]) -> list[tuple[str, str | None]]:
    """Einträge für eine Spaltenauswahl, in der „keine“ erlaubt ist."""
    return [(QCoreApplication.translate("common", "— keine —"), None), *((c, c) for c in columns)]


def fill_checklist(widget: QListWidget, entries: Iterable[str], checked: Iterable[str]) -> None:
    marked = set(checked)
    widget.blockSignals(True)
    widget.clear()
    for entry in entries:
        item = QListWidgetItem(entry)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if entry in marked else Qt.CheckState.Unchecked)
        widget.addItem(item)
    widget.blockSignals(False)


def checked_entries(widget: QListWidget) -> list[str]:
    return [
        widget.item(i).text()
        for i in range(widget.count())
        if widget.item(i).checkState() == Qt.CheckState.Checked
    ]


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "–"
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} h {minutes:02d} min"
    if minutes:
        return f"{minutes} min {secs:02d} s"
    return f"{secs} s"


def project_task(parent: QObject, project: Project, fn: Callable[[Project], Any]) -> Task:
    """fn(Projekt) im Hintergrund mit eigener Store-Verbindung (SQLite bindet Verbindungen an
    ihren Thread) und der Konfiguration vom Start, auch ungespeichert."""
    folder, config = project.folder, project.config

    def work(report: Callable[[Any], None]) -> Any:
        own = Project.open(folder)
        try:
            own.config = config
            return fn(own)
        finally:
            own.close()

    return Task(parent, work)
