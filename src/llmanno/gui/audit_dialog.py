"""Projektprotokoll ansehen: jedes Ereignis mit Zeit und Details (Datei → Protokoll)."""

from __future__ import annotations

import json
from datetime import UTC
from typing import Any

from PySide6.QtCore import QT_TRANSLATE_NOOP
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from llmanno.core.project import Project
from llmanno.gui.common import hint_label

SHOWN, TOOLTIP = 160, 5000  # Zeichen der Kurzform in der Zelle und der Details im Tooltip

# Ereignisse des Protokolls in Klartext; unbekannte erscheinen mit ihrem Schlüssel.
EVENTS = {
    "project_created": QT_TRANSLATE_NOOP("AuditDialog", "Projekt angelegt"),
    "demo_created": QT_TRANSLATE_NOOP("AuditDialog", "Demo angelegt"),
    "config_changed": QT_TRANSLATE_NOOP("AuditDialog", "Einstellungen gespeichert"),
    "data_imported": QT_TRANSLATE_NOOP("AuditDialog", "Daten importiert"),
    "codebook_loaded": QT_TRANSLATE_NOOP("AuditDialog", "Codebuch geladen"),
    "gold_updated": QT_TRANSLATE_NOOP("AuditDialog", "Gold nachgetragen"),
    "pseudonyms_set": QT_TRANSLATE_NOOP("AuditDialog", "Namensliste gesetzt"),
    "codesystem_added": QT_TRANSLATE_NOOP("AuditDialog", "Codesystem hinzugefügt"),
    "instrument_transferred": QT_TRANSLATE_NOOP("AuditDialog", "Instrument übernommen"),
    "split_created": QT_TRANSLATE_NOOP("AuditDialog", "Aufteilung erzeugt"),
    "frozen": QT_TRANSLATE_NOOP("AuditDialog", "Eingefroren"),
    "unfrozen": QT_TRANSLATE_NOOP("AuditDialog", "Einfrieren aufgehoben"),
    "freeze_broken": QT_TRANSLATE_NOOP("AuditDialog", "Einfrieren gebrochen"),
    "remote_consent": QT_TRANSLATE_NOOP("AuditDialog", "Einwilligung externer Anbieter"),
    "boundary_suggested": QT_TRANSLATE_NOOP("AuditDialog", "Abgrenzung vorgeschlagen (LLM)"),
    "api_key_set": QT_TRANSLATE_NOOP("AuditDialog", "API-Key gespeichert"),
    "api_key_removed": QT_TRANSLATE_NOOP("AuditDialog", "API-Key entfernt"),
    "run_started": QT_TRANSLATE_NOOP("AuditDialog", "Lauf gestartet"),
    "run_finished": QT_TRANSLATE_NOOP("AuditDialog", "Lauf beendet"),
    "run_stopped": QT_TRANSLATE_NOOP("AuditDialog", "Lauf angehalten"),
    "run_aborted": QT_TRANSLATE_NOOP("AuditDialog", "Lauf abgebrochen"),
    "run_waiting": QT_TRANSLATE_NOOP("AuditDialog", "Lauf wartet (Anbieter überlastet)"),
    "parallel_reduced": QT_TRANSLATE_NOOP("AuditDialog", "Weniger parallele Anfragen"),
    "sensitivity_started": QT_TRANSLATE_NOOP("AuditDialog", "Prompt-Sensitivität gestartet"),
    "sensitivity_finished": QT_TRANSLATE_NOOP("AuditDialog", "Prompt-Sensitivität beendet"),
    "review_queue_created": QT_TRANSLATE_NOOP("AuditDialog", "Prüfliste erzeugt"),
    "review_suggestion_shown": QT_TRANSLATE_NOOP("AuditDialog", "LLM-Vorschlag angezeigt"),
    "test_evaluated": QT_TRANSLATE_NOOP("AuditDialog", "Prüfteil ausgewertet"),
    "test_exposed": QT_TRANSLATE_NOOP("AuditDialog", "Prüfteil vor dem Einfrieren exportiert"),
    "exported": QT_TRANSLATE_NOOP("AuditDialog", "Export"),
    "store_migrated": QT_TRANSLATE_NOOP("AuditDialog", "Projektdatenbank aktualisiert"),
}


def _summary(details: dict[str, Any]) -> str:
    """Kurzform für die Zelle: einfache Werte direkt, verschachtelte nur mit ihren Schlüsseln."""
    parts = []
    for key, value in details.items():
        if isinstance(value, dict):
            value = ", ".join(map(str, value))
        elif isinstance(value, list):
            simple = [str(v) for v in value if not isinstance(v, dict | list)]
            value = ", ".join(simple) if len(simple) == len(value) else f"[{len(value)}]"
        parts.append(f"{key}: {value}")
    text = " · ".join(parts)
    return text if len(text) <= SHOWN else text[: SHOWN - 1] + "…"


class AuditDialog(QDialog):
    def __init__(self, project: Project, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Protokoll: {0}").format(project.config.name))
        self.resize(1000, 600)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText(self.tr("Filtern nach Ereignis oder Inhalt"))
        self.filter.textChanged.connect(self._apply_filter)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(
            [self.tr("Zeit (Ortszeit)"), self.tr("Ereignis"), self.tr("Details")]
        )
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        entries = project.store.audit()[::-1]  # neueste zuerst
        self.table.setRowCount(len(entries))
        self._texts: list[str] = []
        for row, entry in enumerate(entries):
            label = self.tr(EVENTS.get(entry.event, entry.event))
            details = json.dumps(entry.details, ensure_ascii=False, default=str)
            self._texts.append(f"{entry.event} {label} {details}".casefold())
            pretty = json.dumps(entry.details, ensure_ascii=False, indent=2, default=str)
            cells = [
                (
                    entry.ts.astimezone().strftime("%d.%m.%Y %H:%M:%S"),
                    entry.ts.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
                ),
                (label, entry.event),
                (_summary(entry.details), pretty[:TOOLTIP]),
            ]
            for column, (text, tip) in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setToolTip(tip)
                self.table.setItem(row, column, item)
        self.table.resizeColumnToContents(0)
        self.table.resizeColumnToContents(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(
            hint_label(
                self.tr(
                    "Das Protokoll hält jeden Schritt im Projekt mit Zeit fest und lässt sich "
                    "nicht ändern; Report und Replikationspaket stützen sich darauf. Die vollen "
                    "Details zeigt der Tooltip."
                )
            )
        )
        layout.addWidget(self.filter)
        layout.addWidget(self.table)
        layout.addWidget(buttons)

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().casefold()
        for row, haystack in enumerate(self._texts):
            self.table.setRowHidden(row, bool(needle) and needle not in haystack)
