"""Dialog „Unstimmigkeiten“: Code-Paare, an denen die Modelle vom Gold oder voneinander
abweichen, mit den Fällen und Begründungen; auf Wunsch schlägt ein Modell eine schärfere
Abgrenzung vor. Das Codebuch ändert sich dadurch nicht."""

from __future__ import annotations

from html import escape
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from llmanno.core.disagreements import (
    MAX_CASES,
    NONE,
    Case,
    Overview,
    Pair,
    export_overview,
    overview,
    suggest,
    suggestions,
)
from llmanno.core.project import Project
from llmanno.core.providers import make_provider
from llmanno.gui.common import Task, error_label, error_text, fill_combo, hint_label


def _suggest_in_thread(folder: Path, pair: Pair, key: str, consent: bool, factory) -> str:
    """Eigene Store-Verbindung: SQLite-Verbindungen sind an ihren Thread gebunden."""
    project = Project.open(folder)
    try:
        return suggest(project, pair, key, remote_consent=consent, provider_factory=factory)
    finally:
        project.close()


class DisagreementDialog(QDialog):
    def __init__(
        self, project: Project, parent: QWidget | None = None, *, provider_factory=make_provider
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Unstimmigkeiten"))
        self.resize(1150, 780)
        self.project = project
        self._factory = provider_factory
        self._task: Task | None = None
        self.found: Overview | None = None
        self._text = ""  # angezeigter Vorschlag, zum Kopieren

        self.scope = hint_label()
        self.pairs = QTableWidget(0, 3)
        self.pairs.setHorizontalHeaderLabels(
            [self.tr("Codes"), self.tr("weicht vom Gold ab"), self.tr("Modelle uneinig")]
        )
        self.pairs.horizontalHeaderItem(1).setToolTip(
            self.tr("Einheiten, in denen mindestens ein Modell an diesem Paar anders kodiert als "
                    "das menschliche Gold.")
        )  # fmt: skip
        self.pairs.horizontalHeaderItem(2).setToolTip(
            self.tr("Einheiten, in denen sich die Modelle an diesem Paar uneinig sind; das zeigt "
                    "unklare Stellen auch ohne Gold.")
        )  # fmt: skip
        self.cases = QTableWidget(0, 0)
        self.detail = QTextBrowser()
        for table in (self.pairs, self.cases):
            table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            table.verticalHeader().setVisible(False)
        self.pairs.itemSelectionChanged.connect(self._pair_chosen)
        self.cases.itemSelectionChanged.connect(self._case_chosen)

        self.model_combo = QComboBox()
        fill_combo(self.model_combo, [(m.key, m.key) for m in project.config.panel])
        self.suggest_button = QPushButton(self.tr("Abgrenzung vorschlagen (LLM)"))
        self.suggest_button.setToolTip(
            self.tr("Schickt Codebuch und bis zu {0} Fälle dieses Paars an das gewählte Modell. "
                    "Es schlägt eine schärfere Regel vor; übernehmen musst du sie selbst ins "
                    "Codebuch. Der Vorschlag wird protokolliert.").format(MAX_CASES)
        )  # fmt: skip
        self.suggest_button.clicked.connect(self._suggest)
        self.copy_button = QPushButton(self.tr("Vorschlag kopieren"))
        self.copy_button.clicked.connect(self._copy)
        self.suggestion = QTextBrowser()
        self.error = error_label()
        self.save_button = QPushButton(self.tr("Als Arbeitsblatt speichern (XLSX)…"))
        self.save_button.setToolTip(
            self.tr("Alle Paare mit dem letzten Vorschlag und alle Fälle mit Text, Gold, Codes "
                    "und Begründungen der Modelle; zum Durchgehen im Team.")
        )  # fmt: skip
        self.save_button.clicked.connect(self._save)

        upper = QSplitter()
        upper.addWidget(self.pairs)
        upper.addWidget(self.cases)
        upper.setSizes([330, 820])
        lower = QSplitter()
        lower.addWidget(self.detail)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(self.model_combo, 1)
        row.addWidget(self.suggest_button)
        row.addWidget(self.copy_button)
        right_layout.addLayout(row)
        right_layout.addWidget(self.suggestion, 1)
        right_layout.addWidget(self.error)
        lower.addWidget(right)
        lower.setSizes([520, 630])
        body = QSplitter(Qt.Orientation.Vertical)
        body.addWidget(upper)
        body.addWidget(lower)
        body.setSizes([380, 400])
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.addButton(self.save_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.scope)
        layout.addWidget(body, 1)
        layout.addWidget(buttons)
        self.refresh()

    @property
    def busy(self) -> bool:
        return self._task is not None and self._task.running

    def reject(self) -> None:
        if self.busy:  # die Anfrage gehört zu diesem Dialog; endet spätestens mit dem Zeitlimit
            self.error.setText(
                self.tr("Die Anfrage läuft noch; danach lässt sich der Dialog schließen.")
            )
            return
        super().reject()

    def refresh(self) -> None:
        try:
            self.found = overview(self.project)
        except ValueError as exc:
            self.found = None
            self.scope.setText(error_text(exc))
            self._fill_pairs([])
            return
        f = self.found
        if f.dev_only:
            scope = self.tr("Übungsteil (Dev): {0} Einheiten mit Ergebnis, Gold-Referenz {1}. "
                            "Prüfteil (Test) und Beispiel-Einheiten im Prompt bleiben außen "
                            "vor.").format(f.n_units, f.reference or "–")  # fmt: skip
        else:
            scope = self.tr("Alle kodierten Einheiten ({0}), Gold-Referenz {1}. Ohne Aufteilung "
                            "gibt es keinen unabhängigen Prüfteil: Was du hier schärfst, misst die "
                            "Evaluation danach zu günstig.").format(
                f.n_units, f.reference or "–"
            )  # fmt: skip
        if not f.pairs:
            scope += " " + self.tr("Keine Unstimmigkeiten: Die Modelle stimmen untereinander "
                                   "und mit dem Gold überein.")  # fmt: skip
        self.scope.setText(scope)
        self._fill_pairs(f.pairs)

    def _fill_pairs(self, pairs: list[Pair]) -> None:
        self.pairs.setRowCount(len(pairs))
        for row, pair in enumerate(pairs):
            label = QTableWidgetItem(" ↔ ".join(pair.codes))
            label.setData(Qt.ItemDataRole.UserRole, row)
            self.pairs.setItem(row, 0, label)
            for column, n in ((1, pair.n_gold), (2, pair.n_models)):
                item = QTableWidgetItem()
                item.setData(Qt.ItemDataRole.DisplayRole, n)
                self.pairs.setItem(row, column, item)
        self.pairs.resizeColumnsToContents()
        enabled = bool(pairs)
        for widget in (self.suggest_button, self.save_button, self.model_combo):
            widget.setEnabled(enabled)
        if pairs:
            self.pairs.selectRow(0)
        else:
            self.cases.setRowCount(0)
            self.detail.clear()
            self.suggestion.clear()
        self.copy_button.setEnabled(False)

    def selected_pair(self) -> Pair | None:
        rows = self.pairs.selectionModel().selectedRows()
        if self.found is None or not rows:
            return None
        return self.found.pairs[rows[0].data(Qt.ItemDataRole.UserRole)]

    def _pair_chosen(self) -> None:
        pair = self.selected_pair()
        if pair is None or self.found is None:
            return
        models = self.found.models
        self.cases.clear()
        self.cases.setColumnCount(3 + len(models))
        self.cases.setHorizontalHeaderLabels(
            [self.tr("Einheit"), self.tr("Text"), self.tr("Gold"), *models]
        )
        self.cases.setRowCount(len(pair.cases))
        for row, case in enumerate(pair.cases):
            cells = [case.item.item_id, case.item.target.replace("\n", " "),
                     ", ".join(case.reference) if case.reference else ""]  # fmt: skip
            for column, value in enumerate(cells):
                self.cases.setItem(row, column, QTableWidgetItem(value))
            for column, model in enumerate(models, 3):
                vote = case.votes.get(model)
                item = QTableWidgetItem((", ".join(vote) or NONE) if vote is not None else "")
                item.setToolTip(case.rationales.get(model, ""))
                self.cases.setItem(row, column, item)
        self.cases.setColumnWidth(1, 380)
        self.cases.selectRow(0)
        self.error.clear()
        self._show_suggestion(pair)

    def _case_chosen(self) -> None:
        pair = self.selected_pair()
        rows = self.cases.selectionModel().selectedRows()
        if pair is None or not rows:
            return
        self.detail.setHtml(self._case_html(pair.cases[rows[0].row()]))

    def _case_html(self, case: Case) -> str:
        lines = "<br>".join(
            (f"<b>{escape(line.speaker)}:</b> " if line.speaker else "") + escape(line.text)
            for line in case.item.lines
        )
        parts = [f"<p><b>{escape(case.item.item_id)}</b></p><p>{lines}</p>"]
        gold = ", ".join(case.reference) if case.reference else "–"
        parts.append("<p>" + escape(self.tr("Gold: {0}").format(gold)) + "</p>")
        for model, vote in case.votes.items():
            parts.append(
                f"<p><b>{escape(model)}: {escape(', '.join(vote) or NONE)}</b><br>"
                + escape(case.rationales.get(model, ""))
                + "</p>"
            )
        return "".join(parts)

    def _show_suggestion(self, pair: Pair) -> None:
        latest = [s for s in suggestions(self.project) if s.codes == pair.codes]
        if not latest:
            self.suggestion.setPlainText(
                self.tr("Noch kein Vorschlag für dieses Paar. Ein Vorschlag ist eine Anregung: "
                        "Das Codebuch änderst du selbst (Datei bearbeiten, neu laden), danach "
                        "den Übungsteil neu kodieren und auswerten.")
            )  # fmt: skip
            self.copy_button.setEnabled(False)
            return
        s = latest[-1]
        note = self.tr(
            "Vorschlag von {0} ({1}), ungeprüft. Das Codebuch ändert sich nicht von selbst."
        ).format(s.model, s.ts.astimezone().strftime("%d.%m.%Y %H:%M"))
        self.suggestion.setMarkdown(f"_{note}_\n\n{s.text}")  # fmt: skip
        self._text = s.text
        self.copy_button.setEnabled(True)

    def _copy(self) -> None:
        QApplication.clipboard().setText(self._text)

    def _suggest(self) -> None:
        pair, key = self.selected_pair(), self.model_combo.currentData()
        if pair is None or key is None or self.busy:
            return
        cfg = self.project.config
        provider = cfg.provider(next(m.provider for m in cfg.panel if m.key == key))
        consent = provider.privacy_level != "local"
        if consent and not self._ask_consent(provider, min(len(pair.cases), MAX_CASES)):
            return
        if self.project.dirty:  # der Hintergrund liest project.json, nicht diesen Stand
            self.project.save()
        folder, factory = self.project.folder, self._factory
        self.suggest_button.setEnabled(False)
        self.error.clear()
        self.suggestion.setPlainText(self.tr("Frage {0} …").format(key))
        self._task = Task(
            self, lambda report: _suggest_in_thread(folder, pair, key, consent, factory)
        )
        # Nur zeigen, wenn das Paar noch gewählt ist; sonst erscheint er bei der Rückkehr.
        self._task.done.connect(
            lambda text: self.selected_pair() is pair and self._show_suggestion(pair)
        )
        self._task.failed.connect(self._failed)
        self._task.finished.connect(lambda: self.suggest_button.setEnabled(True))
        self._task.start()

    def _failed(self, exc: BaseException) -> None:
        self.error.setText(error_text(exc))
        if pair := self.selected_pair():
            self._show_suggestion(pair)

    def _ask_consent(self, provider, n: int) -> bool:
        names = (
            self.tr("Namen der Namensliste werden vor dem Senden ersetzt.")
            if self.project.config.coding.pseudonyms_sha256
            else self.tr("Namen werden nicht ersetzt (Tab Prompt & Einheiten → Namen ersetzen…).")
        )
        answer = QMessageBox.question(
            self,
            self.tr("Daten an externe Anbieter senden?"),
            self.tr("Für den Vorschlag gehen das Codebuch und die Texte von bis zu {0} Einheiten "
                    "samt Gold und Begründungen der Modelle an:\n\n• {1} ({2}, {3})\n\nBitte "
                    "vorher prüfen, ob der Anbieter für die Daten zulässig ist. Die Zustimmung "
                    "wird im Projektprotokoll vermerkt.").format(
                n, provider.name, urlparse(provider.base_url).netloc or provider.base_url,
                self.tr("akademisch / EU") if provider.privacy_level == "academic"
                else self.tr("extern"),
            )
            + ("\n\n" + self.tr("Achtung: Der API-Schlüssel ginge unverschlüsselt über http.")
               if provider.key_in_clear else "")
            + "\n\n"
            + names,
        )  # fmt: skip
        return answer == QMessageBox.StandardButton.Yes

    def _save(self) -> None:
        if self.found is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("Arbeitsblatt speichern"),
            str(self.project.folder / "abgrenzungen.xlsx"),
            self.tr("Excel (*.xlsx)"),
        )
        if not path:
            return
        try:
            export_overview(self.project, self.found, Path(path))
        except Exception as exc:
            self.error.setText(error_text(exc))
        else:
            self.error.clear()
            self.scope.setText(self.scope.text() + " " + self.tr("Gespeichert: {0}").format(path))
