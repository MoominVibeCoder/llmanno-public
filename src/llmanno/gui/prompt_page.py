"""Tab „Prompt & Einheiten“: Einheiten-Modus, Läufe, Prompt-Parameter und Prompt-Vorschau."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from llmanno.core.codesystems import is_codesystem
from llmanno.core.config import CodingConfig, UnitSettings
from llmanno.core.engine import prepare_prompts, run_budget
from llmanno.core.examples import MAX_PER_CODE, draw_examples
from llmanno.core.prompt import PromptSpec, estimate_tokens, render
from llmanno.core.units import Item
from llmanno.gui.common import (
    Collapsible,
    Page,
    error_label,
    error_text,
    fill_combo,
    hint_label,
)
from llmanno.gui.pseudonym_dialog import PseudonymDialog

PREVIEW_ITEMS = 500


def _spin(low: int, high: int) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(low, high)
    return spin


def tip_row(form: QFormLayout, text: str, field: QWidget | QLayout, tip: str = "") -> QLabel:
    """Formularzeile, deren Beschriftung (und Feld, wenn es ein Widget ist) den Tooltip trägt."""
    label = QLabel(text)
    label.setToolTip(tip)
    if isinstance(field, QWidget):
        field.setToolTip(tip)
    form.addRow(label, field)
    return label


class PromptPage(Page):
    help_topics = (
        "5. Prompt & Einheiten",
        "Läufe, Temperatur und Panel",
        "Datenschutz",
        "Kosten und Kontingent",
    )

    def __init__(self) -> None:
        super().__init__()
        self._spec: PromptSpec | None = None
        self._items: list[Item] = []
        self._budget: tuple[list[str], str] = ([], "")
        self.unit_mode = QComboBox()
        fill_combo(
            self.unit_mode,
            [
                (self.tr("Zeile isoliert"), "isolated"),
                (self.tr("Zeile mit Kontextfenster"), "context"),
                (self.tr("Gruppe holistisch"), "holistic"),
            ],
        )
        self.n_before, self.n_after = _spin(0, 20), _spin(0, 20)
        self.target_column = QComboBox()
        self.target_column.currentIndexChanged.connect(self._apply)
        self.n_runs, self.min_agree = _spin(1, 20), _spin(2, 20)
        self.base_seed = _spin(0, 2_000_000_000)
        self.max_signal_words, self.max_rationale_words = _spin(1, 10), _spin(10, 200)
        self.adaptive = QCheckBox(
            self.tr("Adaptiver Stopp: aufhören, sobald genug Durchläufe denselben Code liefern")
        )
        self.adaptive.setToolTip(
            self.tr("Spart Aufrufe bei eindeutigen Einheiten: Liefern die ersten Durchläufe alle "
                    "denselben Code, entfallen die übrigen. Ausschalten nur, wenn jede Einheit "
                    "genau N Durchläufe bekommen soll.")
        )  # fmt: skip
        self.permute = QCheckBox(
            self.tr("Code-Reihenfolge je Durchlauf mischen (gegen Positions-Bias)")
        )
        self.permute.setToolTip(
            self.tr("Modelle wählen bevorzugt Codes, die weit oben stehen (Positions-Bias). "
                    "Gemischt steht jeder Code mal vorn, mal hinten, und der Effekt gleicht sich "
                    "über die Durchläufe aus.")
        )  # fmt: skip
        self.evidence_repair = QCheckBox(
            self.tr("Nachfrage (Repair), wenn Signalwörter nicht im Text stehen")
        )
        self.evidence_repair.setToolTip(
            self.tr("Fragt einmal nach, wenn ein Signalwort nicht wörtlich im Text steht; "
                    "übernommen werden nur bessere Belege, nie ein anderer Code. Kleine lokale "
                    "Modelle (unter 10 Mrd. Parameter) schalten das beim Übernehmen ins Panel "
                    "aus: Dort kostet die Nachfrage viel und hilft selten.")
        )  # fmt: skip
        self.repair_hint = hint_label()
        self.names_button = QPushButton(self.tr("Namen ersetzen…"))
        self.names_button.clicked.connect(self._edit_names)
        self.names_state = hint_label()
        self.shared_hint = hint_label()
        self.examples_per_code = _spin(1, MAX_PER_CODE)
        self.examples_button = QPushButton(self.tr("Beispiele auswählen"))
        self.examples_button.clicked.connect(self._draw_examples)
        self.examples_off = QPushButton(self.tr("Entfernen"))
        self.examples_off.clicked.connect(lambda: self._set_examples([]))
        self.examples_state = hint_label()
        # Eingefrorene weitere Codesysteme (vom Hauptfenster gesetzt).
        self.frozen_systems: Callable[[], list[str]] = lambda: []
        self.error = error_label()

        self.unit_mode.currentIndexChanged.connect(self._apply)
        for spin in (self.n_before, self.n_after, self.n_runs, self.min_agree, self.base_seed,
                     self.max_signal_words, self.max_rationale_words):  # fmt: skip
            spin.valueChanged.connect(self._apply)
        for check in (self.adaptive, self.permute, self.evidence_repair):
            check.toggled.connect(self._apply)

        context = QHBoxLayout()
        context.addWidget(QLabel(self.tr("davor")))
        context.addWidget(self.n_before)
        context.addWidget(QLabel(self.tr("danach")))
        context.addWidget(self.n_after)
        context.addStretch()

        form = QFormLayout()
        tip_row(
            form,
            self.tr("Analyseeinheit"),
            self.unit_mode,
            self.tr("Was das Modell auf einmal sieht und kodiert: eine Zeile allein, eine Zeile "
                    "mit den Nachbarzeilen als Kontext (kodiert wird nur die Zielzeile) oder eine "
                    "ganze Gruppe als eine Einheit. Ändern startet eine neue Kodierung."),
        )  # fmt: skip
        self.context_label = tip_row(form, self.tr("Kontextzeilen"), context)
        tip_row(
            form,
            self.tr("Kodieren nur, wo diese Spalte ja ist"),
            self.target_column,
            self.tr("Zeilen mit „nein“ (leer, 0, nein, no) in dieser Spalte stehen nur als Kontext "
                    "im Prompt und werden nicht kodiert, etwa Züge der Lehrkraft. Nicht bei "
                    "„Gruppe holistisch“: Dort wird die ganze Gruppe kodiert."),
        )  # fmt: skip
        tip_row(
            form,
            self.tr("Durchläufe pro Einheit (N)"),
            self.n_runs,
            self.tr("Wie oft jedes Modell dieselbe Einheit kodiert. Aus der Übereinstimmung der "
                    "Durchläufe ergibt sich die Konsistenz (p_sc), das wichtigste Maß für die "
                    "Unsicherheit. Der Wechsel von 1 auf mehrere ändert bei automatischer "
                    "Temperatur auch die Temperatur (0 → 0,7) und beginnt neu."),
        )  # fmt: skip
        names = QHBoxLayout()
        names.addWidget(self.names_button)
        names.addWidget(self.names_state, 1)
        names_tip = self.tr(
            "Ersetzt Namen in Text, Sprecher und Metadaten vor dem Senden, etwa „Lena“ → "
            "„Person 1“. Daten, Ergebnisse und Exporte behalten die Originale; Codebuch und IDs "
            "gehen unverändert an das Modell."
        )
        self.names_button.setToolTip(names_tip)
        tip_row(form, self.tr("Pseudonymisierung"), names, names_tip)

        # Profi-Einstellungen: Die Vorgaben passen für den Einstieg.
        self.advanced = Collapsible()
        more = QFormLayout(self.advanced.body)
        more.setContentsMargins(0, 0, 0, 0)
        more.addRow(self.adaptive)
        tip_row(
            more,
            self.tr("Einstimmige Durchläufe für den Stopp"),
            self.min_agree,
            self.tr("So viele gültige Durchläufe müssen denselben Code liefern, bevor der "
                    "adaptive Stopp greift. Mehr ist vorsichtiger und kostet mehr Aufrufe."),
        )  # fmt: skip
        tip_row(
            more,
            self.tr("Startwert (Seed)"),
            self.base_seed,
            self.tr("Macht den Zufall wiederholbar: die Code-Reihenfolge je Durchlauf, die "
                    "Auswahl der Beispiele und, wo der Anbieter es unterstützt, die Antwort. "
                    "Ändern startet eine neue Kodierung."),
        )  # fmt: skip
        more.addRow(self.permute)
        tip_row(
            more,
            self.tr("Signalwörter je Code (max.)"),
            self.max_signal_words,
            self.tr("Wie viele Wörter oder kurze Wortgruppen das Modell je Code wörtlich aus dem "
                    "Text als Beleg nennt (Signalwörter). Das Programm prüft, ob sie im Text "
                    "stehen, und markiert sie im Tab Ergebnisse."),
        )  # fmt: skip
        tip_row(
            more,
            self.tr("Begründung (max. Wörter)"),
            self.max_rationale_words,
            self.tr("Höchstlänge der kurzen Begründung des Modells, zu sehen im Tab Ergebnisse. "
                    "Kürzer spart Ausgabe-Token; die Begründung plausibilisiert, sie erklärt die "
                    "Entscheidung nicht."),
        )  # fmt: skip
        more.addRow(self.evidence_repair)
        more.addRow(self.repair_hint)
        examples = QHBoxLayout()
        examples.addWidget(QLabel(self.tr("je Code")))
        examples.addWidget(self.examples_per_code)
        examples.addWidget(self.examples_button)
        examples.addWidget(self.examples_off)
        examples.addStretch()
        examples_tip = self.tr(
            "Stellt je Code einige Einheiten des Übungsteils mit ihrem Gold als Beispiele in die "
            "Anweisungen (Few-Shot). Standardmäßig aus: Ankerbeispiele im Codebuch sind die "
            "neutralere Wahl, und Beispiel-Einheiten zählen in keiner Auswertung."
        )
        self.examples_per_code.setToolTip(examples_tip)
        tip_row(more, self.tr("Beispiele aus dem Übungsteil (Dev)"), examples, examples_tip)
        more.addRow(self.examples_state)

        settings = QGroupBox(self.tr("Einstellungen"))
        settings_layout = QVBoxLayout(settings)
        settings_layout.addLayout(form)
        settings_layout.addWidget(self.shared_hint)
        settings_layout.addWidget(self.advanced)
        settings_layout.addWidget(self.error)
        settings_layout.addWidget(
            hint_label(
                self.tr(
                    "Mehr Durchläufe ergänzen die bisherige Kodierung. Jede andere Änderung "
                    "startet beim nächsten Lauf eine neue Kodierung; frühere Ergebnisse bleiben "
                    "im Protokoll."
                )
            )
        )
        settings_layout.addStretch()

        self.item_combo = QComboBox()
        self.item_combo.currentIndexChanged.connect(self._preview)
        self.run_spin = _spin(1, 20)
        self.run_spin.setToolTip(
            self.tr("Welcher Durchlauf gezeigt wird. Mit gemischter Code-Reihenfolge sieht jeder "
                    "Durchlauf die Codes in anderer Reihenfolge.")
        )  # fmt: skip
        self.run_spin.valueChanged.connect(self._preview)
        self.stats = hint_label()
        self.system_view = QPlainTextEdit()
        self.user_view = QPlainTextEdit()
        for view in (self.system_view, self.user_view):
            view.setReadOnly(True)
        chooser = QHBoxLayout()
        chooser.addWidget(QLabel(self.tr("Einheit")))
        chooser.addWidget(self.item_combo, 1)
        chooser.addWidget(QLabel(self.tr("Durchlauf")))
        chooser.addWidget(self.run_spin)
        system_label = QLabel(self.tr("Anweisungen und Codebuch (System-Nachricht)"))
        system_label.setToolTip(
            self.tr("Für alle Einheiten gleich: Aufgabe, Codebuch und Ausgabeformat. Mit "
                    "gemischter Code-Reihenfolge ändert sich je Durchlauf nur die Reihenfolge.")
        )  # fmt: skip
        user_label = QLabel(self.tr("Text der Einheit (User-Nachricht)"))
        user_label.setToolTip(
            self.tr("Je Einheit verschieden: der zu kodierende Text, im Kontextmodus mit den "
                    "Nachbarzeilen.")
        )  # fmt: skip
        preview = QWidget()
        preview_layout = QVBoxLayout(preview)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.addLayout(chooser)
        preview_layout.addWidget(self.stats)
        preview_layout.addWidget(system_label)
        preview_layout.addWidget(self.system_view, 3)
        preview_layout.addWidget(user_label)
        preview_layout.addWidget(self.user_view, 1)

        splitter = QSplitter()
        splitter.addWidget(settings)
        splitter.addWidget(preview)
        splitter.setStretchFactor(1, 2)
        layout = QVBoxLayout(self)
        layout.addWidget(splitter)

    def refresh(self) -> None:
        if self.project is None:
            return
        coding = self.project.config.coding
        units = self.project.store.units()[:1]
        columns = list(units[0].source) if units else []
        self.target_column.blockSignals(True)
        fill_combo(
            self.target_column,
            [(self.tr("— alle Zeilen —"), None), *((c, c) for c in columns)],
            coding.units.target_column,
        )
        self.target_column.blockSignals(False)
        widgets = (self.unit_mode, self.n_before, self.n_after, self.n_runs, self.min_agree,
                   self.base_seed, self.max_signal_words, self.max_rationale_words,
                   self.adaptive, self.permute, self.evidence_repair)  # fmt: skip
        for widget in widgets:
            widget.blockSignals(True)
        self.unit_mode.setCurrentIndex(self.unit_mode.findData(coding.units.mode))
        self.n_before.setValue(coding.units.n_before)
        self.n_after.setValue(coding.units.n_after)
        self.n_runs.setValue(coding.n_runs)
        self.adaptive.setChecked(coding.adaptive)
        self.min_agree.setValue(coding.adaptive_min_agree)
        self.base_seed.setValue(coding.base_seed)
        self.permute.setChecked(coding.permute_codes)
        self.max_signal_words.setValue(coding.max_signal_words)
        self.max_rationale_words.setValue(coding.max_rationale_words)
        self.evidence_repair.setChecked(coding.evidence_repair)
        for widget in widgets:
            widget.blockSignals(False)
        self._enable_dependents()
        self._names_state()
        self._examples_state()
        self._fill_items()

    def _names_state(self) -> None:
        assert self.project is not None
        try:
            n = len(self.project.pseudonym_pairs)
        except ValueError as exc:
            self.names_state.setText(error_text(exc))
            return
        self.names_state.setText(
            self.tr("an: {0} Namen werden vor dem Senden ersetzt").format(n)
            if self.project.config.coding.pseudonyms_sha256 and n
            else self.tr("aus: Namen gehen unverändert an das Modell")
        )

    def _examples_state(self, note: str = "") -> None:
        assert self.project is not None
        examples = self.project.config.coding.examples
        self.examples_off.setEnabled(bool(examples))
        text = (
            self.tr("an: {0} Beispiele mit Gold im Prompt ({1}); sie zählen in keiner "
                    "Auswertung").format(
                len(examples), ", ".join(f"{e.item_id} {'/'.join(e.codes)}" for e in examples)
            )
            if examples
            else self.tr("aus: keine Beispiele aus den Daten (Standard)")
        )  # fmt: skip
        self.examples_state.setText("\n".join(x for x in (note, text) if x))
        has_split = bool(self.project.store.split())
        self.examples_button.setEnabled(has_split)
        self.examples_button.setToolTip(
            ""
            if has_split
            else self.tr("Erst im Tab Splits & Freeze eine Aufteilung (Split) erzeugen: "
                         "Beispiele kommen nur aus dem Übungsteil (Dev).")
        )  # fmt: skip
        if examples or note:  # vom Standard abweichend: den Bereich zeigen
            self.advanced.toggle.setChecked(True)

    def _draw_examples(self) -> None:
        if self.project is None:
            return
        try:
            examples = draw_examples(self.project, self.examples_per_code.value())
        except ValueError as exc:
            self.error.setText(error_text(exc))
            return
        self._set_examples(examples)

    def _set_examples(self, examples: list, note: str = "") -> None:
        assert self.project is not None
        coding = self.project.config.coding.model_copy(update={"examples": examples})
        self.project.update(coding=coding)
        self.error.clear()
        self._examples_state(note)
        self.changed.emit()
        self._fill_items()

    def _edit_names(self) -> None:
        if self.project is None:
            return
        try:
            dialog = PseudonymDialog(self.project, self, also_frozen=self.frozen_systems())
        except ValueError as exc:  # Liste unlesbar: neu anlegen
            QMessageBox.warning(self, self.tr("Namensliste"), error_text(exc))
            dialog = PseudonymDialog(self.project, self, start_empty=True)
        if not dialog.exec():
            return
        try:
            self.project.set_pseudonyms(dialog.pairs())
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, self.tr("Namensliste"), error_text(exc))
            return
        self._names_state()
        self.changed.emit()
        self._fill_items()

    def _enable_dependents(self) -> None:
        # Weitere Codesysteme kodieren dieselben Einheiten mit derselben Namensliste.
        own = self.project is None or not is_codesystem(self.project)
        context = self.unit_mode.currentData() == "context"
        self.unit_mode.setEnabled(own)
        self.n_before.setEnabled(own and context)
        self.n_after.setEnabled(own and context)
        context_tip = self.tr(
            "Nachbarzeilen derselben Gruppe, die das Modell mitliest, aber nicht kodiert: wie "
            "viele davor und danach."
        )
        if not context:
            context_tip += " " + self.tr("Nur bei „Zeile mit Kontextfenster“ einstellbar.")
        for widget in (self.context_label, self.n_before, self.n_after):
            widget.setToolTip(context_tip)
        self.target_column.setEnabled(own and self.unit_mode.currentData() != "holistic")
        self.names_button.setEnabled(own)
        self.shared_hint.setText(
            ""
            if own
            else self.tr("Weiteres Codesystem: Analyseeinheit, Kontext, Zielspalte und "
                         "Namensliste kommen aus dem Hauptprojekt.")
        )  # fmt: skip
        self.min_agree.setEnabled(self.adaptive.isChecked())
        cfg = self.project.config if self.project else None
        local = cfg is not None and any(
            p.kind == "ollama" for p in cfg.providers if p.key in {m.provider for m in cfg.panel}
        )
        self.repair_hint.setText(
            self.tr("Hinweis: Im Panel ist ein lokales Modell. Ist es klein (unter 10 Mrd. "
                    "Parameter), kostet die Nachfrage einen zweiten Aufruf und hilft selten.")
            if local and self.evidence_repair.isChecked()
            else ""
        )  # fmt: skip
        self.run_spin.setMaximum(self.n_runs.value())

    def _apply(self) -> None:
        if self.project is None:
            return
        try:
            # Mit dem bestehenden Stand zusammenführen: Felder, die dieser Tab nicht zeigt,
            # bleiben erhalten.
            coding = CodingConfig.model_validate(
                {
                    **self.project.config.coding.model_dump(),
                    "units": UnitSettings(
                        mode=self.unit_mode.currentData(),
                        n_before=self.n_before.value(),
                        n_after=self.n_after.value(),
                        target_column=self.target_column.currentData(),
                    ),
                    "n_runs": self.n_runs.value(),
                    "adaptive": self.adaptive.isChecked(),
                    "adaptive_min_agree": self.min_agree.value(),
                    "base_seed": self.base_seed.value(),
                    "permute_codes": self.permute.isChecked(),
                    "max_signal_words": self.max_signal_words.value(),
                    "max_rationale_words": self.max_rationale_words.value(),
                    "evidence_repair": self.evidence_repair.isChecked(),
                }
            )
            before = self.project.config.coding.units
            reset = bool(coding.examples) and (
                coding.units.mode != before.mode
                or coding.units.target_column != before.target_column
            )
            if reset:  # andere Einheiten: die Beispiele passen nicht mehr
                coding = coding.model_copy(update={"examples": []})
            self.project.update(coding=coding)
        except Exception as exc:
            self.error.setText(error_text(exc))
            return
        self.error.clear()
        if reset:
            self._examples_state(
                self.tr("Beispiele entfernt: Die Einheiten haben sich geändert; neu auswählen.")
            )
        self._enable_dependents()
        self.changed.emit()
        self._fill_items()

    def _fill_items(self) -> None:
        assert self.project is not None
        try:
            self._spec, self._items = prepare_prompts(self.project, sending=True)
        except Exception as exc:
            self._spec, self._items = None, []
            self.stats.setText(self.tr("Keine Vorschau: {0}").format(error_text(exc)))
            self.system_view.clear()
            self.user_view.clear()
            self.item_combo.clear()
            return
        self._budget = self._budget_note()  # rendert alle Einheiten: nur beim Laden
        current = self.item_combo.currentData()
        fill_combo(
            self.item_combo,
            ((item.item_id, i) for i, item in enumerate(self._items[:PREVIEW_ITEMS])),
            current if isinstance(current, int) and current < len(self._items) else 0,
        )
        self._preview()

    def _preview(self) -> None:
        if self.project is None or self._spec is None or not self._items:
            return
        index = self.item_combo.currentData() or 0
        item = self._items[index]
        cfg = self.project.config
        run = self.run_spin.value() - 1  # angezeigt ab 1, gezählt ab 0
        seed = cfg.coding.base_seed + run
        prompt = render(self._spec, item, seed)
        self.system_view.setPlainText(prompt.system)
        self.user_view.setPlainText(prompt.user)
        n_items, n_runs, n_models = len(self._items), cfg.coding.n_runs, len(cfg.panel)
        lines = [
            self.tr("Durchlauf {0} (Startwert {1}): ≈ {2} Token Anweisungen und Codebuch + {3} "
                    "Token Text der Einheit").format(
                run + 1, seed, estimate_tokens(prompt.system), estimate_tokens(prompt.user)
            ),
            self.tr("Insgesamt: {0} Einheiten × {1} Durchläufe × {2} Modelle = höchstens {3} "
                    "Modellaufrufe").format(n_items, n_runs, n_models, n_items * n_runs * n_models)
            if n_models
            else self.tr("Insgesamt: {0} Einheiten × {1} Durchläufe; noch kein Modell im Panel "
                         "(Tab Modelle).").format(n_items, n_runs),
        ]  # fmt: skip
        self.stats.setText("\n".join(lines + self._budget[0]))
        self.stats.setToolTip(self._budget[1])

    def _budget_note(self) -> tuple[list[str], str]:
        """Was noch offen ist (sichtbar) und die Token-Schätzung dazu (Tooltip)."""
        assert self.project is not None
        budget = run_budget(self.project)
        unit, longest = budget.longest
        lines = []
        if not self.project.config.panel:
            pass
        elif budget.calls == 0:
            lines.append(self.tr("Noch offen: nichts, mit diesen Einstellungen ist alles kodiert."))
        elif budget.calls_min < budget.calls:
            lines.append(
                self.tr("Noch offen: {0} bis {1} Aufrufe; bereits Kodiertes ist abgezogen, der "
                        "adaptive Stopp spart bei eindeutigen Einheiten.").format(
                    budget.calls_min, budget.calls
                )
            )  # fmt: skip
        else:
            lines.append(
                self.tr("Noch offen: {0} Aufrufe; bereits Kodiertes ist abgezogen.").format(
                    budget.calls
                )
            )
        seen = budget.tokens_out_seen
        back = (
            self.tr("nach den bisherigen Antworten etwa {0} bis {1}").format(*seen)
            if seen
            else self.tr("höchstens {0}").format(budget.tokens_out)
        )
        tip = [
            self.tr("Token sind Textbausteine (meist Wortteile), nach denen Anbieter abrechnen und "
                    "Längen begrenzen; geschätzt wird vorsichtig mit 3 Zeichen je Token."),
            self.tr("Längste Einheit {0}: ≈ {1} Token Text (Median {2}); Anweisungen und Codebuch "
                    "≈ {3} Token je Aufruf.").format(unit, longest, budget.median, budget.system),
            self.tr("Noch offen: ≈ {0} bis {1} Token hin, {2} Token zurück.").format(
                budget.tokens_in_min, budget.tokens_in, back
            ),
        ]  # fmt: skip
        if budget.outlier:
            lines.append(
                self.tr("Ausreißer: Einheit {0} ist mehr als dreimal so lang wie die übrigen. "
                        "Einheit prüfen oder kürzen.").format(unit)
            )  # fmt: skip
            tip.append(
                self.tr("Eine sehr lange Einheit treibt bei Ollama das Kontextfenster (num_ctx) "
                        "für alle Aufrufe hoch; bei API-Modellen kann sie es sprengen.")
            )  # fmt: skip
        return lines, "\n".join(tip)
