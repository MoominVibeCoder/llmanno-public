"""Tab „Splits & Freeze“: Dev/Test-Split, Instrument einfrieren, Präregistrierung (PLAN 3.8)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from html import escape
from math import ceil
from pathlib import Path
from typing import Any

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from llmanno.core.engine import fingerprint, results
from llmanno.core.export.prereg import export_preregistration, preregistration
from llmanno.core.metrics.sample_size import expected_half_width, units_needed
from llmanno.core.project import Project
from llmanno.core.validation import (
    GoldBasis,
    count_test_evaluations,
    create_split,
    freeze,
    freeze_intact,
    gold_basis,
    grouped_units,
    model_hashes,
    split_by_reference,
    split_gaps,
    split_sha256,
    unfreeze,
)
from llmanno.gui.common import (
    Collapsible,
    Page,
    Task,
    chosen_path,
    error_label,
    error_text,
    fill_combo,
    hint_label,
    output_language_setting,
)
from llmanno.gui.prompt_page import tip_row


def _hashes_in_thread(folder: Path, report: Callable[[Any], None]) -> dict[str, str]:
    """Fragt die Anbieter nach Digests; eigene Store-Verbindung, da im Worker-Thread."""
    project = Project.open(folder)
    try:
        return model_hashes(project)
    finally:
        project.close()


def freeze_status(project: Project) -> tuple[str, bool]:
    """Kurzer Freeze-Zustand für Statuszeilen; zweiter Wert: Warnung."""
    state = project.config.freeze
    if state is None:
        return "", False
    if freeze_intact(project):
        text = QCoreApplication.translate("SplitPage", "Instrument eingefroren ({0}…)")
        return text.format(state.instrument_hash[:12]), False
    return QCoreApplication.translate(
        "SplitPage", "Freeze gebrochen: Konfiguration oder Split geändert, Läufe gesperrt"
    ), True


class SplitPage(Page):
    help_topics = (
        "6. Splits & Freeze",
        "Dev/Test-Split und Freeze",
    )

    def __init__(self) -> None:
        super().__init__()
        self._task: Task | None = None
        self._before = ""
        self._basis: GoldBasis | None = None
        self._basis_note = ""

        # --- Gold-Größe ---
        self.plan_kappa = QDoubleSpinBox()
        self.plan_kappa.setRange(0.2, 0.95)
        self.plan_kappa.setSingleStep(0.05)
        self.plan_kappa.setValue(0.7)
        self.plan_width = QDoubleSpinBox()
        self.plan_width.setRange(0.02, 0.3)
        self.plan_width.setSingleStep(0.01)
        self.plan_width.setValue(0.1)
        self.plan_width.setPrefix("± ")
        for box in (self.plan_kappa, self.plan_width):
            box.valueChanged.connect(self._show_plan)
        self.plan_info = hint_label()
        plan_form = QFormLayout()
        tip_row(
            plan_form,
            self.tr("Erwartetes κ (LLM gegen Gold)"),
            self.plan_kappa,
            self.tr("Wie gut das LLM voraussichtlich mit dem Gold übereinstimmt, gemessen als "
                    "Cohens κ (0 = wie Zufall, 1 = vollständig). Ohne Erfahrungswert 0,70 lassen."),
        )  # fmt: skip
        tip_row(
            plan_form,
            self.tr("Gewünschte Genauigkeit (95-%-Intervall)"),
            self.plan_width,
            self.tr("Wie breit das Konfidenzintervall für κ im Mittel sein darf: ± 0,10 heißt "
                    "etwa κ = 0,70 [0,60; 0,80]. Enger braucht mehr Einheiten mit Gold."),
        )  # fmt: skip
        plan_box = QGroupBox(self.tr("Wie viel Gold?"))
        plan_layout = QVBoxLayout(plan_box)
        plan_layout.addLayout(plan_form)
        plan_layout.addWidget(self.plan_info)

        # --- Split ---
        self.dev_share = QSpinBox()
        self.dev_share.setRange(5, 95)
        self.dev_share.setSuffix(" %")
        self.dev_share.valueChanged.connect(self._apply_settings)
        self.seed = QSpinBox()
        self.seed.setRange(0, 1_000_000)
        self.seed.valueChanged.connect(self._apply_settings)
        self.stratify = QCheckBox(self.tr("nach Referenzcode schichten"))
        self.stratify.toggled.connect(self._apply_settings)
        self.independent = QCheckBox(
            self.tr("Einheiten einer Gruppe als unabhängig behandeln (nicht empfohlen)")
        )
        self.independent.setToolTip(
            self.tr("Nur bei isolierter Kodierung. Dann zieht der Split je Einheit und der "
                    "Bootstrap ohne Gruppen: Intervalle werden schmaler als gerechtfertigt. "
                    "Sinnvoll nur, um die Fehlerrate je Gruppe zu testen.")
        )  # fmt: skip
        self.independent.toggled.connect(self._apply_settings)
        self.reference = QComboBox()
        self.reference.currentIndexChanged.connect(self._apply_settings)
        self.split_button = QPushButton(self.tr("Aufteilung erzeugen"))
        self.split_button.clicked.connect(self.make_split)
        self.split_lock = hint_label()
        self.split_info = hint_label()
        self.split_table = QTableWidget(0, 3)
        self.split_table.setHorizontalHeaderLabels(
            [self.tr("Referenzcode"), self.tr("Übungsteil (Dev)"), self.tr("Prüfteil (Test)")]
        )
        self.split_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.split_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.split_table.verticalHeader().hide()
        self.split_table.setMaximumHeight(200)

        form = QFormLayout()
        tip_row(
            form,
            self.tr("Anteil Übungsteil (Dev)"),
            self.dev_share,
            self.tr("Welcher Anteil der Einheiten mit Gold in den Übungsteil kommt; der Rest "
                    "bildet den Prüfteil (Test). Mehr Übungsteil hilft beim Schärfen, mehr "
                    "Prüfteil macht die Messung am Ende genauer."),
        )  # fmt: skip
        advanced = Collapsible()
        more = QFormLayout(advanced.body)
        more.setContentsMargins(0, 0, 0, 0)
        tip_row(
            more,
            self.tr("Startwert (Seed)"),
            self.seed,
            self.tr("Macht die Ziehung wiederholbar: Derselbe Startwert ergibt dieselbe "
                    "Aufteilung."),
        )  # fmt: skip
        more.addRow(self.stratify)
        more.addRow(self.independent)
        tip_row(
            more,
            self.tr("Referenz für die Schichtung"),
            self.reference,
            self.tr("Welches Gold die Codes für Schichtung und Tabelle liefert; automatisch: der "
                    "Konsens, sonst die erste Gold-Spalte."),
        )  # fmt: skip
        split_box = QGroupBox(self.tr("1. Aufteilung in Übungs- und Prüfteil (Split)"))
        split_layout = QVBoxLayout(split_box)
        split_layout.addWidget(
            hint_label(
                self.tr(
                    "Am Übungsteil (Dev) Codebuch und Prompt schärfen, so oft nötig. Der Prüfteil "
                    "(Test) bleibt bis zum Einfrieren gesperrt und misst am Ende unabhängig, wie "
                    "gut das Instrument ist. Mit Gruppen-Spalte (etwa Transkript) kommen ganze "
                    "Gruppen in einen Teil, damit kein Prompt Zeilen aus dem anderen Teil sieht."
                )
            )
        )
        split_layout.addLayout(form)
        split_layout.addWidget(advanced)
        split_layout.addWidget(self.split_button)
        split_layout.addWidget(self.split_lock)
        split_layout.addWidget(self.split_info)
        self.split_warning = error_label()
        split_layout.addWidget(self.split_warning)
        split_layout.addWidget(self.split_table)

        next_step = QLabel(
            self.tr("Dann: im Tab Lauf nur den Übungsteil kodieren, die Evaluation prüfen, "
                    "Codebuch und Prompt schärfen und wiederholen; erst danach einfrieren.")
        )  # fmt: skip
        next_step.setWordWrap(True)

        # --- Freeze ---
        self.freeze_state = QLabel()
        self.freeze_state.setWordWrap(True)
        self.freeze_button = QPushButton(self.tr("Instrument einfrieren"))
        self.freeze_button.clicked.connect(self._confirm_freeze)
        self.reason = QLineEdit()
        self.reason.setPlaceholderText(self.tr("Begründung (steht im Protokoll und im Bericht)"))
        self.reason.textChanged.connect(self._update_buttons)
        self.unfreeze_button = QPushButton(self.tr("Freeze aufheben"))
        self.unfreeze_button.clicked.connect(self.lift_freeze)
        unfreeze_row = QHBoxLayout()
        unfreeze_row.addWidget(self.reason, 1)
        unfreeze_row.addWidget(self.unfreeze_button)
        freeze_box = QGroupBox(self.tr("2. Einfrieren (Freeze)"))
        freeze_layout = QVBoxLayout(freeze_box)
        freeze_layout.addWidget(
            hint_label(
                self.tr(
                    "Das Einfrieren schreibt das Instrument fest: Codebuch, Code-Liste, Prompt, "
                    "Modelle samt genauer Version (Digest), Einstellungen und Aufteilung, "
                    "zusammengefasst in einer Prüfsumme (Instrument-Hash). Erst danach lässt "
                    "sich der Prüfteil auswerten; jede Auswertung wird protokolliert und gezählt. "
                    "Wer danach etwas ändert, bricht den Freeze."
                )
            )
        )
        freeze_layout.addWidget(self.freeze_state)
        freeze_layout.addWidget(self.freeze_button)
        freeze_layout.addLayout(unfreeze_row)

        # --- Präregistrierung ---
        self.prereg_button = QPushButton(self.tr("Präregistrierung exportieren…"))
        self.prereg_button.clicked.connect(self._export_prereg)
        prereg_box = QGroupBox(self.tr("3. Präregistrierung"))
        prereg_layout = QVBoxLayout(prereg_box)
        prereg_layout.addWidget(
            hint_label(
                self.tr(
                    "Der Analyseplan als Markdown oder Word, mit Prüfsumme, Codebuch, Prompt und "
                    "Einstellungen. Vor der Auswertung des Prüfteils öffentlich hinterlegen, etwa "
                    "bei OSF (Open Science Framework): So ist belegt, dass der Plan vor dem "
                    "Ergebnis feststand. Ohne Einfrieren ist er ein Entwurf."
                )
            )
        )
        prereg_layout.addWidget(self.prereg_button)

        self.status = hint_label()
        self.error = error_label()
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        for widget in (plan_box, split_box, next_step, freeze_box, prereg_box, self.status,
                       self.error):  # fmt: skip
            left_layout.addWidget(widget)
        left_layout.addStretch()
        # Rollbar statt gestaucht, wenn das Fenster niedrig oder „Erweitert“ offen ist.
        scroll = QScrollArea()
        scroll.setWidget(left)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMaximumWidth(500)

        self.preview = QTextBrowser()
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(QLabel(self.tr("Vorschau der Präregistrierung")))
        right_layout.addWidget(self.preview, 1)
        splitter = QSplitter()
        splitter.addWidget(scroll)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 3)
        layout = QVBoxLayout(self)
        layout.addWidget(splitter)

    @property
    def busy(self) -> bool:
        return self._task is not None and self._task.running

    # --- Stand ---------------------------------------------------------------------------------

    def refresh(self) -> None:
        if self.project is None:
            return
        cfg = self.project.config
        controls = (self.dev_share, self.seed, self.stratify, self.reference, self.independent)
        for widget in controls:
            widget.blockSignals(True)
        self.dev_share.setValue(round(cfg.split.dev_share * 100))
        self.seed.setValue(cfg.split.seed)
        self.stratify.setChecked(cfg.split.stratify)
        self.independent.setChecked(cfg.split.independent_units)
        columns = []
        if cfg.data is not None:
            columns = [*cfg.data.mapping.gold]
            if cfg.data.mapping.consensus:
                columns.append(cfg.data.mapping.consensus)
        fill_combo(
            self.reference,
            [(self.tr("automatisch (Konsens, sonst erste Spalte)"), None)]
            + [(c, c) for c in columns],
            cfg.split.reference,
        )
        for widget in controls:
            widget.blockSignals(False)
        grouped = grouped_units(cfg)
        self.stratify.setToolTip(
            self.tr("Mit Gruppen-Spalte wird gruppenweise gezogen; Schichten entfällt.")
            if grouped
            else self.tr("Zieht je Referenzcode getrennt, damit auch seltene Codes in beiden "
                         "Teilen vorkommen.")
        )  # fmt: skip
        self._show_split()
        self._load_basis()
        self._show_plan()
        self._show_freeze()
        self._show_preview()
        self._update_buttons()

    def _show_split(self) -> None:
        assert self.project is not None
        parts = self.project.store.split()
        self.split_table.setRowCount(0)
        self.split_table.setVisible(bool(parts))
        self._show_gaps(bool(parts))
        if not parts:
            self.split_info.setText(
                self.tr("Noch keine Aufteilung, also auch keine Sperre des Prüfteils.")
            )
            self.split_info.setToolTip("")
            return
        n_dev = sum(1 for p in parts.values() if p == "dev")
        self.split_info.setText(
            self.tr("Übungsteil (Dev) {0} · Prüfteil (Test) {1} Einheiten").format(
                n_dev, len(parts) - n_dev
            )
        )
        self.split_info.setToolTip(
            self.tr("Prüfsumme der Aufteilung (Split-SHA-256): {0}").format(split_sha256(parts))
        )
        try:
            table = split_by_reference(self.project)
        except ValueError as exc:
            self.error.setText(error_text(exc))
            return
        for code, counts in table.items():
            row = self.split_table.rowCount()
            self.split_table.insertRow(row)
            for col, value in enumerate((code, str(counts["dev"]), str(counts["test"]))):
                self.split_table.setItem(row, col, QTableWidgetItem(value))

    def _show_plan(self) -> None:
        """Stichprobenrechner; die Verteilung kommt aus refresh, die Spinboxen rechnen nur neu."""
        project, basis = self.project, self._basis
        if project is None or basis is None:
            self.plan_info.setText(self._basis_note)
            return
        kappa, width = self.plan_kappa.value(), self.plan_width.value()
        needed = units_needed(basis.counts, kappa, width)
        dev = project.config.split.dev_share
        total = ceil(needed / (1 - dev))
        lines = [
            self.tr("Der Prüfteil (Test) braucht etwa {0} Einheiten, bei {1} % Übungsteil also "
                    "rund {2} Einheiten mit Gold.").format(needed, round(dev * 100), total),
            self.tr("Verteilung aus dem Gold.")
            if basis.from_gold
            else self.tr("Noch kein Gold: Codes als gleich häufig angenommen."),
        ]  # fmt: skip
        if basis.current_test:
            lines.append(
                self.tr(
                    "Der jetzige Prüfteil ({0} Einheiten) ergibt im Mittel etwa ± {1:.2f}."
                ).format(
                    basis.current_test, expected_half_width(basis.counts, kappa, basis.current_test)
                )
            )
        if grouped_units(project.config):
            lines.append(self.tr("Einheiten hängen über Gruppen zusammen; dann braucht es mehr."))
        self.plan_info.setText(" ".join(lines))

    def _load_basis(self) -> None:
        assert self.project is not None
        self._basis, self._basis_note = None, ""
        if self.project.config.codebook is None:
            self._basis_note = self.tr("Braucht ein Codebuch.")
            return
        try:
            self._basis = gold_basis(self.project)
        except ValueError as exc:
            self._basis_note = error_text(exc)
            return
        if self._basis is None:
            self._basis_note = self.tr("Mit nur einer Kategorie ist κ nicht bestimmbar.")

    def _show_gaps(self, has_split: bool) -> None:
        assert self.project is not None
        try:
            unmapped, outside, values = split_gaps(self.project)
        except ValueError:  # noch keine Daten oder Codes
            unmapped, outside, values = [], [], Counter()
        lines = []
        if unmapped:
            listed = ", ".join(f"„{v}“" for v, _ in values.most_common(5))
            lines.append(
                self.tr("{0} Einheiten haben nur Gold ohne Code ({1}) und fehlen im Split. "
                        "Werte im Tab Codebuch zuordnen, dann den Split ziehen.").format(
                    len(unmapped), listed
                )
            )  # fmt: skip
        if outside and has_split:
            lines.append(
                self.tr("{0} Einheiten mit Gold stehen in keinem Teil des Splits (Zuordnung "
                        "nach dem Split geändert). Sie zählen nur unter „alle Einheiten“; "
                        "vor dem Freeze den Split neu ziehen.").format(len(outside))
            )  # fmt: skip
        self.split_warning.setText("\n".join(lines))
        self.split_warning.setVisible(bool(lines))

    def _show_freeze(self) -> None:
        assert self.project is not None
        state = self.project.config.freeze
        evaluations = count_test_evaluations(self.project)
        seen = self.tr("Prüfteil (Test) bisher {0}-mal ausgewertet.").format(evaluations)
        if state is None:
            text = escape(self.tr("Nicht eingefroren.")) + " " + escape(seen)
            self.freeze_state.setToolTip("")
        else:
            frozen = state.frozen_at.astimezone().strftime("%d.%m.%Y %H:%M")
            text = escape(
                self.tr("Eingefroren am {0} · Prüfsumme (Instrument-Hash) {1}…").format(
                    frozen, state.instrument_hash[:12]
                )
            )
            self.freeze_state.setToolTip(
                self.tr("Prüfsumme des Instruments (Instrument-Hash): {0}. Sie steht in "
                        "Präregistrierung und Report; stimmen beide überein, wurde nach dem "
                        "Einfrieren nichts geändert.").format(state.instrument_hash)
            )  # fmt: skip
            if freeze_intact(self.project):
                text += "<br><b style='color:#256b86'>" + escape(self.tr("intakt")) + "</b>"
            else:
                text += (
                    "<br><b style='color:#b3261e'>"
                    + escape(
                        self.tr(
                            "gebrochen: Einstellungen oder Aufteilung wurden seit dem Einfrieren "
                            "geändert, Läufe sind gesperrt. Änderung zurücknehmen oder den Freeze "
                            "unten mit Begründung aufheben."
                        )
                    )
                    + "</b>"
                )
            text += "<br>" + escape(seen)
        self.freeze_state.setText(text)

    def _show_preview(self) -> None:
        assert self.project is not None
        cfg = self.project.config
        if cfg.data is None or cfg.codebook is None:
            self.preview.setMarkdown(
                self.tr("*Die Vorschau der Präregistrierung erscheint, sobald Daten und Codebuch "
                        "geladen sind.*")
            )  # fmt: skip
            return
        try:
            self.preview.setMarkdown(preregistration(self.project, output_language_setting()))
        except Exception as exc:  # Vorschau darf den Tab nie unbenutzbar machen
            self.preview.setPlainText(error_text(exc))

    def _update_buttons(self) -> None:
        project = self.project
        if project is None:
            return
        frozen = project.config.freeze is not None
        evaluated = count_test_evaluations(project) > 0
        locked = frozen or self.busy or evaluated
        for widget in (self.dev_share, self.seed, self.reference, self.split_button):
            widget.setEnabled(not locked)
        # Warum die Aufteilung gesperrt ist; während des Einfrierens kein Hinweis.
        lock = (
            self.tr("Neue Aufteilung gesperrt: Der Prüfteil wurde schon ausgewertet, eine neue "
                    "Aufteilung mischte bereits angesehene Einheiten in den Prüfteil.")
            if evaluated
            else self.tr("Neue Aufteilung gesperrt: Das Instrument ist eingefroren. Dafür erst "
                         "den Freeze mit Begründung aufheben.")
            if frozen
            else ""
        )  # fmt: skip
        self.split_lock.setText(lock)
        self.split_lock.setVisible(bool(lock))
        self.split_button.setToolTip(
            lock
            or self.tr("Teilt die Einheiten mit Gold zufällig in Übungsteil (Dev) und Prüfteil "
                       "(Test) (Split). Neu erzeugen ersetzt die bisherige Aufteilung.")
        )  # fmt: skip
        cfg = project.config
        self.stratify.setEnabled(not locked and not grouped_units(cfg))
        isolated = cfg.coding.units.mode == "isolated"
        has_group = cfg.data is not None and cfg.data.mapping.group is not None
        self.independent.setEnabled(not locked and isolated and has_group)
        ready = project.config.data is not None and bool(project.config.panel)
        self.freeze_button.setEnabled(not frozen and not self.busy and ready)
        self.freeze_button.setToolTip(
            ""
            if ready or frozen
            else self.tr("Braucht importierte Daten und mindestens ein Modell im Panel.")
        )
        self.reason.setEnabled(frozen)
        self.unfreeze_button.setEnabled(frozen and bool(self.reason.text().strip()))
        self.prereg_button.setEnabled(project.config.codebook is not None and ready)

    def _apply_settings(self) -> None:
        if self.project is None:
            return
        split = self.project.config.split.model_copy(
            update={
                "dev_share": self.dev_share.value() / 100,
                "seed": self.seed.value(),
                "stratify": self.stratify.isChecked(),
                "independent_units": self.independent.isChecked(),
                "reference": self.reference.currentData(),
            }
        )
        try:
            self.project.update(split=split)
        except Exception as exc:
            self.error.setText(error_text(exc))
            return
        self._show_plan()  # Dev-Anteil geändert
        self.changed.emit()

    # --- Aktionen ------------------------------------------------------------------------------

    def make_split(self) -> None:
        if self.project is None:
            return
        self.error.clear()
        try:
            parts = create_split(self.project)
        except (PermissionError, ValueError) as exc:
            self.error.setText(error_text(exc))
            return
        self.status.setText(self.tr("Aufteilung erzeugt: {0} Einheiten.").format(len(parts)))
        self.refresh()
        self.changed.emit()

    def _confirm_freeze(self) -> None:
        """Rückfrage mit Checkliste vor dem Einfrieren; freeze_instrument fragt selbst nicht."""
        if self.project is None or self.busy:
            return
        parts = self.project.store.split()
        if not parts:
            text = self.tr(
                "Achtung: Es gibt noch keine Aufteilung (Split). Ohne Prüfteil misst die "
                "Evaluation an denselben Einheiten, an denen Codebuch und Prompt geschärft "
                "wurden, und überschätzt die Güte.\n\nEmpfohlen: erst aufteilen, den Übungsteil "
                "kodieren und auswerten, dann einfrieren.\n\nTrotzdem ohne Aufteilung einfrieren?"
            )
        else:
            dev = [i for i, part in parts.items() if part == "dev"]
            coded = 0
            if self.project.config.codebook is not None:
                coded = sum(r.status == "ok" for r in results(self.project, dev).values())
            split_line = self.tr("Aufteilung vorhanden: Übungsteil {0}, Prüfteil {1} Einheiten")
            coded_line = self.tr("Übungsteil kodiert: {0} von {1} Einheiten mit gültigem Ergebnis")
            text = "\n".join(
                [
                    self.tr("Vor dem Einfrieren prüfen:"),
                    "✓ " + split_line.format(len(dev), len(parts) - len(dev)),
                    ("✓ " if coded == len(dev) else "✗ ") + coded_line.format(coded, len(dev)),
                    "□ "
                    + self.tr("Evaluation des Übungsteils geprüft, Codebuch und Prompt geschärft?"),
                    "",
                    self.tr("Danach bricht jede Änderung an Codebuch, Prompt, Modellen, "
                            "Einstellungen oder Aufteilung den Freeze; das steht im Report."),
                    "",
                    self.tr("Instrument jetzt einfrieren?"),
                ]
            )  # fmt: skip
        answer = QMessageBox.question(
            self,
            self.tr("Instrument einfrieren?"),
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.freeze_instrument()

    def freeze_instrument(self) -> None:
        if self.project is None or self.busy:
            return
        self.error.clear()
        if self.project.dirty:
            # Der Hintergrund-Thread liest project.json; ungespeicherte Änderungen zuerst sichern.
            self.project.save()
            self.changed.emit()
        self._before = fingerprint(self.project)
        self.status.setText(self.tr("Frage die Anbieter nach der genauen Modellversion (Digest) …"))
        folder = self.project.folder
        self._task = Task(self, lambda report: _hashes_in_thread(folder, report))
        self._task.done.connect(self._commit_freeze)
        self._task.failed.connect(self._freeze_failed)
        self._task.finished.connect(self._update_buttons)
        self._task.start()
        self._update_buttons()  # nach dem Start, sonst gilt busy noch nicht

    def _commit_freeze(self, hashes: dict[str, str]) -> None:
        assert self.project is not None
        if fingerprint(self.project) != self._before:
            self._freeze_failed(
                RuntimeError(
                    self.tr(
                        "Die Konfiguration hat sich während des Einfrierens geändert. Bitte "
                        "erneut einfrieren."
                    )
                )  # fmt: skip
            )
            return
        try:
            state = freeze(self.project, hashes)
        except (PermissionError, ValueError) as exc:
            self._freeze_failed(exc)
            return
        self.status.setText(
            self.tr("Eingefroren. Prüfsumme (Instrument-Hash): {0}").format(state.instrument_hash)
        )
        self.refresh()
        self.changed.emit()

    def _freeze_failed(self, exc: BaseException) -> None:
        self.status.clear()
        self.error.setText(error_text(exc))

    def lift_freeze(self) -> None:
        if self.project is None:
            return
        self.error.clear()
        try:
            unfreeze(self.project, self.reason.text())
        except ValueError as exc:
            self.error.setText(error_text(exc))
            return
        self.reason.clear()
        self.status.setText(self.tr("Freeze aufgehoben; die Begründung steht im Protokoll."))
        self.refresh()
        self.changed.emit()

    def _export_prereg(self) -> None:
        if self.project is None:
            return
        folder = self.project.folder / "exports"
        folder.mkdir(exist_ok=True)
        path, selected = QFileDialog.getSaveFileName(
            self,
            self.tr("Präregistrierung speichern"),
            str(folder / f"{self.project.config.name}_praeregistrierung.md"),
            self.tr("Markdown (*.md);;Word (*.docx)"),
        )
        if path:
            self.export_prereg_to(chosen_path(path, selected))

    def export_prereg_to(self, path: Path) -> None:
        assert self.project is not None
        self.error.clear()
        try:
            export_preregistration(self.project, path, output_language_setting())
        except Exception as exc:
            self.error.setText(error_text(exc))
            return
        self.status.setText(self.tr("Gespeichert: {0}").format(path))
