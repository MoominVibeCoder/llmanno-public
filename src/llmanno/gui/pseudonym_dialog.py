"""Namensliste für die Pseudonymisierung vor dem Senden (core.pseudonyms)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from llmanno.core import data_import
from llmanno.core.project import Project
from llmanno.core.pseudonyms import (
    SHORT,
    Pseudonymizer,
    check_pairs,
    is_list_file,
    next_pseudonyms,
    normalize_name,
    read_pairs,
)
from llmanno.gui.common import error_label, error_text, hint_label


class PseudonymDialog(QDialog):
    def __init__(
        self,
        project: Project,
        parent: QWidget | None = None,
        *,
        start_empty: bool = False,
        also_frozen: list[str] | None = None,
    ) -> None:
        """`also_frozen`: eingefrorene weitere Codesysteme; die Liste gilt auch für sie."""
        super().__init__(parent)
        self.setWindowTitle(self.tr("Namen vor dem Senden ersetzen"))
        self.resize(560, 480)
        self._project = project
        units = project.store.units()
        self._speakers = list(dict.fromkeys(u.speaker for u in units if u.speaker))
        # Alles, was in den Prompt geht und Namen tragen kann.
        self._texts = [u.text for u in units] + [u.speaker for u in units if u.speaker]
        self._texts += [v for u in units for v in u.meta.values() if isinstance(v, str)]

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(
            [self.tr("Original"), self.tr("Ersatz"), self.tr("Treffer")]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeaderItem(2).setToolTip(
            self.tr("Wie oft der Name in Text, Sprecher:in und Metadaten vorkommt, genau in dieser "
                    "Schreibweise. In Klammern: Treffer in anderer Groß- und Kleinschreibung; "
                    "sie werden nicht ersetzt.")
        )  # fmt: skip
        self.table.itemChanged.connect(self._count)
        for original, replacement in [] if start_empty else project.pseudonym_pairs:
            self._add(original, replacement)

        add = QPushButton(self.tr("Zeile hinzufügen"))
        add.clicked.connect(lambda: self._add("", next_pseudonyms(self.pairs(), 1)[0]))
        remove = QPushButton(self.tr("Zeile entfernen"))
        remove.clicked.connect(self._remove)
        speakers = QPushButton(self.tr("Sprecher übernehmen"))
        speakers.setToolTip(
            self.tr("Trägt alle Werte der Sprecher-Spalte ein, die noch fehlen. Kürzel bis {0} "
                    "Zeichen (etwa „L“ oder „S1“) bleiben weg: Sie sind meist Rollen, und "
                    "ersetzt würde jedes gleiche Wort im Text.").format(SHORT)
        )  # fmt: skip
        speakers.setEnabled(bool(self._speakers))
        speakers.clicked.connect(self._add_speakers)
        load = QPushButton(self.tr("Aus Datei…"))
        load.setToolTip(
            self.tr("XLSX, CSV oder TSV: erste Spalte Original, zweite Ersatz (leer: Person N); "
                    "erste Zeile sind Überschriften.")
        )  # fmt: skip
        load.clicked.connect(self._load)
        row = QHBoxLayout()
        for button in (add, remove, speakers, load):
            row.addWidget(button)
        row.addStretch()

        self.error = error_label()
        self.warning = error_label()
        warnings = []
        if project.config.coding.pseudonyms_sha256 and not project.pseudonymizer_ready():
            warnings.append(
                self.tr("Die gespeicherte Namensliste fehlt oder wurde außerhalb geändert. Eine "
                        "neue Liste ändert die Lauf-Identität: Alle Modelle kodieren neu.")
            )  # fmt: skip
        if also_frozen:
            warnings.append(
                self.tr("Die Liste gilt auch für die eingefrorenen Codesysteme {0}; eine "
                        "Änderung bricht auch deren Freeze.").format(", ".join(also_frozen))
            )  # fmt: skip
        if project.config.freeze is not None:
            warnings.append(
                self.tr("Das Instrument ist eingefroren. Eine geänderte Liste bricht den "
                        "Freeze; er muss dann begründet aufgehoben werden.")
            )  # fmt: skip
        self.warning.setText("\n".join(warnings))
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        intro = hint_label(
            self.tr(
                "Ersetzt Namen in Text, Sprecher:in und Metadaten, bevor sie an das Modell gehen: "
                "nur ganze Wörter, auch mit Genitiv-s („Lenas“). Codebuch und IDs bleiben "
                "unverändert; Namen dort vorher entfernen. Die Liste bleibt im Projektordner. "
                "Eine geänderte Liste führt beim nächsten Lauf zu einer neuen Kodierung."
            )
        )
        intro.setToolTip(
            self.tr("Groß- und Kleinschreibung zählt. Dateien im Projektordner: "
                    "pseudonyme.tsv, frühere Fassungen in pseudonyme_alt; ins "
                    "Replikationspaket kommt sie nur mit den Rohtexten. Leere Liste: keine "
                    "Ersetzung.")
        )  # fmt: skip
        layout.addWidget(intro)
        layout.addWidget(self.warning)
        layout.addWidget(self.table)
        layout.addLayout(row)
        layout.addWidget(self.error)
        layout.addWidget(buttons)
        self._count()

    def pairs(self) -> list[tuple[str, str]]:
        def cell(row: int, col: int) -> str:
            item = self.table.item(row, col)
            return item.text() if item else ""

        return [(cell(r, 0), cell(r, 1)) for r in range(self.table.rowCount())]

    def _add(self, original: str, replacement: str) -> None:
        self.table.blockSignals(True)
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(original))
        self.table.setItem(row, 1, QTableWidgetItem(replacement))
        hits = QTableWidgetItem("")
        hits.setFlags(hits.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, 2, hits)
        self.table.blockSignals(False)

    def _remove(self) -> None:
        for row in sorted({i.row() for i in self.table.selectedItems()}, reverse=True):
            self.table.removeRow(row)
        self._count()

    def _add_missing(self, originals: list[tuple[str, str]]) -> None:
        known = {o for o, _ in self.pairs()}
        new = [(o, r) for o, r in originals if o and o not in known]
        free = iter(next_pseudonyms(self.pairs(), len(new)))
        for original, replacement in new:
            self._add(original, replacement or next(free))
        self._count()

    def _add_speakers(self) -> None:
        # Kürzel wie „I“, „B“, „L“ sind Rollen; ersetzt würde jedes solche Wort im Text.
        self._add_missing([(s, "") for s in self._speakers if len(s.strip()) > SHORT])

    def _load(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, self.tr("Namensliste wählen"), "", self.tr("Tabellen (*.xlsx *.csv *.tsv)")
        )
        if not path:
            return
        if is_list_file(Path(path)):  # Namensliste eines anderen Projekts
            try:
                self._add_missing(read_pairs(Path(path)))
            except ValueError as exc:
                self.error.setText(error_text(exc))
            return
        try:
            sheet = data_import.read_sheet(Path(path))
        except Exception as exc:
            self.error.setText(error_text(exc))
            return

        def text(cells: list, index: int) -> str:
            value = cells[index] if index < len(cells) else None
            return "" if value is None else str(value).strip()

        self._add_missing([(text(cells, 0), text(cells, 1)) for _, cells in sheet.rows])

    def _count(self) -> None:
        pairs = [(o.strip(), r.strip()) for o, r in self.pairs()]
        valid = tuple(p for p in pairs if p[0] and p[1])
        names = Pseudonymizer(valid)
        exact = names.counts(self._texts) if valid else {}
        anyhow = names.counts(self._texts, ignore_case=True) if valid else {}
        self.table.blockSignals(True)
        for row, (original, _) in enumerate(pairs):
            item = self.table.item(row, 2)
            key = normalize_name(original)
            if item is None:
                continue
            text = str(exact[key]) if key in exact else ""
            if (other := anyhow.get(key, 0) - exact.get(key, 0)) > 0:
                text += " " + self.tr("(+{0} anders geschrieben)").format(other)
            item.setText(text)
        self.table.blockSignals(False)

    def _accept(self) -> None:
        try:
            check_pairs(self.pairs())
        except ValueError as exc:
            self.error.setText(error_text(exc))
            return
        self.accept()
