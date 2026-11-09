"""Tab „Projekt“: Projekt anlegen oder öffnen, Grundeinstellungen bearbeiten."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from llmanno.core.lock import storage_warning
from llmanno.core.project import Project
from llmanno.gui.common import Collapsible, hint_label

RECENT_KEY = "recent/projects"


def recent_projects() -> list[str]:
    """Zuletzt geöffnete Projektordner, neueste zuerst."""
    value = QSettings().value(RECENT_KEY, [])
    return [value] if isinstance(value, str) else list(value or [])


class ProjectPage(QStackedWidget):
    help_topics = (
        "1. Projekt",
        "Wofür das Programm gedacht ist",
        "Installation und Start",
    )

    new_requested = Signal()
    open_requested = Signal()
    demo_requested = Signal()
    recent_requested = Signal(str)
    save_requested = Signal()
    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.project: Project | None = None
        self.addWidget(self._build_empty())
        self.addWidget(self._build_form())

    def _build_empty(self) -> QWidget:
        new_button = QPushButton(self.tr("Neues Projekt…"))
        new_button.setToolTip(self.tr("Ein Assistent fragt Ordner, Daten, Codebuch und Modell ab."))
        new_button.clicked.connect(self.new_requested)
        open_button = QPushButton(self.tr("Projekt öffnen…"))
        open_button.setToolTip(self.tr("Ordner eines bestehenden Projekts wählen."))
        open_button.clicked.connect(self.open_requested)
        demo_button = QPushButton(self.tr("Demo öffnen"))
        demo_button.setToolTip(
            self.tr("Beispielprojekt mit 200 synthetischen Gesprächszügen und simulierten "
                    "Antworten (kein LLM): alles ansehen, ohne eigene Daten oder Modell.")
        )  # fmt: skip
        demo_button.clicked.connect(self.demo_requested)

        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(new_button)
        buttons.addWidget(open_button)
        buttons.addWidget(demo_button)
        buttons.addStretch()

        title = QLabel(self.tr("Kein Projekt geöffnet."))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-weight: bold")
        intro = hint_label(
            self.tr(
                "Das Programm lässt Texte, etwa Transkripte oder offene Antworten, von "
                "Sprachmodellen (LLM) nach einem eigenen Codebuch kodieren und misst an "
                "menschlichen Kodierungen (Gold), wie gut das gelingt.\n\n"
                "Zum Kennenlernen die Demo öffnen; sie braucht weder eigene Daten noch ein "
                "Modell. Für eine eigene Studie ein neues Projekt anlegen und den Tabs von links "
                "nach rechts folgen. F1 öffnet die Anleitung zum jeweiligen Tab."
            )
        )
        intro.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.recent_label = QLabel(self.tr("Zuletzt geöffnet (Doppelklick öffnet):"))
        self.recent_list = QListWidget()
        self.recent_list.setMaximumHeight(140)
        self.recent_list.itemActivated.connect(
            lambda item: self.recent_requested.emit(item.data(Qt.ItemDataRole.UserRole))
        )

        column = QVBoxLayout()
        column.addStretch()
        column.addWidget(title)
        column.addWidget(intro)
        column.addLayout(buttons)
        column.addSpacing(16)
        column.addWidget(self.recent_label)
        column.addWidget(self.recent_list)
        column.addStretch()
        inner = QWidget()
        inner.setMaximumWidth(640)
        inner.setLayout(column)

        layout = QHBoxLayout()
        layout.addStretch()
        layout.addWidget(inner, 1)
        layout.addStretch()
        page = QWidget()
        page.setLayout(layout)
        return page

    def _fill_recent(self) -> None:
        self.recent_list.clear()
        for folder in [f for f in recent_projects() if Path(f).is_dir()][:5]:
            where = folder if len(folder) <= 60 else "…" + folder[-60:]
            item = QListWidgetItem(f"{Path(folder).name}  ({where})")
            item.setData(Qt.ItemDataRole.UserRole, folder)
            item.setToolTip(folder)
            self.recent_list.addItem(item)
        has_recent = self.recent_list.count() > 0
        self.recent_label.setVisible(has_recent)
        self.recent_list.setVisible(has_recent)

    def _build_form(self) -> QWidget:
        self.name_edit = QLineEdit()
        self.folder_edit = QLineEdit()
        self.folder_edit.setReadOnly(True)
        self.created_label = QLabel()

        self.language_combo = QComboBox()
        self.language_combo.addItem(self.tr("Deutsch"), "de")
        self.language_combo.addItem(self.tr("Englisch"), "en")
        self.language_combo.setToolTip(
            self.tr("Sprache der Anweisungen an das Modell. Das Codebuch geht unverändert mit; "
                    "am besten die Sprache von Codebuch und Daten wählen.")
        )  # fmt: skip

        self.mode_combo = QComboBox()
        self.mode_combo.addItem(self.tr("Ein Code je Einheit (Single-Label)"), "single")
        self.mode_combo.addItem(self.tr("Mehrere Codes je Einheit (Multi-Label)"), "multi")
        self.mode_combo.setToolTip(
            self.tr("Ein Code je Einheit: Das Modell wählt den passendsten Code, weitere nur als "
                    "Alternativen. Mehrere Codes: Jeder zutreffende Code zählt.")
        )  # fmt: skip

        self.k_spin = QSpinBox()
        self.k_spin.setRange(1, 3)
        self.k_spin.setToolTip(
            self.tr("Ein Code je Einheit: 1 Code plus bis zu k−1 Alternativen. Mehrere Codes: "
                    "höchstens k. Standard 3.")
        )  # fmt: skip

        self.error_label = QLabel()
        self.error_label.setStyleSheet("color: #b3261e")
        self.storage_label = QLabel()
        self.storage_label.setWordWrap(True)
        self.storage_label.setStyleSheet("color: #b3261e")
        self.recode_hint = hint_label(
            self.tr("Sprache, Kodiermodus und Codes pro Einheit gehören zu den Anweisungen: Gibt "
                    "es schon Kodierläufe, kodiert der nächste Lauf alle Einheiten neu.")
        )  # fmt: skip
        self.save_button = QPushButton(self.tr("Speichern"))
        self.save_button.clicked.connect(self.save_requested)

        self.name_edit.textEdited.connect(self._on_edit)
        self.language_combo.currentIndexChanged.connect(self._on_edit)
        self.mode_combo.currentIndexChanged.connect(self._on_edit)
        self.k_spin.valueChanged.connect(self._on_edit)

        form = QFormLayout()
        form.addRow(self.tr("Name"), self.name_edit)
        form.addRow(self.tr("Ordner"), self.folder_edit)
        form.addRow("", self.storage_label)
        form.addRow(self.tr("Angelegt"), self.created_label)
        form.addRow(self.tr("Prompt-Sprache"), self.language_combo)
        form.addRow(self.tr("Kodiermodus"), self.mode_combo)
        advanced = Collapsible()
        QFormLayout(advanced.body).addRow(self.tr("Codes pro Einheit (Top-k)"), self.k_spin)
        form.addRow(advanced)
        form.addRow(self.recode_hint)
        form.addRow(self.error_label)

        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.save_button)

        column = QVBoxLayout()
        column.addLayout(form)
        column.addLayout(buttons)
        column.addStretch()

        page = QWidget()
        page.setMaximumWidth(760)
        page.setLayout(column)
        return page

    def set_project(self, project: Project | None) -> None:
        self.project = project
        if project is None:
            self._fill_recent()
            self.setCurrentIndex(0)
            return
        config = project.config
        widgets = (self.name_edit, self.language_combo, self.mode_combo, self.k_spin)
        for widget in widgets:
            widget.blockSignals(True)
        self.name_edit.setText(config.name)
        self.language_combo.setCurrentIndex(self.language_combo.findData(config.prompt_language))
        self.mode_combo.setCurrentIndex(self.mode_combo.findData(config.mode))
        self.k_spin.setValue(config.k)
        for widget in widgets:
            widget.blockSignals(False)
        self.folder_edit.setText(str(project.folder))
        warning = storage_warning(project.folder)
        self.storage_label.setText(warning or "")
        self.storage_label.setVisible(warning is not None)
        self.created_label.setText(config.created.astimezone().strftime("%d.%m.%Y %H:%M"))
        self.error_label.clear()
        self.refresh()
        self.setCurrentIndex(1)

    def refresh(self) -> None:
        assert self.project is not None
        self.save_button.setEnabled(self.project.dirty and not self.error_label.text())

    def _on_edit(self) -> None:
        assert self.project is not None
        name = self.name_edit.text().strip()
        if name:
            self.error_label.clear()
            self.project.update(
                name=name,
                prompt_language=self.language_combo.currentData(),
                mode=self.mode_combo.currentData(),
                k=self.k_spin.value(),
            )
        else:
            self.error_label.setText(self.tr("Der Projektname darf nicht leer sein."))
        self.refresh()
        self.changed.emit()
