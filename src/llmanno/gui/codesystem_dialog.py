"""Weiteres Codesystem anlegen (core.codesystems): Name, Codebuch, Gold-Spalten."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from llmanno.core import data_import
from llmanno.core.project import Project
from llmanno.gui.common import (
    checked_entries,
    error_label,
    fill_checklist,
    fill_combo,
    hint_label,
    optional_columns,
)


class CodesystemDialog(QDialog):
    def __init__(
        self, main: Project, parent: QWidget | None = None, system: Project | None = None
    ) -> None:
        """`system`: bestehendes Codesystem, dessen Gold-Spalten sich ändern sollen."""
        super().__init__(parent)
        self.system = system
        self.setWindowTitle(
            self.tr("Codesystem hinzufügen") if system is None else self.tr("Codesystem")
        )
        self.resize(520, 520)
        self.codebook: Path | None = None
        data = main.config.data
        header: list[str] = []
        if data is not None and main.data_path is not None and main.data_path.is_file():
            header = data_import.read_sheet(main.data_path, data.sheet).header
        taken = set(data.mapping.columns()) - set(data.mapping.gold) if data else set()
        if data and data.mapping.consensus:
            taken.discard(data.mapping.consensus)
        free = [c for c in header if c not in taken]

        self.name = QLineEdit()
        self.name.setPlaceholderText(self.tr("z. B. Reference Move"))
        self.codebook_label = QLabel(self.tr("— keine Datei —"))
        choose = QPushButton(self.tr("Codebuch wählen…"))
        choose.clicked.connect(self._choose)
        codebook = QHBoxLayout()
        codebook.addWidget(self.codebook_label, 1)
        codebook.addWidget(choose)
        own = system.config.data.mapping if system and system.config.data else None
        self.gold = QListWidget()
        self.gold.setToolTip(
            self.tr("Spalten mit menschlichen Kodierungen nach diesem Codebuch, eine je Person. "
                    "Können leer bleiben und später nachgetragen werden.")
        )  # fmt: skip
        fill_checklist(self.gold, free, own.gold if own else [])
        self.consensus = QComboBox()
        self.consensus.setToolTip(
            self.tr("Optional: Spalte mit der gemeinsam abgestimmten Kodierung; sie ist dann der "
                    "Hauptmaßstab.")
        )  # fmt: skip
        fill_combo(self.consensus, optional_columns(free), own.consensus if own else None)
        if system is not None:
            self.name.setText(system.config.name)
            self.name.setReadOnly(True)
            choose.setEnabled(False)
            self.codebook_label.setText(self.tr("(im Tab Codebuch)"))
        source = system.config if system is not None else main.config
        self.mode = QComboBox()
        fill_combo(
            self.mode,
            [
                (self.tr("Ein Code je Einheit (Single-Label)"), "single"),
                (self.tr("Mehrere Codes je Einheit (Multi-Label)"), "multi"),
            ],
            source.mode,
        )
        self.mode.setToolTip(
            self.tr("Ein Code je Einheit: Das Modell wählt den passendsten Code, weitere nur als "
                    "Alternativen. Mehrere Codes: Jeder zutreffende Code zählt.")
        )  # fmt: skip
        self.k = QSpinBox()
        self.k.setRange(1, 3)
        self.k.setValue(source.k)
        self.k.setToolTip(
            self.tr("Ein Code je Einheit: 1 Code plus bis zu k−1 Alternativen. Mehrere Codes: "
                    "höchstens k. Standard 3.")
        )  # fmt: skip
        self.error = error_label()

        form = QFormLayout()
        form.addRow(self.tr("Name"), self.name)
        form.addRow(self.tr("Codebuch"), codebook)
        form.addRow(self.tr("Gold-Spalten"), self.gold)
        form.addRow(self.tr("Konsens-Spalte"), self.consensus)
        form.addRow(self.tr("Kodiermodus"), self.mode)
        form.addRow(self.tr("Codes pro Einheit (Top-k)"), self.k)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(
            hint_label(
                self.tr(
                    "Ein weiteres Codesystem kodiert dieselben Einheiten mit eigenem Codebuch und "
                    "eigener Auswertung: eigene Gold-Spalten, Ergebnisse, Reviews, Aufteilung "
                    "(Split) und Freeze. Daten, Modelle, Prompt-Sprache, Einheiten und die "
                    "Namensliste übernimmt es aus dem Hauptprojekt."
                )
            )
        )
        layout.addLayout(form)
        layout.addWidget(self.error)
        layout.addWidget(buttons)

    def gold_columns(self) -> list[str]:
        return checked_entries(self.gold)

    def consensus_column(self) -> str | None:
        return self.consensus.currentData()

    def _choose(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("Codebuch wählen"),
            "",
            self.tr("Textdateien (*.txt *.md);;Alle Dateien (*)"),
        )
        if path:
            self.codebook = Path(path)
            self.codebook_label.setText(self.codebook.name)

    def _accept(self) -> None:
        if not self.name.text().strip():
            self.error.setText(self.tr("Bitte einen Namen eingeben."))
            return
        if self.codebook is None and self.system is None:
            self.error.setText(self.tr("Bitte ein Codebuch wählen."))
            return
        self.accept()
