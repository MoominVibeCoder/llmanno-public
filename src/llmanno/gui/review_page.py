"""Tab „Review“: Prüfliste (Queue) erzeugen, Einheiten blind oder assistiert kodieren."""

from __future__ import annotations

from html import escape

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, QEvent, QObject, QSettings
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from llmanno.core.aggregate import ItemResult
from llmanno.core.engine import prepare_prompts, results
from llmanno.core.review import (
    RANDOM,
    add_review,
    build_queue,
    gate_calibration,
    latest_reviews,
    mark_suggestion_shown,
    person,
)
from llmanno.core.store import QueueEntry
from llmanno.core.units import Item, Line
from llmanno.gui.common import (
    Collapsible,
    Page,
    error_label,
    error_text,
    fill_combo,
    hint_label,
    status_text,
)

# Regeln der Auswahl unsicherer Fälle (Gate): Schlüssel in ReviewConfig, Beschriftung, Tooltip
GATES = (
    (
        "gate_low_confidence",
        QT_TRANSLATE_NOOP("ReviewPage", "Konsistenz unter τ oder unentschieden"),
        QT_TRANSLATE_NOOP(
            "ReviewPage",
            "Der Anteil der Durchläufe mit demselben Code (Konsistenz) liegt unter τ, oder kein "
            "Code erreicht die nötige Mehrheit.",
        ),
    ),
    (
        "gate_dissent",
        QT_TRANSLATE_NOOP("ReviewPage", "Dissens im Panel"),
        QT_TRANSLATE_NOOP("ReviewPage", "Die Modelle des Panels entscheiden sich verschieden."),
    ),
    (
        "gate_abstain",
        QT_TRANSLATE_NOOP("ReviewPage", "Restkategorie gewählt"),
        QT_TRANSLATE_NOOP("ReviewPage", "Das LLM meint, dass kein Code des Codebuchs passt."),
    ),
    (
        "gate_unsupported",
        QT_TRANSLATE_NOOP("ReviewPage", "Signalwörter nicht im Text"),
        QT_TRANSLATE_NOOP(
            "ReviewPage",
            "Die Wörter, mit denen das LLM seinen Code belegt, stehen nicht wörtlich im Text.",
        ),
    ),
    (
        "gate_invalid",
        QT_TRANSLATE_NOOP("ReviewPage", "ungültig oder Fehler"),
        QT_TRANSLATE_NOOP(
            "ReviewPage", "Keine gültige Antwort: falsches Format oder Fehler beim Anbieter."
        ),
    ),
)
NAME_KEY = "review/name"


# Bedienung, mit der eine Person den Modus wechselt (Klick in der Liste, Tasten, Mausrad)
USER_INPUT = (QEvent.Type.MouseButtonRelease, QEvent.Type.KeyPress, QEvent.Type.Wheel)

# Tastenkürzel der Codes nach 0 (Restkategorie): erst Ziffern, dann Buchstaben.
SHORTCUT_KEYS = "123456789abcdefghijklmnopqrstuvwxyz"


class ReviewPage(Page):
    help_topics = (
        "9. Review",
        "Review",
    )

    def __init__(self) -> None:
        super().__init__()
        self._queue: list[QueueEntry] = []
        self._items: dict[str, Item] = {}
        self._results: dict[str, ItemResult] = {}
        self._codes: list[tuple[str, str]] = []
        self._multi = False
        self._index = 0

        # --- Prüfliste ---
        self.tau = QDoubleSpinBox()
        self.tau.setRange(0, 1)
        self.tau.setSingleStep(0.05)
        self.tau.setDecimals(2)
        self.tau.setToolTip(
            self.tr("Einheiten, deren Konsistenz (Anteil der Durchläufe mit demselben Code) unter "
                    "diesem Wert liegt, kommen in die Prüfliste. Höher heißt: mehr prüfen; 0,6 ist "
                    "ein guter Start.")
        )  # fmt: skip
        self.recalibrated = QCheckBox(self.tr("τ als geschätzte Trefferquote"))
        self.recalibrated.setToolTip(
            self.tr("τ gilt dann nicht für die Konsistenz selbst, sondern für die daraus "
                    "geschätzte Trefferquote, gelernt an den Einheiten des Übungsteils mit Gold "
                    "(mindestens 20). τ = 0,8 heißt dann: prüfen, wenn die geschätzte "
                    "Trefferquote unter 80 % liegt.")
        )  # fmt: skip
        self.recalibrated.toggled.connect(self._toggle_calibration)
        self.calibration_info = hint_label()
        self.calibration_error = error_label()
        self.calibration_error.hide()  # leere Zeilen liefen sonst als Lücke mit
        self.gates = {}
        for key, text, tip in GATES:
            self.gates[key] = QCheckBox(self.tr(text))
            self.gates[key].setToolTip(self.tr(tip))
        self.random_share = QSpinBox()
        self.random_share.setRange(0, 100)
        self.random_share.setSuffix(" %")
        self.random_share.setToolTip(
            self.tr("Anteil aller Einheiten, der unabhängig von der Unsicherheit zufällig in die "
                    "Prüfliste kommt. Nur diese Stichprobe liefert unverzerrte Kennwerte und "
                    "korrigierte Häufigkeiten; 10 % ist ein guter Start.")
        )  # fmt: skip
        self.seed = QSpinBox()
        self.seed.setRange(0, 1_000_000)
        self.seed.setToolTip(
            self.tr("Derselbe Startwert zieht dieselbe Zufallsstichprobe; so lässt sich die "
                    "Prüfliste wiederholen.")
        )  # fmt: skip
        for widget in (self.tau, self.random_share, self.seed):
            widget.valueChanged.connect(self._apply_settings)
        for box in self.gates.values():
            box.toggled.connect(self._apply_settings)
        self.queue_button = QPushButton(self.tr("Prüfliste erzeugen"))
        self.queue_button.clicked.connect(self.make_queue)
        self.queue_info = hint_label()
        self.settings_hint = hint_label(
            self.tr("Einstellungen geändert: Erst „Prüfliste erzeugen“ übernimmt sie; bis dahin "
                    "gilt die bestehende Prüfliste.")
        )  # fmt: skip
        self.settings_hint.hide()
        form = QFormLayout()
        form.addRow(self.tr("Schwelle „unsicher“ (τ)"), self.tau)
        form.addRow(self.tr("Zufallsstichprobe"), self.random_share)
        advanced = Collapsible()
        advanced_layout = QVBoxLayout(advanced.body)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.addWidget(hint_label(self.tr("Als unsicher gelten Einheiten mit:")))
        for box in self.gates.values():
            advanced_layout.addWidget(box)
        advanced_layout.addWidget(self.recalibrated)
        advanced_layout.addWidget(self.calibration_info)
        advanced_layout.addWidget(self.calibration_error)
        seed_form = QFormLayout()
        seed_form.addRow(self.tr("Startwert (Seed)"), self.seed)
        advanced_layout.addLayout(seed_form)
        queue_box = QGroupBox(self.tr("Prüfliste"))
        queue_layout = QVBoxLayout(queue_box)
        queue_layout.addWidget(
            hint_label(
                self.tr(
                    "Die Prüfliste verbindet die Auswahl unsicherer Fälle (Gate) mit einer "
                    "Zufallsstichprobe aus allen Einheiten. Unverzerrte Kennwerte liefert nur die "
                    "Stichprobe."
                )
            )
        )
        queue_layout.addLayout(form)
        queue_layout.addWidget(advanced)
        self.locked_hint = hint_label(
            self.tr("Eingefroren: Die Regeln der Prüfliste gehören zum Instrument und sind "
                    "gesperrt; „Prüfliste erzeugen“ geht trotzdem.")
        )  # fmt: skip
        self.locked_hint.hide()
        queue_layout.addWidget(self.locked_hint)
        queue_layout.addWidget(self.settings_hint)
        queue_layout.addWidget(self.queue_button)
        queue_layout.addWidget(self.queue_info)

        # --- Person und Modus ---
        self.name = QLineEdit(str(QSettings().value(NAME_KEY, "")))
        self.name.setPlaceholderText(self.tr("Name oder Kürzel"))
        self.name.setToolTip(
            self.tr("Pflicht: steht bei jeder Review. Dieselbe Person immer gleich schreiben.")
        )
        self.name.editingFinished.connect(self._name_changed)
        self.name.textChanged.connect(self._update_save)
        self.mode = QComboBox()
        fill_combo(
            self.mode,
            [
                (self.tr("blind (zählt als Gold)"), "blind"),
                (self.tr("assistiert (LLM-Vorschlag sichtbar, kein Gold)"), "assisted"),
            ],
        )
        self.mode.setToolTip(
            self.tr("Blind: nur Text und Codebuch, zählt als Gold. Assistiert: mit LLM-Vorschlag, "
                    "zählt nicht als Gold.")
        )  # fmt: skip
        self.mode.currentIndexChanged.connect(self._mode_changed)
        # Qt meldet „activated“ erst nach dem Wechsel. Damit die Rückfrage vor dem ersten
        # Vorschlag kommt, merkt sich die Seite Bedienung an Combo und Auswahlliste.
        self._mode_touched = False
        for widget in (self.mode, self.mode.view(), self.mode.view().viewport()):
            widget.installEventFilter(self)
        person_box = QGroupBox(self.tr("Person"))
        person_form = QFormLayout(person_box)
        person_form.addRow(self.tr("Name"), self.name)
        person_form.addRow(self.tr("Modus"), self.mode)
        person_form.addRow(
            hint_label(
                self.tr(
                    "Sichtbare LLM-Vorschläge verzerren das Urteil. Wer eine Einheit assistiert "
                    "gesehen hat, kann sie nicht mehr blind reviewen."
                )
            )
        )

        left = QWidget()
        left.setMaximumWidth(380)
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(queue_box)
        left_layout.addWidget(person_box)
        left_layout.addStretch()

        # --- Einheit ---
        self.position = QLabel()
        self.position.setWordWrap(True)
        self.legend = hint_label(
            self.tr("Fett: zu kodieren · grau: Kontext davor und danach, nur zum Verständnis")
        )
        self.text = QTextBrowser()
        self.suggestion = QLabel()
        self.suggestion.setWordWrap(True)
        self.code_box = QGroupBox(self.tr("Codes (Tasten 1–9, dann a–z; Restkategorie 0)"))
        self.code_layout = QVBoxLayout(self.code_box)
        self.group = QButtonGroup(self)
        self.note = QLineEdit()
        self.note.setPlaceholderText(self.tr("Notiz (optional)"))
        self.note.returnPressed.connect(self.save_and_next)
        self.saved = hint_label()
        self.error = error_label()
        self.back_button = QPushButton(self.tr("← Zurück"))
        self.back_button.clicked.connect(lambda: self._go(self._index - 1))
        self.skip_button = QPushButton(self.tr("Überspringen"))
        self.skip_button.setToolTip(
            self.tr("Weiter ohne Speichern. Übersprungene Einheiten der Zufallsstichprobe später "
                    "nachholen, sonst ist die Stichprobe nicht mehr zufällig.")
        )  # fmt: skip
        self.skip_button.clicked.connect(lambda: self._go(self._index + 1))
        self.save_button = QPushButton(self.tr("Speichern && weiter (Strg+Enter)"))
        self.save_button.clicked.connect(self.save_and_next)
        nav = QHBoxLayout()
        nav.addWidget(self.back_button)
        nav.addWidget(self.skip_button)
        nav.addStretch()
        nav.addWidget(self.save_button)
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.addWidget(self.position)
        center_layout.addWidget(self.legend)
        center_layout.addWidget(self.text, 2)
        center_layout.addWidget(self.suggestion)
        center_layout.addWidget(self.code_box)
        center_layout.addWidget(self.note)
        center_layout.addLayout(nav)
        center_layout.addWidget(self.saved)
        center_layout.addWidget(self.error)

        self.codebook = QTextBrowser()
        splitter = QSplitter()
        for widget in (left, center, self.codebook):
            splitter.addWidget(widget)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 2)
        layout = QVBoxLayout(self)
        layout.addWidget(splitter)

        for key in "0" + SHORTCUT_KEYS:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(lambda k=key: self._toggle(k))
        for keys in ("Ctrl+Return", "Ctrl+Enter"):
            QShortcut(QKeySequence(keys), self).activated.connect(self.save_and_next)

    @property
    def reviewer(self) -> str:
        return self.name.text().strip()

    @property
    def current(self) -> QueueEntry | None:
        return self._queue[self._index] if 0 <= self._index < len(self._queue) else None

    # --- Stand ---------------------------------------------------------------------------------

    def refresh(self) -> None:
        if self.project is None:
            return
        cfg = self.project.config
        widgets = (self.tau, self.recalibrated, self.random_share, self.seed, *self.gates.values())
        for widget in widgets:
            widget.blockSignals(True)
        self.tau.setValue(cfg.review.tau)
        self.recalibrated.setChecked(cfg.review.calibration is not None)
        self.calibration_info.setText(
            self.tr("Gelernt auf {0} Einheiten; zum Neulernen aus- und einschalten.").format(
                cfg.review.calibration.n
            )
            if cfg.review.calibration
            else ""
        )
        self.calibration_info.setVisible(cfg.review.calibration is not None)
        self.random_share.setValue(round(cfg.review.random_share * 100))
        self.seed.setValue(cfg.review.seed)
        for key, box in self.gates.items():
            box.setChecked(getattr(cfg.review, key))
        for widget in widgets:
            widget.blockSignals(False)
            # Gate-Regeln gehören zum eingefrorenen Instrument; die Queue lässt sich trotzdem bauen.
            widget.setEnabled(cfg.freeze is None)
        self.locked_hint.setVisible(cfg.freeze is not None)
        self.calibration_error.hide()
        self.codebook.setPlainText(self.project.codebook_text or "")
        try:
            _, items = prepare_prompts(self.project)
        except ValueError as exc:
            self._items, self._queue = {}, []
            self.queue_info.setText(error_text(exc))
            self.queue_button.setEnabled(False)
            self._show()
            return
        self.queue_button.setEnabled(True)
        self._items = {i.item_id: i for i in items}
        self._queue = [e for e in self.project.store.queue() if e.item_id in self._items]
        self._update_settings_hint()
        self._build_code_buttons()
        self._restart()

    def _build_code_buttons(self) -> None:
        assert self.project is not None and self.project.config.codebook is not None
        cb = self.project.config.codebook
        codes = [(c.id, c.label) for c in cb.active_codes()]
        if cb.abstain:
            codes.append((cb.abstain_id, cb.abstain_label))
        multi = self.project.config.mode == "multi"
        if (codes, multi) == (self._codes, self._multi):
            return
        self._codes, self._multi = codes, multi
        for button in self.group.buttons():
            self.group.removeButton(button)
            button.deleteLater()
        self.group.setExclusive(not multi)
        for index, (code, label) in enumerate(codes):
            key = (
                "0"
                if cb.abstain and code == cb.abstain_id
                else SHORTCUT_KEYS[index]
                if index < len(SHORTCUT_KEYS)
                else " "
            )
            text = f"{key}  {code}" + (f" — {label}" if label and label != code else "")
            button: QAbstractButton = QCheckBox(text) if multi else QRadioButton(text)
            button.setProperty("code", code)
            self.group.addButton(button)
            self.code_layout.addWidget(button)

    def _restart(self) -> None:
        """Zur ersten Einheit springen, die diese Person in diesem Modus noch nicht hat."""
        done = self._done()
        self._index = next(
            (n for n, e in enumerate(self._queue) if e.item_id not in done), len(self._queue)
        )
        self._show()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt-Name)
        if event.type() in USER_INPUT:
            self._mode_touched = True
        return super().eventFilter(watched, event)

    def _mode_changed(self) -> None:
        # Nur ein Wechsel durch die Person fragt nach, ein programmatischer nicht.
        touched, self._mode_touched = self._mode_touched, False
        assisted = self.mode.currentData() == "assisted"
        if assisted and touched and not self._confirm_assisted():
            self.mode.setCurrentIndex(self.mode.findData("blind"))
            return
        self._restart()

    def _confirm_assisted(self) -> bool:
        answer = QMessageBox.question(
            self,
            self.tr("Auf assistiert umschalten?"),
            self.tr(
                "Assistiert zeigt zu jeder Einheit sofort den LLM-Vorschlag. Wer ihn gesehen hat, "
                "kann diese Einheit danach nicht mehr blind kodieren; nur blinde Reviews zählen "
                "als Gold."
            ),
        )
        return answer == QMessageBox.StandardButton.Yes

    def _update_save(self) -> None:
        """Speichern erst mit Namen; der Tooltip sagt, was fehlt."""
        missing = self.current is not None and not self.reviewer
        self.save_button.setEnabled(self.current is not None and not missing)
        self.save_button.setToolTip(
            self.tr("Erst links unter „Person“ den Namen oder ein Kürzel eintragen.")
            if missing
            else ""
        )

    def _update_settings_hint(self) -> None:
        """Hinweis, wenn die Einstellungen nicht mehr denen der bestehenden Prüfliste gleichen."""
        assert self.project is not None
        created = [e for e in self.project.store.audit() if e.event == "review_queue_created"]
        used = created[-1].details.get("settings", {}) if created else {}
        current = self.project.config.review.model_dump(mode="json")
        # Nur protokollierte Schlüssel: Neue Einstellungen älterer Prüflisten zählen nicht.
        self.settings_hint.setVisible(
            bool(self._queue) and any(current.get(k) != v for k, v in used.items())
        )

    def _done(self) -> set[str]:
        if self.project is None:
            return set()
        mode, who = self.mode.currentData(), self.reviewer
        who = person(who)
        return {i for (i, p, m) in latest_reviews(self.project) if p == who and m == mode}

    def _info(self) -> None:
        if not self._queue:
            self.queue_info.setText(self.tr("Noch keine Prüfliste."))
            return
        drawn = sum(1 for e in self._queue if RANDOM in e.reasons)
        gated = sum(1 for e in self._queue if set(e.reasons) - {RANDOM})
        self.queue_info.setText(
            self.tr("{0} Einheiten: {1} unsicher, {2} zufällig gezogen · erledigt unter diesem "
                    "Namen und Modus: {3}").format(
                len(self._queue), gated, drawn, len(self._done() & {e.item_id for e in self._queue})
            )
        )  # fmt: skip

    def _show(self) -> None:
        self._info()
        entry = self.current
        self.error.clear()
        self.saved.clear()
        for widget in (self.code_box, self.note, self.skip_button):
            widget.setEnabled(entry is not None)
        self._update_save()
        self.back_button.setEnabled(self._index > 0)
        self.legend.setVisible(entry is not None)
        if entry is None:
            open_items = {e.item_id for e in self._queue} - self._done()
            if not self._queue:
                text = self.tr("Noch keine Prüfliste: links die Einstellungen prüfen und "
                               "„Prüfliste erzeugen“ klicken.")  # fmt: skip
            elif open_items:
                text = self.tr("Ende der Prüfliste; {0} Einheiten noch ohne Review unter diesem "
                               "Namen und Modus. „← Zurück“ führt zu ihnen.").format(
                    len(open_items)
                )  # fmt: skip
            else:
                text = self.tr("Prüfliste abgeschlossen. Die Kennwerte gegen die Reviews zeigt "
                               "der Tab Evaluation.")  # fmt: skip
            self.position.setText(text)
            self.text.clear()
            self.suggestion.clear()
            self._select([])
            return
        item = self._items[entry.item_id]
        self.position.setText(
            self.tr("Einheit {0} von {1} · ID {2}").format(
                self._index + 1, len(self._queue), entry.item_id
            )
        )
        self.text.setHtml(_item_html(item))
        assisted = self.mode.currentData() == "assisted"
        previous = latest_reviews(self.project).get(
            (entry.item_id, person(self.reviewer), self.mode.currentData())
        )
        suggested: list[str] = []
        if assisted and not self.reviewer:
            # Ohne Namen ließe sich nicht festhalten, wer den Vorschlag gesehen hat.
            self.suggestion.setText(
                self.tr("Für den assistierten Modus zuerst den Namen eintragen.")
            )
        elif assisted:
            mark_suggestion_shown(self.project, entry.item_id, self.reviewer)
            result = results(self.project, [entry.item_id])[entry.item_id]
            suggested = result.final if result.status == "ok" else []
            self.suggestion.setText(_suggestion_html(result))
        else:
            self.suggestion.clear()  # blind: kein Vorschlag, kein Grund für die Auswahl
        self._select(previous.codes if previous else suggested)
        self.note.setText(previous.note if previous else "")
        if previous:
            when = previous.ts.astimezone().strftime("%d.%m.%Y %H:%M")
            self.saved.setText(self.tr("Bereits gespeichert am {0}.").format(when))
        self.code_box.setFocus()

    def _select(self, codes: list[str]) -> None:
        exclusive = self.group.exclusive()
        self.group.setExclusive(False)  # sonst lässt sich ein Radioknopf nicht abwählen
        for button in self.group.buttons():
            button.setChecked(button.property("code") in codes)
        self.group.setExclusive(exclusive)

    def selected(self) -> list[str]:
        return [b.property("code") for b in self.group.buttons() if b.isChecked()]

    def _toggle(self, key: int | str) -> None:
        """Tastenkürzel: 0 = Restkategorie, dann 1–9 und a–z für die Codes."""
        if self.current is None or not self._codes:
            return
        cb = self.project.config.codebook if self.project else None
        abstain = cb.abstain_id if cb and cb.abstain else None
        key = str(key)
        if key == "0":
            code = abstain
        else:
            regular = [c for c, _ in self._codes if c != abstain]
            index = SHORTCUT_KEYS.find(key)
            code = regular[index] if 0 <= index < len(regular) else None
        button = next((b for b in self.group.buttons() if b.property("code") == code), None)
        if button is not None:
            button.click()

    # --- Aktionen ------------------------------------------------------------------------------

    def _apply_settings(self) -> None:
        if self.project is None:
            return
        review = self.project.config.review.model_copy(
            update={
                "tau": self.tau.value(),
                "random_share": self.random_share.value() / 100,
                "seed": self.seed.value(),
                **{key: box.isChecked() for key, box in self.gates.items()},
            }
        )
        self.project.update(review=review)
        self._update_settings_hint()
        self.changed.emit()

    def _toggle_calibration(self, on: bool) -> None:
        """Einschalten lernt die Abbildung (sie gehört dann zum Instrument), ausschalten
        verwirft sie."""
        if self.project is None:
            return
        self.calibration_error.hide()
        try:
            calibration = gate_calibration(self.project) if on else None
        except ValueError as exc:
            self.calibration_error.setText(error_text(exc))
            self.calibration_error.show()
            self.recalibrated.blockSignals(True)
            self.recalibrated.setChecked(False)
            self.recalibrated.blockSignals(False)
            return
        review = self.project.config.review.model_copy(update={"calibration": calibration})
        self.project.update(review=review)
        self.changed.emit()
        self.refresh()

    def make_queue(self) -> None:
        if self.project is None:
            return
        self.error.clear()
        reviewed = len({r.item_id for r in self.project.store.reviews()})
        if reviewed and self.project.store.queue():
            answer = QMessageBox.question(
                self,
                self.tr("Prüfliste neu ziehen?"),
                self.tr(
                    "Für {0} Einheiten gibt es schon Reviews. Mit geänderten Einstellungen zieht "
                    "die neue Prüfliste eine andere Zufallsstichprobe; die bisherige bleibt dann "
                    "unvollständig, und die korrigierten Häufigkeiten und der Vergleich mit der "
                    "Stichprobe verlieren ihre Grundlage."
                ).format(reviewed),
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            build_queue(self.project)
        except ValueError as exc:
            self.error.setText(error_text(exc))
            return
        self.refresh()

    def _name_changed(self) -> None:
        QSettings().setValue(NAME_KEY, self.reviewer)
        self._restart()

    def save_and_next(self) -> None:
        entry = self.current
        if self.project is None or entry is None:
            return
        try:
            add_review(
                self.project,
                entry.item_id,
                self.reviewer,
                self.mode.currentData(),
                self.selected(),
                self.note.text(),
            )
        except ValueError as exc:
            self.error.setText(error_text(exc))
            return
        QSettings().setValue(NAME_KEY, self.reviewer)
        self._go(self._index + 1)

    def _go(self, index: int) -> None:
        self._index = max(0, min(index, len(self._queue)))
        self._show()


def _lines(lines: tuple[Line, ...]) -> str:
    return "<br>".join(
        escape(f"{ln.speaker + ': ' if ln.speaker else ''}{ln.text}") for ln in lines
    ).replace("\n", "<br>")


def _item_html(item: Item) -> str:
    parts = []
    if item.before:
        parts.append(f"<p style='color:gray'>{_lines(item.before)}</p>")
    parts.append(f"<p style='font-size:12pt'><b>{_lines(item.lines)}</b></p>")
    if item.after:
        parts.append(f"<p style='color:gray'>{_lines(item.after)}</p>")
    return "".join(parts)


def _suggestion_html(result: ItemResult) -> str:
    if result.status != "ok":
        message = QCoreApplication.translate("ReviewPage", "Kein gültiges LLM-Ergebnis ({0}).")
        return escape(message.format(status_text(result.status)))
    scores = ", ".join(f"{s.code} {round(100 * s.p_sc)} %" for s in result.ranking)
    share = QCoreApplication.translate("ReviewPage", "Anteil der Durchläufe")
    title = QCoreApplication.translate("ReviewPage", "LLM-Vorschlag")
    return (
        "<b>" + escape(title) + f":</b> {escape(', '.join(result.final))} "
        f"<small>({escape(share)}: {escape(scores)})</small><br>"
        f"<i>{escape(result.rationale or '')}</i>"
    )
