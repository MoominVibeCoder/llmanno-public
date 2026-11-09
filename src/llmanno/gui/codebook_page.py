"""Tab „Codebuch“: Codebuch laden, Codes vorschlagen lassen, Code-Liste festlegen."""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from llmanno.core import codebook
from llmanno.core.config import CodebookConfig
from llmanno.core.history import codebook_history
from llmanno.core.prompt import estimate_tokens
from llmanno.gui.common import (
    Collapsible,
    Page,
    error_label,
    error_text,
    fill_combo,
    hint_label,
)
from llmanno.gui.disagreement_dialog import DisagreementDialog
from llmanno.gui.gold_mapping import GoldMappingBox

_ID = re.compile(r"^[A-Za-z0-9_.\-]+$")
COLUMNS = ("id", "label", "parent", "rank", "active")


class CodebookPage(Page):
    help_topics = ("3. Codebuch",)

    def __init__(self) -> None:
        super().__init__()
        self.load_button = QPushButton(self.tr("Codebuch laden (.txt, .md)…"))
        self.load_button.clicked.connect(self._choose_file)
        self.disagree_button = QPushButton(self.tr("Unstimmigkeiten prüfen…"))
        self.disagree_button.setToolTip(
            self.tr("Wo weichen die Modelle vom Gold oder voneinander ab? Code-Paare mit Fällen "
                    "und Begründungen, auf Wunsch mit einem LLM-Vorschlag für eine schärfere "
                    "Abgrenzung. Nur Übungsteil (Dev), sobald es eine Aufteilung gibt.")
        )  # fmt: skip
        self.disagree_button.clicked.connect(self._disagreements)
        self.disagree_button.setEnabled(False)  # erst mit Projekt, Codebuch und Panel
        self.info_label = hint_label()
        self.text_view = QPlainTextEdit()
        self.text_view.setReadOnly(True)
        self.text_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        top.addWidget(self.load_button)
        top.addWidget(self.disagree_button)
        top.addStretch()
        left_layout.addLayout(top)
        left_layout.addWidget(self.info_label)
        left_layout.addWidget(self.text_view, 1)
        self.gold_box = GoldMappingBox()
        self.gold_box.changed.connect(self.changed)
        left_layout.addWidget(self.gold_box)

        self.preset_combo = QComboBox()
        fill_combo(
            self.preset_combo,
            [
                (self.tr("Automatisch"), "auto"),
                (self.tr("Markdown-Überschriften (### K1 — Name)"), "markdown"),
                (self.tr("Zeilen mit Code:/Kategorie:"), "prefix"),
                (self.tr("Nummerierung (1.2 Name)"), "numbered"),
                (self.tr("Eigener regulärer Ausdruck"), "custom"),
            ],
        )
        self.preset_combo.setToolTip(
            self.tr("Wie „Codes vorschlagen“ die Codes im Codebuch findet. „Automatisch“ erkennt "
                    "die üblichen Formen; ein eigener regulärer Ausdruck nur, wenn nichts passt.")
        )  # fmt: skip
        self.regex_edit = QLineEdit()
        self.regex_edit.setPlaceholderText(r"^(?P<code>\w+)\s+(?P<label>.+)$")
        self.regex_edit.setToolTip(
            self.tr("Regulärer Ausdruck, der je Zeile des Codebuchs geprüft wird: Gruppe "
                    "(?P<code>…) für das Kürzel, optional (?P<label>…) für den Namen.")
        )  # fmt: skip
        self.suggest_button = QPushButton(self.tr("Codes vorschlagen"))
        self.suggest_button.setToolTip(
            self.tr("Sucht im Codebuch nach Codes (Muster unter „Erweitert“) und ergänzt die "
                    "Liste; vorhandene Zeilen bleiben.")
        )  # fmt: skip
        self.suggest_button.clicked.connect(self._suggest)
        self.gold_button = QPushButton(self.tr("Aus Gold-Spalten ergänzen"))
        self.gold_button.clicked.connect(self._suggest_from_gold)

        suggest_row = QHBoxLayout()
        suggest_row.addWidget(self.suggest_button)
        suggest_row.addWidget(self.gold_button)
        suggest_row.addStretch()

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(
            [
                self.tr("Kürzel"),
                self.tr("Label"),
                self.tr("Gruppe"),
                self.tr("Rang"),
                self.tr("Aktiv"),
            ]  # fmt: skip
        )
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setColumnWidth(1, 280)
        header_tips = (
            self.tr("Das Kürzel, das das Modell ausgibt und das in den Gold-Spalten steht, etwa "
                    "K1. Erlaubt sind Buchstaben, Ziffern, _ . -"),
            self.tr("Kurzer Name des Codes; steht mit dem Kürzel in der Liste der erlaubten "
                    "Codes. Die Definition kommt aus dem Codebuch."),
            self.tr("Optional: Oberkategorie, etwa ein Diskursmodus. Die Evaluation rechnet "
                    "dann auch auf Ebene der Gruppen; in den Prompt geht sie nicht."),
            self.tr("Nur bei ordinalem Skalenniveau nötig: Stufe als ganze Zahl, etwa 1 für "
                    "niedrig. Die Kennwerte werten dann, wie weit Codes auseinanderliegen."),
            self.tr("Nur aktive Codes darf das Modell vergeben, nur sie werden ausgewertet. "
                    "Abwählen statt löschen, wenn ein Code vorerst wegfallen soll."),
        )  # fmt: skip
        for column, tip in enumerate(header_tips):
            self.table.horizontalHeaderItem(column).setToolTip(tip)
        self.table.itemChanged.connect(self._apply)

        add_button = QPushButton(self.tr("Zeile hinzufügen"))
        add_button.clicked.connect(self._add_row)
        remove_button = QPushButton(self.tr("Zeilen entfernen"))
        remove_button.clicked.connect(self._remove_rows)
        up_button = QPushButton("↑")
        up_button.setToolTip(
            self.tr("Gewählte Zeile nach oben. Die Reihenfolge der Liste ist die im Prompt, "
                    "sofern sie dort nicht zufällig gemischt wird.")
        )  # fmt: skip
        up_button.clicked.connect(lambda: self._move(-1))
        down_button = QPushButton("↓")
        down_button.setToolTip(self.tr("Gewählte Zeile nach unten."))
        down_button.clicked.connect(lambda: self._move(1))
        row_buttons = QHBoxLayout()
        for button in (add_button, remove_button, up_button, down_button):
            row_buttons.addWidget(button)
        row_buttons.addStretch()

        self.scale_combo = QComboBox()
        fill_combo(
            self.scale_combo, [(self.tr("nominal"), "nominal"), (self.tr("ordinal"), "ordinal")]
        )
        self.scale_combo.setToolTip(
            self.tr("nominal: Codes ohne Rangfolge (Standard). ordinal: Die Codes bilden Stufen, "
                    "etwa niedrig, mittel, hoch; dann braucht jeder Code einen Rang, und eine "
                    "Abweichung um eine Stufe wiegt weniger als um mehrere.")
        )  # fmt: skip
        self.scale_combo.currentIndexChanged.connect(self._apply)
        self.abstain_check = QCheckBox(self.tr("Restkategorie „kein Code passt“ anbieten"))
        self.abstain_check.setToolTip(
            self.tr("Erlaubt dem Modell zu sagen, dass kein Code passt, statt einen zu erzwingen "
                    "(Restkategorie, Kürzel KEIN_CODE).")
        )  # fmt: skip
        self.abstain_check.toggled.connect(self._apply)
        self.abstain_id_edit = QLineEdit()
        self.abstain_id_edit.setToolTip(
            self.tr("So heißt die Restkategorie in Antworten und Auswertung. Ändern, wenn die "
                    "Gold-Spalten ein anderes Kürzel verwenden, etwa „99“.")
        )  # fmt: skip
        self.abstain_id_edit.editingFinished.connect(self._apply)
        self.abstain_label_edit = QLineEdit()
        self.abstain_label_edit.setToolTip(
            self.tr("Erklärt dem Modell, wann die Restkategorie gilt.")
        )
        self.abstain_label_edit.editingFinished.connect(self._apply)
        advanced = Collapsible()
        self.advanced_form = QFormLayout(advanced.body)
        self.advanced_form.setContentsMargins(0, 0, 0, 0)
        self.advanced_form.addRow(self.tr("Muster für „Codes vorschlagen“"), self.preset_combo)
        self.advanced_form.addRow(self.tr("Eigener Ausdruck"), self.regex_edit)
        self.advanced_form.setRowVisible(self.regex_edit, False)
        self.preset_combo.currentIndexChanged.connect(
            lambda: self.advanced_form.setRowVisible(
                self.regex_edit, self.preset_combo.currentData() == "custom"
            )
        )
        self.advanced_form.addRow(self.tr("Skalenniveau"), self.scale_combo)
        self.advanced_form.addRow(self.tr("Kürzel der Restkategorie"), self.abstain_id_edit)
        self.advanced_form.addRow(self.tr("Beschreibung im Prompt"), self.abstain_label_edit)
        options = QVBoxLayout()
        options.addWidget(self.abstain_check)
        options.addWidget(advanced)

        self.error = error_label()
        self.status = hint_label()
        self.codes_box = QGroupBox(
            self.tr("Code-Liste: nur diese Codes vergibt das Modell, nur sie werden ausgewertet")
        )
        codes_layout = QVBoxLayout(self.codes_box)
        codes_layout.addLayout(suggest_row)
        codes_layout.addWidget(self.table, 1)
        codes_layout.addLayout(row_buttons)
        codes_layout.addLayout(options)
        self.issues = error_label()
        codes_layout.addWidget(self.issues)
        codes_layout.addWidget(self.status)
        codes_layout.addWidget(self.error)

        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(self.codes_box)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        layout = QVBoxLayout(self)
        layout.addWidget(splitter)

    # --- Stand anzeigen ------------------------------------------------------------------------

    def _disagreements(self) -> None:
        if self.project is not None:
            DisagreementDialog(self.project, self).exec()
            self.changed.emit()  # der Dialog speichert vor einem Vorschlag

    def refresh(self) -> None:
        if self.project is None:
            self.disagree_button.setEnabled(False)
            return
        cb = self.project.config.codebook
        text = self.project.codebook_text
        self.gold_box.refresh(self.project)
        self.codes_box.setEnabled(cb is not None and text is not None)
        self.disagree_button.setEnabled(cb is not None and bool(self.project.config.panel))
        has_gold = bool(self.project.config.data and self.project.config.data.mapping.gold)
        self.gold_button.setEnabled(has_gold)
        self.gold_button.setToolTip(
            self.tr("Übernimmt Werte aus den Gold-Spalten, die noch nicht in der Liste stehen; "
                    "das Label danach ergänzen.")
            if has_gold
            else self.tr("Erst Gold-Spalten zuordnen (Tab Daten).")
        )  # fmt: skip
        if cb is None or text is None:
            self.info_label.setText(
                self.tr("Noch kein Codebuch geladen: „Codebuch laden“ wählen (.txt oder .md). Der "
                        "Text geht unverändert an das Modell; die Codes schlägt das Programm "
                        "danach vor.")
            )  # fmt: skip
            self.info_label.setToolTip("")
            self.text_view.clear()
            self._fill_table([])
            return
        versions = len(codebook_history(self.project))
        self.info_label.setText(
            self.tr("{0} · etwa {1} Token").format(cb.source_name, estimate_tokens(text))
            + (" · " + self.tr("Fassung {0}").format(versions) if versions > 1 else "")
        )
        self.info_label.setToolTip(
            self.tr("Token sind Textbausteine, nach denen Anbieter abrechnen; die Zahl ist eher "
                    "zu hoch geschätzt. Zeichenkodierung: {0}. Prüfsumme (SHA-256): {1}").format(
                cb.encoding, cb.sha256
            )
        )  # fmt: skip
        if self.text_view.toPlainText() != text:
            self.text_view.setPlainText(text)
        self._fill_table([c.model_dump() for c in cb.codes])
        for widget in (
            self.scale_combo,
            self.abstain_check,
            self.abstain_id_edit,
            self.abstain_label_edit,
        ):
            widget.blockSignals(True)
        self.scale_combo.setCurrentIndex(self.scale_combo.findData(cb.scale))
        self.abstain_check.setChecked(cb.abstain)
        self.abstain_id_edit.setText(cb.abstain_id)
        self.abstain_label_edit.setText(cb.abstain_label)
        for widget in (
            self.scale_combo,
            self.abstain_check,
            self.abstain_id_edit,
            self.abstain_label_edit,
        ):
            widget.blockSignals(False)
        self.error.clear()
        self._show_issues()

    def _show_issues(self) -> None:
        cb, text = self.project.config.codebook, self.project.codebook_text
        issues = codebook.code_list_issues(cb, text) if cb and text else []
        self.issues.setText("\n".join(issues))
        empty = self.tr("Noch keine aktiven Codes: „Codes vorschlagen“ oder Zeilen von Hand "
                        "eintragen. Änderungen an der Liste gelten sofort.")  # fmt: skip
        if cb is not None and not cb.active_codes():
            self.status.setText(empty)
        elif self.status.text() == empty:
            self.status.clear()

    def _fill_table(self, codes: list[dict]) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for code in codes:
            self._insert_row(code)
        self.table.blockSignals(False)

    def _insert_row(self, code: dict) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(code.get("id", "")))
        self.table.setItem(row, 1, QTableWidgetItem(code.get("label", "")))
        self.table.setItem(row, 2, QTableWidgetItem(code.get("parent") or ""))
        rank = code.get("rank")
        self.table.setItem(row, 3, QTableWidgetItem("" if rank is None else str(rank)))
        active = QTableWidgetItem()
        active.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        active.setCheckState(
            Qt.CheckState.Checked if code.get("active", True) else Qt.CheckState.Unchecked
        )
        self.table.setItem(row, 4, active)

    # --- Laden und Vorschläge ------------------------------------------------------------------

    def _choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("Codebuch wählen"),
            "",
            self.tr("Textdateien (*.txt *.md);;Alle Dateien (*)"),
        )
        if path:
            self.load_file(Path(path))

    def load_file(self, path: Path) -> None:
        assert self.project is not None
        try:
            self.project.load_codebook(path)
        except Exception as exc:
            self.error.setText(error_text(exc))
            return
        self.changed.emit()
        self.refresh()
        self.suggest_if_empty()

    def suggest_if_empty(self) -> None:
        """Ein frisch geladenes Codebuch ohne Code-Liste: gleich Codes vorschlagen."""
        cb = self.project.config.codebook if self.project else None
        if cb is not None and not cb.codes and self.project.codebook_text:
            self._suggest()

    def _suggest(self) -> None:
        assert self.project is not None
        text = self.project.codebook_text or ""
        preset = self.preset_combo.currentData()
        try:
            if preset == "auto":
                found = codebook.suggest_auto(text)
            else:
                found = codebook.suggest(
                    text, self.regex_edit.text() if preset == "custom" else preset
                )
        except (ValueError, re.error) as exc:
            self.error.setText(self.tr("Ausdruck ungültig: {0}").format(exc))
            return
        added = self._merge([{"id": s.id, "label": s.label} for s in found])
        self.status.setText(
            self.tr("{0} Codes vorgeschlagen, {1} neu übernommen. Bitte prüfen.").format(
                len(found), added
            )
            if found
            else self.tr(
                "Keine Codes gefunden. Anderes Muster wählen oder Codes von Hand eintragen."
            )
        )

    def _suggest_from_gold(self) -> None:
        assert self.project is not None
        values = [v for unit in self.project.store.units() for v in unit.gold.values()]
        known = {self.table.item(r, 0).text() for r in range(self.table.rowCount())}
        cfg = self.project.config
        separator = cfg.gold.separator if cfg.mode == "multi" else None
        found = codebook.gold_suggestions(values, known, separator=separator)
        added = self._merge([{"id": s.id, "label": ""} for s in found])
        self.status.setText(self.tr("{0} Codes aus den Gold-Spalten ergänzt.").format(added))

    def _merge(self, codes: list[dict]) -> int:
        known = {self.table.item(r, 0).text() for r in range(self.table.rowCount())}
        new = [c for c in codes if c["id"] not in known]
        self.table.blockSignals(True)
        for code in new:
            self._insert_row(code)
        self.table.blockSignals(False)
        self._apply()
        return len(new)

    # --- Tabelle bearbeiten --------------------------------------------------------------------

    def _add_row(self) -> None:
        self.table.blockSignals(True)
        self._insert_row({})
        self.table.blockSignals(False)
        self.table.editItem(self.table.item(self.table.rowCount() - 1, 0))

    def _remove_rows(self) -> None:
        for row in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(row)
        self._apply()

    def _move(self, step: int) -> None:
        row = self.table.currentRow()
        target = row + step
        if row < 0 or not 0 <= target < self.table.rowCount():
            return
        codes = self._rows()
        codes[row], codes[target] = codes[target], codes[row]
        self._fill_table(codes)
        self.table.selectRow(target)
        self._apply()

    def _rows(self) -> list[dict]:
        rows = []
        for r in range(self.table.rowCount()):
            rank_text = self.table.item(r, 3).text().strip()
            rows.append(
                {
                    "id": self.table.item(r, 0).text().strip(),
                    "label": self.table.item(r, 1).text().strip(),
                    "parent": self.table.item(r, 2).text().strip() or None,
                    "rank": rank_text,
                    "active": self.table.item(r, 4).checkState() == Qt.CheckState.Checked,
                }
            )
        return rows

    def _apply(self) -> None:
        if self.project is None or self.project.config.codebook is None:
            return
        try:
            codes = []
            for number, row in enumerate(self._rows(), start=1):
                if not _ID.match(row["id"]):
                    raise ValueError(
                        self.tr(
                            "Zeile {0}: Kürzel „{1}“ darf nur Buchstaben, Ziffern, _ . - enthalten."
                        ).format(number, row["id"])
                    )
                if row["rank"] and not row["rank"].lstrip("-").isdigit():
                    raise ValueError(
                        self.tr("Zeile {0}: Rang muss eine ganze Zahl sein.").format(number)
                    )
                codes.append({**row, "rank": int(row["rank"]) if row["rank"] else None})
            updated = CodebookConfig.model_validate(
                {
                    **self.project.config.codebook.model_dump(),
                    "codes": codes,
                    "scale": self.scale_combo.currentData(),
                    "abstain": self.abstain_check.isChecked(),
                    "abstain_id": self.abstain_id_edit.text().strip() or "KEIN_CODE",
                    "abstain_label": self.abstain_label_edit.text().strip(),
                }
            )
            self.project.update(codebook=updated)
        except Exception as exc:
            self.error.setText(error_text(exc))
            return
        self.error.clear()
        self.gold_box.refresh(self.project)
        self._show_issues()
        self.changed.emit()
