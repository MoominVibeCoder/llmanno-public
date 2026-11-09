"""Tab „Daten“: XLSX oder CSV wählen (auch mehrere zusammenführen), Spalten zuordnen,
importieren, Prüfbericht zeigen."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from llmanno.core import data_import
from llmanno.core.config import ColumnMapping
from llmanno.core.project import Project
from llmanno.core.qda_import import QdaImport, add_qda_gold, default_coders, read_qdpx
from llmanno.gui.common import (
    Collapsible,
    Page,
    checked_entries,
    error_label,
    error_text,
    fill_checklist,
    fill_combo,
    hint_label,
    optional_columns,
)

PREVIEW_ROWS = 50


class DataPage(Page):
    help_topics = (
        "2. Daten",
        "Datenschutz",
    )

    def __init__(self) -> None:
        super().__init__()
        self._path: Path | None = None
        self._sheet: data_import.Sheet | None = None
        # Hat ein weiteres Codesystem (core.codesystems) Läufe? Dann keine neuen Texte.
        self.other_calls: Callable[[], bool] = lambda: False

        self.choose_button = QPushButton(self.tr("Datei wählen (XLSX, CSV)…"))
        self.choose_button.clicked.connect(self._choose_file)
        self.merge_button = QPushButton(self.tr("Mehrere zusammenführen…"))
        self.merge_button.setToolTip(
            self.tr("Mehrere Dateien oder Blätter, etwa ein Transkript je Stunde, zu einer "
                    "Datei zusammenführen. Die Spalte „Quelle“ eignet sich als Gruppe.")
        )  # fmt: skip
        self.merge_button.clicked.connect(self._merge)
        self.file_label = QLabel()
        self.sheet_combo = QComboBox()
        self.sheet_combo.currentIndexChanged.connect(self._load_sheet)
        self.status_label = hint_label()

        top = QHBoxLayout()
        top.addWidget(self.choose_button)
        top.addWidget(self.merge_button)
        top.addWidget(self.file_label, 1)
        top.addWidget(QLabel(self.tr("Blatt")))
        top.addWidget(self.sheet_combo)

        self.preview = QTableWidget()
        self.preview.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        self.id_combo, self.group_combo, self.order_combo = QComboBox(), QComboBox(), QComboBox()
        self.speaker_combo, self.consensus_combo = QComboBox(), QComboBox()
        self.text_list, self.meta_list, self.gold_list = QListWidget(), QListWidget(), QListWidget()
        self.analysis_list = QListWidget()
        self.analysis_list.itemChanged.connect(self._analysis_changed)
        for widget in (self.text_list, self.meta_list, self.gold_list, self.analysis_list):
            widget.setMaximumHeight(110)
        tips = {
            self.id_combo: self.tr("Spalte mit einer eindeutigen Kennung je Zeile, etwa "
                                   "„T01-05“. Ohne ID heißen die Einheiten nach der Zeilennummer."),
            self.text_list: self.tr("Spalte mit dem Text, den das Modell kodiert. Mehrere Spalten "
                                    "werden mit ihrem Namen zu einem Text verbunden."),
            self.group_combo: self.tr("Fasst Zeilen zusammen, etwa alle Gesprächszüge eines "
                                      "Transkripts. Nötig, wenn das Modell Nachbarzeilen als "
                                      "Kontext mitlesen oder ganze Gruppen kodieren soll."),
            self.order_combo: self.tr("Reihenfolge der Zeilen in einer Gruppe, etwa eine "
                                      "Zugnummer. Ohne Angabe gilt die Zeilenfolge der Datei."),
            self.speaker_combo: self.tr("Wer spricht, etwa „L“ oder „S3“. Das Modell liest es "
                                        "zu jedem Text mit."),
            self.meta_list: self.tr("Weitere Spalten, die das Modell zu jeder Einheit mitliest, "
                                    "etwa die Unterrichtsphase. Nur wählen, was für die Kodierung "
                                    "nötig ist."),
            self.gold_list: self.tr("Spalten mit menschlichen Kodierungen, eine je Person. Gold "
                                    "ist der Maßstab, an dem die Qualität des LLM gemessen wird."),
            self.consensus_combo: self.tr("Optional: Spalte mit der gemeinsam abgestimmten "
                                          "Kodierung. Ist sie gewählt, ist sie der Hauptmaßstab; "
                                          "sonst die Mehrheit der Gold-Spalten."),
            self.analysis_list: self.tr("Merkmale wie Geschlecht oder Schulform, nach denen "
                                        "ausgewertet werden soll. Das Modell sieht sie nie; sie "
                                        "lassen sich auch nach Kodierläufen ändern."),
        }  # fmt: skip
        for widget, tip in tips.items():
            widget.setToolTip(tip)

        form = QFormLayout()
        form.addRow(self.tr("ID"), self.id_combo)
        form.addRow(self.tr("Text (kodiert)"), self.text_list)
        form.addRow(self.tr("Gruppe (z. B. Transkript)"), self.group_combo)
        form.addRow(self.tr("Sprecher:in"), self.speaker_combo)
        form.addRow(self.tr("Gold: Kodierer:innen"), self.gold_list)

        self.import_button = QPushButton(self.tr("Importieren"))
        self.import_button.clicked.connect(self._import)
        self.qda_button = QPushButton(self.tr("Gold aus QDA-Projekt…"))
        self.qda_button.clicked.connect(self._choose_qda)
        self.gold_button = QPushButton(self.tr("Gold aktualisieren"))
        self.gold_button.clicked.connect(self._update_gold)
        self.error = error_label()
        self.report = QListWidget()

        self.advanced = Collapsible()
        advanced_form = QFormLayout(self.advanced.body)
        advanced_form.setContentsMargins(0, 0, 0, 0)
        advanced_form.addRow(self.tr("Reihenfolge"), self.order_combo)
        advanced_form.addRow(self.tr("Metadaten (im Prompt)"), self.meta_list)
        advanced_form.addRow(self.tr("Gold: Konsens"), self.consensus_combo)
        advanced_form.addRow(self.tr("Analyse-Variablen (nicht im Prompt)"), self.analysis_list)
        advanced_form.addRow(self.qda_button)

        mapping = QGroupBox(self.tr("Spalten-Zuordnung"))
        mapping_layout = QVBoxLayout(mapping)
        mapping_layout.addLayout(form)
        mapping_layout.addWidget(self.advanced)
        import_row = QHBoxLayout()
        import_row.addWidget(self.import_button)
        import_row.addWidget(self.gold_button)
        mapping_layout.addLayout(import_row)
        mapping_layout.addWidget(self.error)
        mapping_layout.addWidget(QLabel(self.tr("Prüfbericht")))
        mapping_layout.addWidget(self.report)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self.status_label)
        right_layout.addWidget(self.preview)

        splitter = QSplitter()
        splitter.addWidget(mapping)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 2)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(splitter, 1)

    # --- Stand anzeigen ------------------------------------------------------------------------

    def set_project(self, project: Project | None, refresh: bool = True) -> None:
        # Eine Datei aus dem vorigen Projekt gehört nicht zu diesem.
        self._path = self._sheet = None
        self.file_label.clear()
        fill_combo(self.sheet_combo, [])
        self.report.clear()
        self.error.clear()
        super().set_project(project, refresh)

    def refresh(self) -> None:
        if self.project is None:
            return
        locked = self.project.store.count_calls() > 0 or self.other_calls()
        self._update_buttons()
        data = self.project.config.data
        if locked:
            self.status_label.setText(
                self.tr(
                    "Für dieses Projekt gibt es schon Kodierläufe; die Texte sind festgeschrieben. "
                    "Neue Daten bitte in einem neuen Projekt importieren. Gold lässt sich "
                    "nachtragen: dieselbe Datei mit neuen oder korrigierten Gold-Spalten "
                    "wählen, Gold-Spalten ankreuzen, „Gold aktualisieren“."
                )
            )
        elif data:
            self.status_label.setText(
                self.tr("Importiert: {0}, Blatt „{1}“, {2} Einheiten. Links steht die "
                        "Zuordnung; zum Ändern die Datei erneut wählen. Weiter im Tab "
                        "Codebuch.").format(data.source_name, data.sheet, data.n_units)
            )  # fmt: skip
        else:
            self.status_label.setText(
                self.tr("Noch keine Daten importiert. Erwartet wird eine Tabelle (XLSX oder CSV) "
                        "mit einer Zeile je Einheit, etwa je Gesprächszug, und den Spaltennamen "
                        "in der ersten Zeile: eine Spalte mit dem Text, am besten eine ID, "
                        "optional Gruppe (etwa Transkript), Sprecher:in und je Person eine Spalte "
                        "mit ihren Codes (Gold). „Datei wählen“ zeigt eine Vorschau; importiert "
                        "wird erst mit „Importieren“.")
            )  # fmt: skip
        self.status_label.setToolTip(
            self.tr("Prüfsumme der Datei (SHA-256): {0}").format(data.file_sha256) if data else ""
        )
        if self._sheet is None:
            self._show_units()
            # Ohne geladene Datei: die Zuordnung des Imports zeigen; Analyse-Variablen lassen
            # sich hier gleich ändern.
            units = self.project.store.units()[:1]
            self._preselect(list(units[0].source) if units else [])

    def _show_units(self) -> None:
        assert self.project is not None
        units = self.project.store.units()[:PREVIEW_ROWS]
        headers = [
            self.tr("ID"),
            self.tr("Gruppe"),
            self.tr("Reihenfolge"),
            self.tr("Sprecher:in"),
            self.tr("Text"),
        ]
        rows = [[u.unit_id, u.group, u.order, u.speaker, u.text] for u in units]
        self._fill_preview(headers, rows)

    def _fill_preview(self, headers: list[str], rows: list[list[object]]) -> None:
        self.preview.clear()
        self.preview.setColumnCount(len(headers))
        self.preview.setHorizontalHeaderLabels(headers)
        self.preview.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                self.preview.setItem(r, c, QTableWidgetItem("" if value is None else str(value)))
        self.preview.resizeColumnsToContents()

    # --- Datei und Blatt -----------------------------------------------------------------------

    def _choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, self.tr("Daten wählen"), "", self.tr("Daten (*.xlsx *.xlsm *.csv *.tsv)")
        )
        if path:
            self.open_file(Path(path))

    def _merge(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            self.tr("Dateien zum Zusammenführen wählen"),
            "",
            self.tr("Daten (*.xlsx *.xlsm *.csv *.tsv)"),
        )
        if not paths:
            return
        self.error.clear()
        try:
            sources = [(Path(p), s) for p in paths for s in data_import.sheet_names(Path(p))]
        except Exception as exc:  # openpyxl wirft je nach Defekt sehr verschiedene Fehler
            self.error.setText(self.tr("Datei nicht lesbar: {0}").format(error_text(exc)))
            return
        if len(sources) > len(paths):  # Arbeitsmappen mit mehreren Blättern: auswählen
            dialog = SourceDialog(sources, self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            sources = dialog.chosen()
        out, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("Zusammengeführte Datei speichern"),
            str(Path(paths[0]).with_name("zusammengefuehrt.xlsx")),
            self.tr("Excel-Dateien (*.xlsx)"),
        )
        if out:
            self.merge_to(sources, Path(out))

    def merge_to(self, sources: list[tuple[Path, str]], out: Path) -> None:
        self.error.clear()
        self.report.clear()
        try:
            notes = data_import.merge_sources(sources, out)
        except Exception as exc:
            self.error.setText(error_text(exc))
            return
        self.open_file(out)
        if self._sheet is None:
            return
        for combo, column in (
            (self.group_combo, data_import.SOURCE_COLUMN),
            (self.id_combo, data_import.SOURCE_ID_COLUMN),
        ):
            combo.setCurrentIndex(combo.findData(column))
        self.report.addItem(
            self.tr("{0} Quellen zusammengeführt in {1}. Vorgewählt: „Quelle“ als Gruppe, "
                    "„Quelle_ID“ als ID.").format(len(sources), out.name)
        )  # fmt: skip
        for note in notes:
            self.report.addItem(note)

    def open_file(self, path: Path) -> None:
        self.error.clear()
        try:
            names = data_import.sheet_names(path)
        except Exception as exc:  # openpyxl wirft je nach Defekt sehr verschiedene Fehler
            self.error.setText(self.tr("Datei nicht lesbar: {0}").format(error_text(exc)))
            return
        self._path = path
        self.file_label.setText(str(path))
        data = self.project.config.data if self.project else None
        preferred = data.sheet if data and data.source_name == path.name else None
        fill_combo(self.sheet_combo, ((n, n) for n in names), preferred)
        self._load_sheet()

    def _load_sheet(self) -> None:
        if self._path is None:
            return
        try:
            self._sheet = data_import.read_sheet(self._path, self.sheet_combo.currentData())
        except Exception as exc:
            self._sheet = None
            self.error.setText(self.tr("Blatt nicht lesbar: {0}").format(error_text(exc)))
            self.refresh()
            return
        header = self._sheet.header
        self._fill_preview(header, [values for _, values in self._sheet.rows[:PREVIEW_ROWS]])
        self.status_label.setText(
            self.tr("Vorschau: {0} Datenzeilen, {1} Spalten").format(
                len(self._sheet.rows), len(header)
            )
        )
        self._preselect(header)
        self._update_buttons()

    def _update_buttons(self) -> None:
        """Nach Kodierläufen bleiben nur die Gold-Spalten änderbar."""
        project = self.project
        locked = project is not None and (project.store.count_calls() > 0 or self.other_calls())
        has_data = project is not None and project.config.data is not None
        self.import_button.setEnabled(
            project is not None and self._sheet is not None and not locked
        )
        self.gold_button.setEnabled(self._sheet is not None and has_data)
        self.qda_button.setEnabled(has_data)
        if locked:
            reason = self.tr("Nach Kodierläufen sind die Texte festgeschrieben; neue Daten in "
                             "einem neuen Projekt importieren.")  # fmt: skip
        elif self._sheet is None:
            reason = self.tr("Erst „Datei wählen“.")
        else:
            reason = self.tr("Liest die gewählten Spalten ein und prüft sie (Prüfbericht).")
        self.import_button.setToolTip(reason)
        self.gold_button.setToolTip(
            self.tr("Gold-Spalten nachtragen oder korrigieren, auch nach Kodierläufen. Die "
                    "Texte müssen gleich bleiben; Lauf-Identitäten und Freeze bleiben.")
            if self._sheet is not None and has_data
            else self.tr("Erst Daten importieren, dann dieselbe Datei mit den neuen "
                         "Gold-Spalten wählen.")
        )  # fmt: skip
        self.qda_button.setToolTip(
            self.tr("Kodierungen aus MAXQDA, ATLAS.ti, QualCoder o. Ä. als Gold übernehmen: "
                    "Projekt mit „REFI-QDA-Projekt exportieren“ ausgeben, dort kodieren, "
                    "als .qdpx zurückexportieren und hier wählen.")
            if has_data
            else self.tr("Erst Daten importieren.")
        )  # fmt: skip
        for widget in (
            self.id_combo,
            self.text_list,
            self.group_combo,
            self.order_combo,
            self.speaker_combo,
            self.meta_list,
        ):
            widget.setEnabled(not locked)  # fmt: skip

    def _preselect(self, header: list[str]) -> None:
        data = self.project.config.data if self.project else None
        mapping = data.mapping if data and set(data.mapping.columns()) <= set(header) else None
        lower = {c.lower(): c for c in header}
        guess_id = next((lower[n] for n in ("id", "nr", "nummer") if n in lower), None)
        guess_text = next((c for c in header if "text" in c.lower()), None)

        def pick(field: str, guess: str | None = None) -> str | None:
            return getattr(mapping, field) if mapping else guess

        fill_combo(self.id_combo, optional_columns(header), pick("id", guess_id))
        fill_combo(self.group_combo, optional_columns(header), pick("group"))
        fill_combo(self.order_combo, optional_columns(header), pick("order"))
        fill_combo(self.speaker_combo, optional_columns(header), pick("speaker"))
        fill_combo(self.consensus_combo, optional_columns(header), pick("consensus"))
        fill_checklist(
            self.text_list, header, mapping.text if mapping else [guess_text] if guess_text else []
        )
        fill_checklist(self.meta_list, header, mapping.meta if mapping else [])
        fill_checklist(self.gold_list, header, mapping.gold if mapping else [])
        fill_checklist(self.analysis_list, header, mapping.analysis if mapping else [])
        for widget in (self.text_list, self.meta_list, self.gold_list, self.analysis_list):
            items = [widget.item(i) for i in range(widget.count())]
            checked = [i for i in items if i.checkState() == Qt.CheckState.Checked]
            if checked:
                widget.scrollToItem(checked[0])  # Gewähltes sichtbar, auch weiter unten
        # Belegte Felder im zugeklappten Bereich nennen: Metadaten gehen etwa in den Prompt.
        used = [
            name
            for name, value in (
                (self.tr("Reihenfolge"), mapping and mapping.order),
                (self.tr("Metadaten"), mapping and mapping.meta),
                (self.tr("Konsens"), mapping and mapping.consensus),
                (self.tr("Analyse-Variablen"), mapping and mapping.analysis),
            )
            if value
        ]
        label = self.tr("Erweitert (belegt: {0})").format(", ".join(used))
        self.advanced.toggle.setText(label if used else self.tr("Erweitert"))

    # --- Import --------------------------------------------------------------------------------

    def mapping(self) -> ColumnMapping:
        return ColumnMapping(
            id=self.id_combo.currentData(),
            text=checked_entries(self.text_list),
            group=self.group_combo.currentData(),
            order=self.order_combo.currentData(),
            speaker=self.speaker_combo.currentData(),
            meta=checked_entries(self.meta_list),
            gold=checked_entries(self.gold_list),
            analysis=checked_entries(self.analysis_list),
            consensus=self.consensus_combo.currentData(),
        )

    def _import(self) -> None:
        assert self.project is not None and self._path is not None
        self.error.clear()
        self.report.clear()
        if not checked_entries(self.text_list):
            self.error.setText(self.tr("Mindestens eine Text-Spalte auswählen."))
            return
        try:
            report = self.project.import_data(
                self._path, self.sheet_combo.currentData(), self.mapping()
            )
        except Exception as exc:
            self.error.setText(error_text(exc))
            return
        warnings = sum(1 for i in report.issues if i.severity == "warning")
        self.report.addItem(
            self.tr("{0} Einheiten importiert, {1} Zeilen übersprungen, {2} Warnungen.").format(
                len(report.units), report.n_skipped, warnings
            )
        )
        for issue in report.issues[:1000]:
            where = self.tr("Zeile {0}").format(issue.row) if issue.row else self.tr("Allgemein")
            kind = self.tr("Fehler") if issue.severity == "error" else self.tr("Warnung")
            self.report.addItem(f"{kind} · {where}: {issue.message}")
        self.report.addItem(self.tr("Weiter im Tab Codebuch: Codebuch laden."))
        self._sheet = None
        self.changed.emit()
        self.refresh()

    def _update_gold(self) -> None:
        assert self.project is not None and self._path is not None
        self.error.clear()
        self.report.clear()
        try:
            self.project.update_gold(
                self._path,
                self.sheet_combo.currentData(),
                checked_entries(self.gold_list),
                self.consensus_combo.currentData(),
                checked_entries(self.analysis_list),
            )
        except Exception as exc:
            self.error.setText(error_text(exc))
            return
        details = self.project.store.audit()[-1].details
        self.report.addItem(
            self.tr(
                "Gold aktualisiert: {0} Einheiten mit geändertem Gold. Lauf-Identitäten und "
                "Freeze bleiben; die Änderung steht im Protokoll."
            ).format(details["n_changed"])
        )
        if details["dropped"]:
            self.report.addItem(
                self.tr("Nicht mehr als Gold geführt: {0}").format(", ".join(details["dropped"]))
            )
        if details["backup"]:
            self.report.addItem(
                self.tr("Die bisherige Datenkopie liegt als {0} im Projektordner.").format(
                    details["backup"]
                )
            )
        self._sheet = None
        self.changed.emit()
        self.refresh()

    def _choose_qda(self) -> None:
        if self.project is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, self.tr("QDA-Projekt wählen"), "", self.tr("REFI-QDA-Projekt (*.qdpx)")
        )
        if not path:
            return
        self.error.clear()
        try:
            qda = read_qdpx(Path(path), self.project)
        except Exception as exc:  # beschädigtes XML, unbekannte Struktur …
            self.error.setText(error_text(exc))
            return
        dialog = CoderDialog(qda, default_coders(qda, self.project), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.add_qda(qda, dialog.chosen())

    def add_qda(self, qda: QdaImport, coders: list[str]) -> None:
        assert self.project is not None
        self.error.clear()
        self.report.clear()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out = self.project.folder / "exports" / f"daten_mit_qda_{stamp}.xlsx"
        try:
            add_qda_gold(self.project, qda, coders, out)
        except Exception as exc:
            self.error.setText(error_text(exc))
            return
        units = len(set().union(*(qda.coders[c].overlap for c in coders)))
        self.report.addItem(
            self.tr("Gold aus dem QDA-Projekt übernommen: {0} ({1} Einheiten mit Kodierung). "
                    "Lauf-Identitäten und Freeze bleiben; die Daten mit den neuen Spalten "
                    "liegen in {2}.").format(", ".join(coders), units, out.name)
        )  # fmt: skip
        if qda.unknown_codes:
            listed = ", ".join(f"{name} ({n}×)" for name, n in qda.unknown_codes.most_common())
            self.report.addItem(
                self.tr("Codes ohne Gegenstück im Codebuch, nicht übernommen: {0}").format(listed)
            )
        self._sheet = None
        self.changed.emit()
        self.refresh()

    def _analysis_changed(self) -> None:
        """Ohne geladene Datei gilt die Auswahl sofort; sie ändert keinen Prompt."""
        data = self.project.config.data if self.project else None
        if self._sheet is not None or data is None:
            return
        mapping = data.mapping.model_copy(update={"analysis": checked_entries(self.analysis_list)})
        self.project.update(data=data.model_copy(update={"mapping": mapping}))
        self.changed.emit()


class CoderDialog(QDialog):
    """Welche Personen aus dem QDA-Projekt als Gold übernehmen? Vorausgewählt sind neue
    menschliche Kodierende (nicht das LLM, keine schon vorhandene Gold-Spalte)."""

    def __init__(self, qda: QdaImport, chosen: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Kodierende wählen"))
        self._names = list(qda.coders)
        self.list = QListWidget()
        for name in self._names:
            coder = qda.coders[name]
            text = self.tr("{0}: {1} Kodierungen, {2} Einheiten").format(
                name, coder.codings, len(coder.overlap)
            )
            item = QListWidgetItem(text)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            checked = Qt.CheckState.Checked if name in chosen else Qt.CheckState.Unchecked
            item.setCheckState(checked)
            self.list.addItem(item)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(self.tr("Als Gold-Spalte übernehmen:")))
        layout.addWidget(self.list)
        layout.addWidget(buttons)

    def chosen(self) -> list[str]:
        return [
            name
            for row, name in enumerate(self._names)
            if self.list.item(row).checkState() == Qt.CheckState.Checked
        ]


class SourceDialog(QDialog):
    """Welche Blätter zusammenführen? Arbeitsmappen haben oft Zusatzblätter (Legende o. Ä.)."""

    def __init__(self, sources: list[tuple[Path, str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Blätter wählen"))
        self._sources = sources
        self.list = QListWidget()
        for path, name in sources:
            item = QListWidgetItem(f"{path.name} › {name}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.list.addItem(item)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(self.tr("Diese Blätter zusammenführen:")))
        layout.addWidget(self.list)
        layout.addWidget(buttons)

    def chosen(self) -> list[tuple[Path, str]]:
        return [
            source
            for row, source in enumerate(self._sources)
            if self.list.item(row).checkState() == Qt.CheckState.Checked
        ]
