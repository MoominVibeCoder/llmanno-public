"""Tab „Evaluation“: Kennwerte gegen menschliche Kodierungen berechnen, anzeigen, exportieren."""

from __future__ import annotations

import math
import threading
from collections.abc import Callable
from html import escape
from pathlib import Path

from PySide6.QtCore import QT_TRANSLATE_NOOP, QByteArray, QRectF, QUrl
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from llmanno.core.config import ProjectConfig
from llmanno.core.evaluation import (
    SUBSET_LABELS,
    EvaluationReport,
    LLMComparison,
    evaluate,
    main_comparison,
    subgroup_options,
)
from llmanno.core.export.evaluation import export_evaluation
from llmanno.core.export.plots import prevalence_svg, reliability_svg, risk_coverage_svg
from llmanno.core.export.report import export_report, methods_paragraph
from llmanno.core.export.stats import export_stats
from llmanno.core.gold import gold_columns, gold_values
from llmanno.core.i18n import localized
from llmanno.core.metrics.alt_test import MIN_ANNOTATORS
from llmanno.core.metrics.bootstrap import Cancelled, Estimate, cancellable
from llmanno.core.metrics.rho import THRESHOLD as RHO_THRESHOLD
from llmanno.core.project import Project
from llmanno.core.validation import count_test_evaluations, freeze_intact
from llmanno.core.verdict import verdict as short_verdict
from llmanno.gui import common
from llmanno.gui.common import (
    Collapsible,
    Page,
    Task,
    advanced_setting,
    chosen_path,
    error_label,
    error_text,
    fill_combo,
    heading,
    hint_label,
    output_language_setting,
    project_task,
)
from llmanno.gui.gold_mapping import GoldMappingBox
from llmanno.gui.verdict_view import VerdictText

EPSILONS = [
    (QT_TRANSLATE_NOOP("EvaluationPage", "Expert:innen (ε = 0,2)"), 0.2),
    (QT_TRANSLATE_NOOP("EvaluationPage", "geschulte Kodierende (ε = 0,15)"), 0.15),
    (QT_TRANSLATE_NOOP("EvaluationPage", "Crowdworker (ε = 0,1)"), 0.1),
]
# Konfidenz-Signale der Kalibrierung in Klartext, das Kürzel wie im Export in Klammern
CALIBRATION_LABELS = {
    "p_sc": QT_TRANSLATE_NOOP("EvaluationPage", "Konsistenz (p_sc)"),
    "p_verbal": QT_TRANSLATE_NOOP("EvaluationPage", "Selbsteinschätzung (p_verbal)"),
    "panel": QT_TRANSLATE_NOOP("EvaluationPage", "Anteil zustimmender Modelle (panel)"),
}


def _num(value: float | None, digits: int = 3) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "–"
    return f"{value:.{digits}f}"


def _saves(low: float | None, original: float | None) -> bool:
    """Spart „Reasoning niedrig“ spürbar Ausgabe-Token (mindestens ein Fünftel)?"""
    return low is not None and bool(original) and low < 0.8 * original


def _est(e: Estimate | None) -> str:
    if e is None:
        return "–"
    return f"{_num(e.value)} <small>[{_num(e.low)}; {_num(e.high)}]</small>"


# Helle Hinterlegungen brauchen dunkle Schrift, sonst ist im Dunkelmodus nichts zu lesen.
DARK_TEXT = "#111827"


def _table(header: list[str], rows: list[list[str]], marked: int | None = None) -> str:
    """`marked`: Zeile, die hinterlegt wird (der Hauptvergleich)."""
    head = "".join(f"<th align='left'>{h}</th>" for h in header)
    mark = f" style='background:#e0f2fe;color:{DARK_TEXT}'"
    body = "".join(
        f"<tr{mark if n == marked else ''}>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>"
        for n, row in enumerate(rows)
    )
    return (
        f"<table cellpadding='4' cellspacing='0' border='1' style='border-collapse:collapse'>"
        f"<tr style='background:#f3f4f6;color:{DARK_TEXT}'>{head}</tr>{body}</table>"
    )


DETAILS_ANCHOR = "details"


class PlotBrowser(QTextBrowser):
    """Zeigt SVG-Grafiken: Qt-Rich-Text kennt kein <svg>, also als Bild über loadResource."""

    def __init__(self) -> None:
        super().__init__()
        self.images: dict[str, QImage] = {}
        self._count = 0

    def image(self, svg: str) -> str:
        # Fortlaufende Namen: Das Dokument hält geladene Bilder unter ihrem Namen fest.
        self._count += 1
        name = f"plot:{self._count}"
        self.images[name] = _svg_image(svg)
        return f"<img src='{name}'>"

    def setHtml(self, html: str) -> None:  # noqa: N802 (Qt-Name)
        self.images = {k: v for k, v in self.images.items() if f"'{k}'" in html}
        super().setHtml(html)

    def loadResource(self, kind: int, url: QUrl) -> object:  # noqa: N802 (Qt-Name)
        image = self.images.get(url.toString())
        return image if image is not None else super().loadResource(kind, url)


def _small(lines: list[str]) -> str:
    """Legende unter einer Tabelle: je Kennwert ein Satz."""
    return "<p><small>" + "<br>".join(escape(line) for line in lines) + "</small></p>"


def _svg_image(svg: str) -> QImage:
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    size = renderer.defaultSize()
    image = QImage(size * 2, QImage.Format.Format_ARGB32)
    image.setDevicePixelRatio(2)  # scharf auf hochauflösenden Bildschirmen
    image.fill(QColor("white"))  # auch im Dunkelmodus: dunkle Schrift braucht hellen Grund
    painter = QPainter(image)
    renderer.render(painter, QRectF(0, 0, size.width(), size.height()))
    painter.end()
    return image


class EvaluationPage(Page):
    help_topics = (
        "10. Evaluation",
        "Kennwerte lesen",
        "Prävalenz: PPI und dsl",
        "Prompt-Sensitivität",
    )

    def __init__(self) -> None:
        super().__init__()
        self._task: Task | None = None
        self._export_task: Task | None = None
        self._report: EvaluationReport | None = None
        self._basis: tuple | None = None  # _stamp() beim Start der Berechnung

        self.resamples = QSpinBox()
        self.resamples.setRange(100, 10000)
        self.resamples.setSingleStep(500)
        self.resamples.setValue(1000)
        self.resamples.setToolTip(
            self.tr("Wie oft der Bootstrap neu zieht, um die 95-%-Intervalle zu schätzen. Mehr "
                    "Ziehungen: stabilere Intervalle, längere Rechnung; 1000 reicht meist.")
        )  # fmt: skip
        self.seed = QSpinBox()
        self.seed.setRange(0, 1_000_000)
        self.seed.setToolTip(
            self.tr("Derselbe Startwert ergibt dieselben Intervalle; so lässt sich die "
                    "Auswertung wiederholen.")
        )  # fmt: skip
        self.subset = QComboBox()
        self.subset.setToolTip(
            self.tr("Welche Einheiten ausgewertet werden. Bis zum Einfrieren nur der Übungsteil "
                    "(Dev); jede Auswertung des Prüfteils (Test) wird danach gezählt.")
        )  # fmt: skip
        self.subset_info = hint_label()
        self.subgroup = QComboBox()
        self.subgroup.setToolTip(
            self.tr("Kennwerte zusätzlich getrennt nach dieser Variable, etwa je Sprecher: Kodiert "
                    "das LLM manche Teile der Daten schlechter?")
        )  # fmt: skip
        self.model = QComboBox()
        self.model.setToolTip(
            self.tr("Panel: die gemeinsame Entscheidung aller Modelle. Ein einzelnes Modell zeigt, "
                    "wie gut es allein kodiert.")
        )  # fmt: skip
        self.epsilon = QComboBox()
        fill_combo(self.epsilon, [(self.tr(label), value) for label, value in EPSILONS])
        self.epsilon.currentIndexChanged.connect(self._apply_settings)
        self.compute_button = QPushButton(self.tr("Kennwerte berechnen"))
        self.compute_button.clicked.connect(self.compute)
        self.cancel_button = QPushButton(self.tr("Abbrechen"))
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        self._cancel: threading.Event | None = None
        self.export_button = QPushButton(self.tr("Kennwerte als XLSX…"))
        self.export_button.clicked.connect(self._export)
        self.report_button = QPushButton(self.tr("Methoden-Report (Word/HTML)…"))
        self.report_button.clicked.connect(self._export_report)
        self.methods_lang = QComboBox()
        fill_combo(
            self.methods_lang, [("Deutsch", "de"), ("English", "en")], output_language_setting()
        )
        self.methods_button = QPushButton(self.tr("Methodenabsatz kopieren"))
        self.methods_button.clicked.connect(self.copy_methods)
        self.methods_lang.setToolTip(
            self.tr("Gilt nur für den kopierten Methodenabsatz; Report und Replikationspaket "
                    "folgen dem Menü Sprache.")
        )  # fmt: skip
        self.normalized = QCheckBox(self.tr("Konfusionsmatrix zeilenweise in %"))
        self.normalized.setToolTip(
            self.tr("Jede Zeile in Prozent ihrer Referenzfälle; auf der Diagonale steht dann, "
                    "wie viele Fälle eines Codes das LLM findet (Recall).")
        )  # fmt: skip
        self.normalized.toggled.connect(self._render)
        self.all_metrics = QCheckBox(self.tr("Alle Kennwerte zeigen"))
        self.all_metrics.setChecked(advanced_setting())
        self.all_metrics.setToolTip(
            self.tr("Zeigt zusätzlich Scotts π, PABAK, Accuracy, MCC, Top-k, gewichtetes κ, λ, "
                    "Stabilität und Dawid-Skene. Kennwerte-XLSX und Report enthalten immer alle.")
        )  # fmt: skip
        self.all_metrics.toggled.connect(self._render)
        self.stats_combo = QComboBox()
        fill_combo(
            self.stats_combo, [("SPSS (.sav)", "sav"), ("Stata (.dta)", "dta"), ("R (.rds)", "rds")]
        )
        self.stats_button = QPushButton(self.tr("Daten für Statistik exportieren…"))
        self.stats_button.clicked.connect(self._export_stats)
        self.status = hint_label()
        self.error = error_label()

        form = QFormLayout()
        form.addRow(self.tr("Teilmenge"), self.subset)
        form.addRow("", self.subset_info)
        form.addRow(self.tr("LLM"), self.model)
        form.addRow(self.tr("Subgruppen nach"), self.subgroup)
        advanced = Collapsible()
        advanced_form = QFormLayout(advanced.body)
        advanced_form.setContentsMargins(0, 0, 0, 0)
        advanced_form.addRow(self.tr("Bootstrap-Ziehungen"), self.resamples)
        advanced_form.addRow(self.tr("Startwert (Seed)"), self.seed)
        advanced_form.addRow(self.tr("Alt-Test: Vergleichsgruppe"), self.epsilon)
        buttons = QHBoxLayout()
        buttons.addWidget(self.compute_button, 1)
        buttons.addWidget(self.cancel_button)
        export_box = QGroupBox(self.tr("Exportieren"))
        export_layout = QVBoxLayout(export_box)
        export_layout.addWidget(self.export_button)
        export_layout.addWidget(self.report_button)
        language = QFormLayout()
        language.addRow(QLabel(self.tr("Sprache des Methodenabsatzes")), self.methods_lang)
        export_layout.addLayout(language)
        export_layout.addWidget(self.methods_button)
        stats_row = QHBoxLayout()
        stats_row.addWidget(self.stats_combo)
        stats_row.addWidget(self.stats_button, 1)
        export_layout.addLayout(stats_row)

        self.gold_box = GoldMappingBox()
        self.gold_box.setTitle("")
        self.gold_box.changed.connect(self._gold_changed)
        # Eingeklappt, solange jeder Gold-Wert einem Code entspricht; sonst öffnet refresh().
        self.gold_fold = Collapsible(self.tr("Gold-Werte zuordnen"))
        gold_layout = QVBoxLayout(self.gold_fold.body)
        gold_layout.setContentsMargins(0, 0, 0, 0)
        gold_layout.addWidget(self.gold_box)

        self.reference = QComboBox()
        self.reference.currentIndexChanged.connect(self._render)
        # Die Details stehen unten im Bericht: nach der Auswahl dorthin springen
        self.reference.activated.connect(lambda: self.view.scrollToAnchor(DETAILS_ANCHOR))

        left = QWidget()
        left.setMaximumWidth(420)
        left_layout = QVBoxLayout(left)
        left_layout.addLayout(form)
        left_layout.addWidget(advanced)
        left_layout.addLayout(buttons)
        left_layout.addWidget(self.status)
        left_layout.addWidget(self.error)
        left_layout.addWidget(
            hint_label(self.tr("Details (je Code, Konfusion, Kalibrierung) für:"))
        )
        left_layout.addWidget(self.reference)
        left_layout.addWidget(self.normalized)
        left_layout.addWidget(self.all_metrics)
        left_layout.addWidget(export_box)
        left_layout.addWidget(self.gold_fold)
        left_layout.addStretch()

        self.view = PlotBrowser()
        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(self.view)
        splitter.setStretchFactor(1, 3)
        layout = QVBoxLayout(self)
        layout.addWidget(splitter)

    @property
    def report(self) -> EvaluationReport | None:
        """Die Auswertung, solange sie zum Stand des Projekts passt; sonst None.

        Exporte (Report, Kennwerte, metrics.json im Paket) sollen keine Kennwerte eines
        anderen Projekts oder eines überholten Stands enthalten."""
        if self._report is None or self._basis != self._stamp():
            return None
        return self._report

    def _stamp(self) -> tuple | None:
        """Wovon die Auswertung abhängt: Projekt, Konfiguration, Aufrufe, Reviews, Split."""
        if self.project is None:
            return None
        store = self.project.store
        return (
            self.project,
            self.project.config.model_dump_json(),
            store.revision(),
            tuple(store.split().items()),
        )

    def cancel(self) -> None:
        """Laufende Berechnung abbrechen; der Bootstrap prüft das je Ziehung."""
        if self._cancel is not None:
            self._cancel.set()

    def set_project(self, project: Project | None, refresh: bool = True) -> None:
        self.cancel()  # Kennwerte des alten Projekts braucht niemand mehr
        self._report = self._basis = None
        self.view.clear()
        self.status.clear()
        self.error.clear()
        self.gold_box.refresh(None)
        self.reference.clear()
        super().set_project(project, refresh)

    @property
    def busy(self) -> bool:
        return any(t is not None and t.running for t in (self._task, self._export_task))

    # --- Stand ---------------------------------------------------------------------------------

    def refresh(self) -> None:
        if self.project is None:
            return
        gold = self.project.config.gold
        self.epsilon.blockSignals(True)
        index = self.epsilon.findData(gold.epsilon)
        self.epsilon.setCurrentIndex(index if index >= 0 else 0)
        self.epsilon.blockSignals(False)
        self.gold_box.refresh(self.project)
        self._fill_subsets()
        fill_combo(
            self.subgroup,
            [(self.tr("— keine —"), None), *((label, key) for key, label in
                                              subgroup_options(self.project.config))],
            self.subgroup.currentData(),
        )  # fmt: skip
        fill_combo(
            self.model,
            [(self.tr("Panel (alle Modelle)"), None)]
            + [(m.key, m.key) for m in self.project.config.panel],
            self.model.currentData(),
        )
        self.model.setEnabled(len(self.project.config.panel) > 1)
        cfg = self.project.config
        coders = len(cfg.data.mapping.gold) if cfg.data else 0
        _enable(
            self.epsilon,
            self.tr("Mit wem das LLM im Alt-Test verglichen wird. ε ist der Vorsprung, den es "
                    "wegen geringerer Kosten haben darf: je geübter die Menschen, desto größer."),
            None
            if coders >= MIN_ANNOTATORS
            else self.tr("Der Alt-Test braucht mindestens {0} Kodierende (Gold-Spalten, Tab "
                         "Daten); jetzt {1}.").format(MIN_ANNOTATORS, coders),
        )  # fmt: skip
        if self._gold_unmapped():
            self.gold_fold.toggle.setChecked(True)
        has_report = self.report is not None
        if self._report is not None and not has_report:
            self.status.setText(
                self.tr("Seit der Berechnung haben sich Läufe, Reviews, Split oder Einstellungen "
                        "geändert. Die Anzeige ist veraltet; bitte neu berechnen.")
            )  # fmt: skip
        _enable(
            self.export_button,
            self.tr("Alle Kennwerte mit Intervallen als Excel-Datei, auch die hier "
                    "ausgeblendeten."),
            None
            if has_report
            else self.tr("Erst „Kennwerte berechnen“; nach Änderungen an Läufen, Reviews oder "
                         "Einstellungen neu berechnen."),
        )  # fmt: skip
        ready = (
            None
            if cfg.data is not None and cfg.codebook is not None
            else self.tr("Erst Daten (Tab Daten) und Codebuch (Tab Codebuch) festlegen.")
        )
        # ohne Auswertung eben ohne Kennwerte
        _enable(
            self.report_button,
            self.tr("Bericht für den Methodenteil samt GUIDE-LLM-Checkliste; mit Kennwerten, "
                    "wenn eine aktuelle Auswertung berechnet ist."),
            ready,
        )  # fmt: skip
        _enable(
            self.methods_button,
            self.tr("Absatz für den Methodenteil in die Zwischenablage; mit Kennwerten, wenn "
                    "eine aktuelle Auswertung berechnet ist."),
            ready,
        )  # fmt: skip
        _enable(
            self.stats_button,
            self.tr("Eine Zeile je Einheit mit Codes, Aufteilung und Review-Status für SPSS, "
                    "Stata oder R. Vor dem Einfrieren nur der Übungsteil (Dev)."),
            None
            if self.project.store.count_calls()
            else self.tr("Noch keine Ergebnisse: erst im Tab Lauf kodieren."),
        )  # fmt: skip
        if self._report is None:
            self._placeholder()

    def _gold_unmapped(self) -> bool:
        """Gibt es Gold-Werte ohne Code? Dann gehört die Zuordnung aufgeklappt."""
        assert self.project is not None
        cfg = self.project.config
        if not gold_columns(cfg) or cfg.codebook is None or not cfg.codebook.allowed_ids():
            return False
        return bool(gold_values(cfg, self.project.store.units())[1])

    def _placeholder(self) -> None:
        """Vor der ersten Berechnung: was es braucht und was als Nächstes zu tun ist."""
        assert self.project is not None
        store = self.project.store
        checks = [
            (store.count_calls() > 0, self.tr("Ergebnisse eines Laufs (Tab Lauf)")),
            (
                bool(gold_columns(self.project.config))
                or any(r.mode == "blind" for r in store.reviews()),
                self.tr("Gold (Tab Daten) oder blinde Reviews (Tab Review)"),
            ),
        ]
        items = "".join(f"<li>{'✓' if ok else '✗'} {escape(text)}</li>" for ok, text in checks)
        needed = escape(self.tr("Voraussetzungen:"))
        self.view.setHtml(
            heading("h2", self.tr("Noch keine Auswertung"))
            + f"<p>{needed}</p><ul>{items}</ul><p>"
            + escape(self.tr("Dann links „Kennwerte berechnen“ klicken. Unter jeder Tabelle "
                             "erklärt eine Legende die Kennwerte."))
            + "</p>"
        )  # fmt: skip

    def _fill_subsets(self) -> None:
        """Mit Split bis zum Freeze nur Dev; Test und „alle“ (enthält Test) erst danach."""
        assert self.project is not None
        parts = self.project.store.split()
        if not parts:
            entries = [("all", self.tr("alle Einheiten"))]
            info = self.tr("Kein Split: Ausgewertet wird alles. Eine Test-Sperre gibt es erst mit "
                           "einem Split (Tab Splits & Freeze).")  # fmt: skip
        elif self.project.config.freeze is None:
            entries = [("dev", self.tr("Übungsteil (Dev)"))]
            info = self.tr("Prüfteil (Test) und „alle Einheiten“ sind bis zum Einfrieren "
                           "gesperrt.")  # fmt: skip
        else:
            entries = [
                ("dev", self.tr("Übungsteil (Dev)")),
                ("test", self.tr("Prüfteil (Test)")),
                ("all", self.tr("alle Einheiten")),
            ]
            intact = self.tr("intakt") if freeze_intact(self.project) else self.tr("gebrochen")
            info = self.tr("Freeze {0} · Test-Set bisher {1}-mal ausgewertet.").format(
                intact, count_test_evaluations(self.project)
            )
        fill_combo(self.subset, [(text, key) for key, text in entries], self.subset.currentData())
        self.subset_info.setText(info)

    def _confirm_test(self, subset: str) -> bool:
        assert self.project is not None
        if subset == "dev" or not self.project.store.split():
            return True
        text = self.tr(
            "Jede Auswertung des Test-Sets wird mit dem Instrument-Hash protokolliert und im "
            "Bericht gezählt (bisher {0}-mal). Wer zwischen zwei Test-Auswertungen nachschärft, "
            "entwertet den Test."
        ).format(count_test_evaluations(self.project))
        if not freeze_intact(self.project):
            text += "\n\n" + self.tr("Achtung: Der Freeze ist gebrochen.")
        answer = QMessageBox.question(self, self.tr("Test-Set auswerten?"), text)
        return answer == QMessageBox.StandardButton.Yes

    def _gold_changed(self) -> None:
        self.changed.emit()
        self.refresh()  # meldet die Auswertung als veraltet

    def _apply_settings(self) -> None:
        if self.project is None:
            return
        gold = self.project.config.gold.model_copy(update={"epsilon": self.epsilon.currentData()})
        try:
            self.project.update(gold=gold)
        except Exception as exc:
            self.error.setText(error_text(exc))
            return
        self.changed.emit()

    # --- Berechnen -----------------------------------------------------------------------------

    def compute(self) -> None:
        if self.project is None or (self._task and self._task.running):
            return
        subset = self.subset.currentData() or "all"
        if not self._confirm_test(subset):
            return
        self.error.clear()
        self.status.setText(
            self.tr("Berechne … (Bootstrap mit {0} Ziehungen)").format(self.resamples.value())
        )
        self.compute_button.setEnabled(False)
        folder, config = self.project.folder, self.project.config
        resamples, seed = self.resamples.value(), self.seed.value()
        stamp = self._stamp()
        subgroup = self.subgroup.currentData()
        model = self.model.currentData()
        # Die Auswertung liest nur; sie läuft im Hintergrund mit eigener Store-Verbindung und
        # mit der Konfiguration vom Startzeitpunkt (inklusive ungespeicherter Zuordnungen).
        cancel = self._cancel = threading.Event()
        self._task = Task(
            self,
            lambda report: _evaluate_in_thread(
                folder, config, subset, resamples, seed, subgroup, model, cancel
            ),
        )
        self._task.done.connect(lambda report: self._show(report, stamp))
        self._task.failed.connect(lambda exc: self._failed(exc, stamp))
        self._task.finished.connect(lambda: self.compute_button.setEnabled(True))
        self._task.finished.connect(lambda: self.cancel_button.setEnabled(False))
        self.cancel_button.setEnabled(True)
        self._task.start()

    def _failed(self, exc: BaseException, stamp: tuple | None) -> None:
        if stamp is not None and stamp[0] is self.project:
            if isinstance(exc, Cancelled):
                self.status.setText(self.tr("Berechnung abgebrochen."))
                return
            self.error.setText(error_text(exc))
            self.status.clear()

    def _show(self, report: EvaluationReport, stamp: tuple | None) -> None:
        if stamp is None or stamp[0] is not self.project:
            return  # gestartet für ein anderes, inzwischen geschlossenes Projekt
        self._report, self._basis = report, stamp
        self.status.setText(
            self.tr("{0} von {1} Einheiten mit gültigem LLM-Ergebnis ausgewertet.").format(
                report.n_scored, report.n_items
            )
        )
        fill_combo(
            self.reference,
            [(f"LLM vs. {c.reference}", i) for i, c in enumerate(report.comparisons)],
            self.reference.currentData(),
        )
        self.refresh()
        self._render()

    # --- Darstellung ---------------------------------------------------------------------------

    def _render(self) -> None:
        report = self._report
        if report is None:
            return  # Platzhalter oder leer: setzen refresh() und set_project()
        full = self.all_metrics.isChecked()
        parts = [heading("h2", self.tr("Auswertung"))]
        # Wie die Tabelle: die Konsens-Spalte des Berichts, nicht der aktuellen Zuordnung
        if (short := short_verdict(report, report.gold.consensus)) is not None:
            parts.append(VerdictText().html(short))
        parts.append(
            "<p>"
            + escape(
                self.tr(
                    "{0} von {1} Einheiten mit gültigem LLM-Ergebnis · Bootstrap {2} Ziehungen, "
                    "Seed {3}, {4} · Werte mit 95-%-Konfidenzintervall in eckigen Klammern"
                ).format(
                    report.n_scored,
                    report.n_items,
                    report.resamples,
                    report.seed,
                    self.tr("nach Gruppen gezogen")
                    if report.clustered
                    else self.tr("über Einheiten"),
                )
            )
            + "</p>"
        )
        scope = self.tr("Teilmenge: {0}").format(self.tr(SUBSET_LABELS[report.subset]))
        scope += " · " + self.tr("LLM: {0}").format(report.model or self.tr("Panel"))
        if report.instrument_hash:
            intact = self.tr("intakt") if report.freeze_intact else self.tr("gebrochen")
            scope += " · " + self.tr("Instrument-Hash {0}… (Freeze {1})").format(
                report.instrument_hash[:12], intact
            )
        if report.has_split:
            scope += " · " + self.tr("Test-Auswertungen bisher: {0}").format(
                report.test_evaluations
            )
        parts.append(f"<p>{escape(scope)}</p>")
        if report.warnings:
            items = "".join(
                f"<li>{escape(localized(w, common.UI_LANGUAGE))}</li>" for w in report.warnings
            )
            parts.append(
                "<p style='color:#92400e'><b>"
                + escape(self.tr("Hinweise"))
                + f"</b></p><ul>{items}</ul>"
            )

        parts.append(heading("h3", self.tr("LLM gegen Referenz")))
        main = main_comparison(report.comparisons, report.gold.consensus)
        rows = []
        for c in report.comparisons:
            a = c.agreement
            name = escape(c.reference)
            if c is main:
                label = escape(self.tr("Hauptvergleich"))
                name = f"<b>{name}</b><br><small>{label}</small>"
            row = [name, str(a.n), _est(a.percent), _est(a.kappa), _est(a.ac1), _est(a.alpha)]
            if full:
                row += [_est(a.scott_pi), _est(a.brennan_prediger), _est(c.accuracy)]
            row.append(_est(c.macro_f1))
            if full:
                row += [
                    _est(c.mcc) if c.mcc else "–",
                    " · ".join(f"Top-{k} {_est(v)}" for k, v in c.top_k.items()) or "–",
                ]
            rows.append(row)
        header = [self.tr("Referenz"), "n", self.tr("% Übereinst."), "Cohens κ", "Gwets AC1",
                  "Krippendorffs α"]  # fmt: skip
        if full:
            header += ["Scotts π", "BP/PABAK", "Accuracy"]
        header.append("Macro-F1")
        if full:
            header += ["MCC", "Top-k"]
        marked = report.comparisons.index(main) if main is not None else None
        parts.append(_table(header, rows, marked))
        legend = [
            self.tr("Hauptvergleich (hinterlegt): die wichtigste Referenz, Konsens, sonst Mehrheit "
                    "der Kodierenden, sonst die zufällige Review-Stichprobe. Werte in eckigen "
                    "Klammern: 95-%-Intervall."),
            self.tr("% Übereinstimmung: Anteil gleich kodierter Einheiten, ohne Abzug zufälliger "
                    "Treffer; nie allein berichten."),
            self.tr("Cohens κ: Übereinstimmung, bereinigt um zufällige Treffer; 0 = wie Zufall, "
                    "1 = perfekt, ab etwa 0,6 deutlich, ab 0,8 fast vollständig (Landis & Koch "
                    "1977)."),
            self.tr("Gwets AC1: wie κ, aber robust, wenn Codes sehr ungleich häufig sind; ist κ "
                    "trotz hoher Übereinstimmung niedrig (Kappa-Paradox), AC1 mitlesen."),
            self.tr("Krippendorffs α: wie κ, auch für mehrere Kodierende und fehlende Werte; ab "
                    "0,800 belastbar, ab 0,667 vorläufig (Krippendorff 2018)."),
            self.tr("Macro-F1: Mittel der F1-Werte je Code, jeder Code zählt gleich, auch "
                    "seltene; 1 = perfekt."),
        ]  # fmt: skip
        if full:
            legend += [
                self.tr("Scotts π: wie κ, mit einer gemeinsamen Code-Verteilung für LLM und "
                        "Referenz."),
                self.tr("BP/PABAK: um Zufall bereinigt, als wären alle Codes gleich häufig; hilft "
                        "beim Kappa-Paradox."),
                self.tr("Accuracy: Anteil der Einheiten, die das LLM wie die Referenz kodiert."),
                self.tr("MCC: Korrelation von LLM und Referenz über alle Codes; 0 = wie Zufall, "
                        "1 = perfekt."),
                self.tr("Top-k: Anteil der Einheiten, bei denen der Referenz-Code unter den k "
                        "wahrscheinlichsten Codes des LLM ist."),
            ]  # fmt: skip
        else:
            legend.append(
                self.tr("Weitere Kennwerte (Scotts π, PABAK, Accuracy, MCC, Top-k, Stabilität, "
                        "Dawid-Skene) zeigt „Alle Kennwerte zeigen“ links.")
            )  # fmt: skip
        parts.append(_small(legend))
        if report.group_comparisons:
            parts.append(
                "<h3>"
                + escape(self.tr("Auf Ebene der Code-Gruppen ({0})").format(
                    ", ".join(report.groups)
                ))
                + "</h3>"
            )  # fmt: skip
            group_rows = [
                [escape(g.reference), str(g.agreement.n), _est(g.agreement.percent),
                 _est(g.agreement.kappa), _est(g.agreement.ac1), _est(g.agreement.alpha)]
                for g in report.group_comparisons
            ]  # fmt: skip
            parts.append(
                _table(
                    [
                        self.tr("Referenz"),
                        "n",
                        self.tr("% Übereinst."),
                        "Cohens κ",
                        "Gwets AC1",
                        "Krippendorffs α",
                    ],
                    group_rows,
                )  # fmt: skip
            )
            if report.group_baseline is not None:
                parts.append(
                    "<p>"
                    + escape(self.tr("Mensch-Mensch auf den Gruppen: Krippendorffs α {0}").format(
                        _num(report.group_baseline)
                    ))
                    + "</p>"
                )  # fmt: skip
        if report.prevalence:
            labeled, unlabeled = report.prevalence_n or (0, 0)
            parts.append(heading("h3", self.tr("Bias-korrigierte Prävalenz (PPI++)")))
            parts.append(
                "<p><b>"
                + escape(
                    self.tr("Für Häufigkeiten im Bericht PPI++ verwenden, nicht „LLM naiv“: "
                            "PPI++ korrigiert die LLM-Anteile mit der Zufallsstichprobe und ist "
                            "unverzerrt.")
                )
                + "</b></p>"
            )  # fmt: skip
            parts.append(
                "<p>"
                + escape(
                    self.tr(
                        "LLM-Labels aller {0} Einheiten, korrigiert um den Fehler auf der "
                        "zufälligen Review-Stichprobe ({1} Einheiten); PPI++ mit Power-Tuning λ "
                        "(1 = PPI, 0 = nur Stichprobe), Angelopoulos, Duchi & Zrnic 2023."
                    ).format(labeled + unlabeled, labeled)
                    + (
                        " "
                        + self.tr(
                            "„Alle Reviews gewichtet“ nutzt zusätzlich die blinden Reviews aus "
                            "dem Gate ({0} Einheiten insgesamt), gewichtet mit ihrer "
                            "Ziehwahrscheinlichkeit (Gate 1, Zufallsstichprobe n/N): "
                            "confidence-driven inference, Gligorić et al. 2025."
                        ).format(report.prevalence_weighted_n)
                        if report.prevalence_weighted_n
                        else ""
                    )
                )
                + "</p>"
            )
            parts.append(
                _table(
                    [
                        self.tr("Code"),
                        self.tr("LLM naiv"),
                        self.tr("nur Stichprobe"),
                        self.tr("PPI++"),
                    ]
                    + ([self.tr("λ")] if full else [])
                    + [self.tr("Intervallbreite PPI++ / nur Stichprobe")]
                    + ([self.tr("Alle Reviews gewichtet")] if report.prevalence_weighted_n else [])
                    + ([self.tr("λ gewichtet")] if report.prevalence_weighted_n and full else []),
                    [
                        [escape(r.code), _num(r.llm), _est(r.sample), _est(r.ppi)]
                        + ([_num(r.ppi_lambda, 2)] if full else [])
                        + [
                            _num((r.ppi.high - r.ppi.low) / (r.sample.high - r.sample.low), 2)
                            if r.sample.high > r.sample.low
                            else "–",
                        ]
                        + (
                            [_est(r.weighted) if r.weighted else "–"]
                            if report.prevalence_weighted_n
                            else []
                        )
                        + (
                            [_num(r.weighted_lambda, 2) if r.weighted_lambda is not None else "–"]
                            if report.prevalence_weighted_n and full
                            else []
                        )
                        for r in report.prevalence
                    ],
                )
            )
            parts.append(
                _small(
                    [
                        self.tr("LLM naiv: einfach ausgezählte LLM-Codes, durch systematische "
                                "Fehler verschoben. Nur Stichprobe: allein aus den blinden "
                                "Reviews der Zufallsstichprobe."),
                        self.tr("Intervallbreite unter 1: PPI++ schätzt genauer als die "
                                "Stichprobe allein."),
                    ]
                    + (
                        [self.tr("λ: Gewicht der LLM-Codes (1 = PPI, 0 = nur Stichprobe).")]
                        if full
                        else []
                    )
                )
            )  # fmt: skip
            labels = (self.tr("LLM naiv"), self.tr("nur Stichprobe"), self.tr("PPI++"))
            svg = prevalence_svg(report.prevalence, labels, ".")
            parts.append(f"<p>{self.view.image(svg)}</p>")
        for s in report.sensitivity:
            parts.append(heading("h3", self.tr("Prompt-Sensitivität: {0}").format(s.model)))
            parts.append(
                "<p>"
                + escape(
                    self.tr(
                        "{0} Einheiten, je Variante ein Lauf. Krippendorffs α über Original "
                        "und Umformulierungen: {1} (unter 0,8: Codes hängen am Wortlaut). "
                        "„ohne Kontext“ zählt nicht zum α."
                    ).format(s.n_items, _num(s.alpha))
                )
                + "</p>"
            )
            if (low := s.reasoning) is not None:
                parts.append(
                    "<p>"
                    + escape(
                        self.tr(
                            "„Reasoning niedrig“: Original-Prompt mit Reasoning-Aufwand low, "
                            "zählt nicht zum α. {0} der Codes wie im Original, im Mittel {1} "
                            "statt {2} Ausgabe-Token je Aufruf."
                        ).format(
                            _num(low.agreement), _num(low.tokens_out, 0),
                            _num(s.rows[0].tokens_out, 0),
                        )
                        + " "
                        + (
                            self.tr(
                                "Liegt die Übereinstimmung nahe 1 und hält α gegen die Referenz, "
                                "spart low im Tab Modelle Kontingent und Zeit; das ist ein neues "
                                "Instrument, also vor dem Freeze."
                            )
                            if _saves(low.tokens_out, s.rows[0].tokens_out)
                            else self.tr(
                                "Low spart hier kaum Ausgabe-Token; womöglich ignoriert der "
                                "Anbieter den Aufwand."
                            )
                        )
                    )
                    + "</p>"
                )  # fmt: skip
            parts.append(
                _table(
                    [
                        self.tr("Variante"),
                        "n",
                        self.tr("gleich wie Original"),
                        self.tr("α gegen Referenz"),
                        self.tr("Ø Token Ausgabe"),
                        self.tr("Ø Sekunden"),
                        *map(escape, report.labels),
                    ],
                    [
                        [
                            escape(r.variant),
                            str(r.n),
                            _num(r.agreement),
                            _num(r.alpha_reference),
                            _num(r.tokens_out, 0),
                            _num(r.seconds, 1),
                            *(_num(r.shares.get(c), 2) for c in report.labels),
                        ]
                        for r in s.rows
                    ],
                )  # fmt: skip
            )
        if report.differential:
            parts.append(heading("h3", self.tr("Messfehler nach Subgruppen")))
            parts.append(
                "<p>"
                + escape(
                    self.tr(
                        "Hängt die Fehlerrate gegen {0} von der Subgruppe ab? Wald-Test, "
                        "robust bzw. cluster-robust, Holm-korrigiert. Ein kleines p heißt: "
                        "Analysen nach dieser Variable mit LLM-Codes wären verzerrt."
                    ).format(report.differential_reference)
                )
                + "</p>"
            )
            parts.append(
                _table(
                    [
                        self.tr("Variable"),
                        self.tr("Fehlerrate je Ausprägung (n)"),
                        self.tr("p"),
                        self.tr("p Holm"),
                        self.tr("Deutung"),
                    ],
                    [
                        [
                            escape(t.variable),
                            escape(
                                "; ".join(
                                    f"{v} {rate:.0%} ({n})" for v, (n, rate) in t.levels.items()
                                )
                            ),
                            _num(t.p_value),
                            _num(t.p_holm),
                            escape(self._differential_verdict(t.p_holm)),
                        ]
                        for t in report.differential
                    ],
                )  # fmt: skip
            )
        if full and any(c.agreement.kappa_weighted is not None for c in report.comparisons):
            parts.append(
                "<p>"
                + escape(self.tr("Gewichtetes κ (quadratisch, ordinal): "))
                + ", ".join(
                    f"{escape(c.reference)} {_est(c.agreement.kappa_weighted)}"
                    for c in report.comparisons
                )
                + "</p>"
            )

        if report.baseline:
            parts.append(heading("h3", self.tr("Mensch-Mensch-Baseline")))
            rows = [
                [
                    f"{escape(a)} vs. {escape(b)}",
                    str(v.n),
                    _est(v.percent),
                    _est(v.kappa),
                    _est(v.ac1),
                    _est(v.alpha),
                ]
                for (a, b), v in report.baseline.pairs.items()
            ]
            parts.append(
                _table(
                    [
                        self.tr("Paar"),
                        "n",
                        self.tr("% Übereinst."),
                        "Cohens κ",
                        "Gwets AC1",
                        "Krippendorffs α",
                    ],
                    rows,
                )
            )
            parts.append(
                "<p>"
                + escape(
                    self.tr(
                        "Alle Kodierenden: α {0}, Fleiss' κ {1}, AC1 {2} · "
                        "mit LLM als zusätzlicher Kodierung: α {3}"
                    ).format(
                        _num(report.baseline.alpha),
                        _num(report.baseline.fleiss),
                        _num(report.baseline.ac1),
                        _num(report.baseline.alpha_with_llm),
                    )
                )
                + "</p>"
            )
            parts.append(
                _small(
                    [
                        self.tr("Wie gut stimmen die Menschen untereinander überein? Ein LLM, das "
                                "an diese Werte heranreicht, kodiert so gut wie eine weitere "
                                "geschulte Person.")
                    ]
                )
            )  # fmt: skip

        if report.alt_test:
            alt = report.alt_test
            verdict = self.tr("bestanden") if alt.passed else self.tr("nicht bestanden")
            parts.append(heading("h3", self.tr("Alt-Test (Calderon et al. 2025)")))
            parts.append(
                "<p>"
                + escape(
                    self.tr(
                        "ε = {0} · Winning Rate ω = {1} · Advantage Probability {2} · {3} (ω ≥ 0,5)"
                    ).format(
                        alt.epsilon,
                        _num(alt.winning_rate, 2),
                        _num(alt.advantage_probability),
                        verdict,
                    )
                )
                + "</p>"
            )
            rows = [
                [
                    escape(a.annotator),
                    str(a.n),
                    _num(a.rho_llm),
                    _num(a.rho_human),
                    _num(a.p_adjusted, 4),
                    self.tr("ja") if a.rejected else self.tr("nein"),
                    escape(a.test),
                ]
                for a in alt.annotators
            ]
            parts.append(
                _table(
                    [
                        self.tr("Person"),
                        "n",
                        self.tr("LLM gleich gut oder besser"),
                        self.tr("Person gleich gut oder besser"),
                        "p (BY)",
                        self.tr("LLM gewinnt"),
                        self.tr("Test"),
                    ],
                    rows,
                )
            )
            parts.append(
                _small(
                    [
                        self.tr("Der Alt-Test prüft je Person, ob das LLM mit den übrigen "
                                "Kodierenden mindestens so gut übereinstimmt wie diese Person, "
                                "bis auf den Vorsprung ε. Bestanden, wenn das LLM gegen "
                                "mindestens die Hälfte der Personen gewinnt (Winning Rate ω ≥ "
                                "0,5)."),
                        self.tr("Advantage Probability: mittlerer Anteil der Einheiten, in denen "
                                "das LLM gleich gut oder besser ist; zum Vergleich mehrerer LLMs. "
                                "p (BY): nach Benjamini-Yekutieli korrigiert."),
                    ]
                )
            )  # fmt: skip

        stability = report.stability
        if report.review_alpha:
            alpha, n = report.review_alpha
            text = self.tr("Blinde Reviews untereinander: Krippendorffs α {0} auf {1} Einheiten")
            parts.append(f"<p>{escape(text.format(_num(alpha), n))}</p>")
        if full:
            parts.append(heading("h3", self.tr("Stabilität")))
            intra = (
                ", ".join(f"{escape(m)}: α {_num(a)}" for m, a in stability.intra_alpha.items())
                or "–"
            )
            inter = _num(stability.inter_alpha) if stability.inter_alpha is not None else "–"
            parts.append(
                "<p>"
                + escape(self.tr("Über die Läufe eines Modells: "))
                + intra
                + "<br>"
                + escape(self.tr("Zwischen den Modellen des Panels: α "))
                + inter
                + "<br><i>"
                + escape(
                    self.tr(
                        "Übereinstimmung zwischen Modellen ist ein Unsicherheitssignal, "
                        "kein Gütemaß."
                    )
                )
                + "</i></p>"
            )

        if full and (ds := report.dawid_skene):
            accuracy = ", ".join(f"{escape(m)} {_num(a)}" for m, a in ds.accuracy.items())
            parts.append(
                "<p>"
                + escape(
                    self.tr(
                        "Dawid-Skene (Gegenprobe, ohne Gold): Gewichtet wären {0} von {1} "
                        "Einheiten anders kodiert; κ gegen {2}: {3} statt {4}. Geschätzte "
                        "Trefferquote je Modell: "
                    ).format(
                        ds.changed,
                        ds.n,
                        ds.reference,
                        _num(ds.kappa_weighted),
                        _num(ds.kappa_panel),
                    )
                )
                + accuracy
                + "<br><i>"
                + escape(
                    self.tr(
                        "Setzt unabhängige Fehler der Modelle voraus; bei LLMs meist verletzt. "
                        "Nur ein Hinweis, ob eine Gewichtung etwas ändern würde."
                    )
                )
                + "</i></p>"
            )

        index = self.reference.currentData()
        if isinstance(index, int) and index < len(report.comparisons):
            parts.append(self._details(report.comparisons[index]))
        self.view.setHtml("".join(parts))

    def _differential_verdict(self, p: float | None) -> str:
        if p is None or math.isnan(p):
            return "–"
        if p < 0.05:
            return self.tr("Fehlerrate hängt von der Variable ab")
        return self.tr("kein Hinweis auf Unterschiede")

    def _details(self, c: LLMComparison) -> str:
        parts = [
            f"<a name='{DETAILS_ANCHOR}'></a>"
            + heading("h3", self.tr("Details: LLM vs. {0} (je Code)").format(c.reference))
        ]
        extras = []
        if c.multi:
            m = c.multi
            extras += [
                f"Micro-F1 {_num(m.micro_f1)}",
                f"Exact Match {_num(m.exact_match)}",
                f"Jaccard {_num(m.jaccard)}",
                f"Hamming-Loss {_num(m.hamming_loss)}",
            ]
        if c.selective:
            s = c.selective
            extras += [
                self.tr("Abstain-Rate {0}").format(_num(s.abstain_rate)),
                self.tr("Coverage {0}").format(_num(s.coverage)),
                self.tr("Selektive Accuracy {0}").format(_num(s.selective_accuracy)),
            ]
        if extras:
            parts.append(f"<p>{escape(' · '.join(extras))}</p>")
        scores = (c.single or c.multi).per_class
        rows = []
        for code, s in scores.items():
            share = c.shares.get(code)
            test = c.mcnemar.get(code)
            warn = " ⚠" if code in c.low_support else ""
            rows.append(
                [
                    escape(code) + warn,
                    _num(s.precision),
                    _num(s.recall),
                    _num(s.f1),
                    str(s.support),
                    str(s.predicted),
                    _num(share.gold) if share else "–",
                    _num(share.llm) if share else "–",
                    _num(test.p_value, 4) if test else "–",
                    _num(c.code_kappa.get(code)),
                    _num(c.rho.get(code)),
                ]
            )
        parts.append(
            _table(
                [
                    self.tr("Code"),
                    "Precision",
                    "Recall",
                    "F1",
                    self.tr("Referenz n"),
                    self.tr("LLM n"),
                    self.tr("Anteil Ref."),
                    self.tr("Anteil LLM"),
                    "McNemar p",
                    self.tr("Cohens κ je Code"),
                    self.tr("ρ (Shaffer)"),
                ],
                rows,
            )
        )
        parts.append(
            _small(
                [
                    self.tr("Precision: Anteil der LLM-Vergaben eines Codes, die laut Referenz "
                            "stimmen. Recall: Anteil der Referenzfälle, die das LLM findet. F1 "
                            "verbindet beide; 1 = perfekt."),
                    self.tr("McNemar p: Vergibt das LLM den Code systematisch häufiger oder "
                            "seltener als die Referenz? Unter 0,05 heißt: ja."),
                    self.tr("⚠: weniger als 20 Referenzfälle; die Werte schwanken stark."),
                ]
            )
        )  # fmt: skip
        parts.append(
            "<p><small>"
            + escape(
                self.tr(
                    "κ je Code: Code ja/nein. ρ nach Shaffer (R-Paket rhoR): Anteil "
                    "simulierter Test-Sets gleicher Größe aus Grundgesamtheiten mit κ "
                    "zwischen 0,40 und {0}, die das beobachtete κ erreichen; ρ < 0,05 spricht "
                    "für κ > {0} in allen Einheiten. Die Fehlerrate gilt gemittelt über diesen "
                    "Bereich; liegt κ knapp unter {0}, sagt ρ öfter fälschlich „darüber“, umso "
                    "öfter, je größer das Test-Set. ρ setzt unabhängige Einheiten voraus."
                ).format(_num(RHO_THRESHOLD, 2))
            )
            + "</small></p>"
        )
        if c.subgroups:
            parts.append(self._subgroups(c))
        if c.stuart_maxwell:
            sm = c.stuart_maxwell
            parts.append(
                "<p>"
                + escape(
                    self.tr(
                        "Marginale Homogenität (Stuart-Maxwell): χ² = {0}, df = {1}, p = {2}"
                    ).format(_num(sm.statistic, 2), sm.df, _num(sm.p_value, 4))
                )
                + "</p>"
            )
        if c.single:
            parts.append(
                heading("h4", self.tr("Konfusionsmatrix (Zeilen = Referenz, Spalten = LLM)"))
            )
            parts.append(
                _confusion(
                    c.single.labels, c.single.confusion.tolist(), self.normalized.isChecked()
                )
            )
        parts.append(heading("h4", self.tr("Kalibrierung")))
        rows = [
            [escape(self._signal(key)), _num(cal.ece), _num(cal.ece_recalibrated), _num(cal.brier),
             _num(cal.auroc), _num(cal.aurc), str(cal.n)]
            for key, cal in c.calibration.items()
        ]  # fmt: skip
        parts.append(
            _table(
                [
                    self.tr("Konfidenz"),
                    "ECE",
                    self.tr("ECE rekalibriert"),
                    "Brier",
                    "AUROC",
                    "AURC",
                    "n",
                ],
                rows,
            )  # fmt: skip
        )
        parts.append(
            _small(
                [
                    self.tr("ECE: mittlere Abweichung zwischen Konfidenz und tatsächlicher "
                            "Trefferquote; 0 = perfekt kalibriert, unter 0,05 gut. Brier: "
                            "mittlerer quadrierter Fehler der Konfidenz; kleiner ist besser."),
                ]
            )
        )  # fmt: skip
        parts.append(
            "<p><small>"
            + escape(
                self.tr(
                    "AUROC: trennt die Konfidenz richtige von falschen Kodierungen (0,5 = "
                    "nein). AURC: mittlere Fehlerrate, wenn man nach absteigender Konfidenz "
                    "annimmt (kleiner ist besser). ECE rekalibriert: nach isotoner "
                    "Rekalibrierung, fünffach kreuzvalidiert. Verbalisierte Konfidenz nie "
                    "unkalibriert als Schwelle verwenden."
                )
            )
            + "</small></p>"
        )
        if c.by_runs:
            parts.append(
                _table(
                    [self.tr("Übereinstimmung der Läufe"), "n", self.tr("Trefferquote")],
                    [[escape(label), str(n), _num(acc)] for label, n, acc in c.by_runs],
                )
            )
        plots = " ".join(
            self.view.image(
                reliability_svg(
                    self._signal(key), cal, self.tr("Konfidenz"), self.tr("Trefferquote"), "."
                )
            )
            + " "
            + self.view.image(
                risk_coverage_svg(
                    self._signal(key), cal, self.tr("Abdeckung"), self.tr("Fehlerrate"), "."
                )
            )
            for key, cal in c.calibration.items()
            if cal.n
        )
        if plots:
            parts.append(f"<p>{plots}</p>")
            parts.append(
                "<p><small>"
                + escape(
                    self.tr(
                        "Reliability-Diagramm: Trefferquote je Konfidenzklasse gegen die "
                        "Diagonale (perfekt kalibriert). Risk-Coverage-Kurve: Fehlerrate der "
                        "angenommenen Einheiten, wenn man nach absteigender Konfidenz annimmt; "
                        "gestrichelt die Fehlerrate ohne Auswahl."
                    )
                )
                + "</small></p>"
            )
        for key, cal in c.calibration.items():
            bins = " · ".join(
                f"{_num(b.low, 1)}–{_num(b.high, 1)}: {_num(b.accuracy, 2)} (n {b.n})"
                for b in cal.bins
            )
            label = escape(self.tr("Trefferquote je Konfidenzklasse"))
            parts.append(f"<p><small>{escape(self._signal(key))}: {label} {bins}</small></p>")
        return "".join(parts)

    def _signal(self, key: str) -> str:
        return self.tr(CALIBRATION_LABELS[key]) if key in CALIBRATION_LABELS else key

    def _subgroups(self, c: LLMComparison) -> str:
        assert self._report is not None
        labels = self._report.labels
        multi = self._report.mode == "multi"
        rows = []
        for r in c.subgroups:
            warn = " ⚠" if r.n < 30 else ""
            shares = " · ".join(
                f"{escape(code)} {_num(r.shares[code].gold, 2)}/{_num(r.shares[code].llm, 2)}"
                for code in labels
                if code in r.shares
            )
            rows.append(
                [
                    escape(r.value) + warn,
                    str(r.n),
                    _num(r.percent),
                    _est(r.kappa),
                    _num(r.ac1),
                    _num(r.accuracy),
                    _num(r.macro_f1),
                    f"<small>{shares}</small>",
                ]
            )
        variable = dict(subgroup_options(self.project.config)).get(self._report.subgroup or "", "")
        title = escape(self.tr("Subgruppen: {0}").format(variable))
        legend = _small(
            [
                self.tr("⚠: weniger als 30 Einheiten; Unterschiede vorsichtig deuten. Anteile "
                        "Ref./LLM: Anteil jedes Codes in der Referenz und beim LLM.")
            ]
        )  # fmt: skip
        return (
            f"<h4>{title}</h4>"
            + _table(
                [
                    self.tr("Ausprägung"),
                    "n",
                    self.tr("% Übereinst."),
                    "Cohens κ",
                    "Gwets AC1",
                    "Exact Match" if multi else "Accuracy",
                    "Macro-F1",
                    self.tr("Anteile Ref./LLM"),
                ],
                rows,
            )
            + legend
        )

    # --- Export --------------------------------------------------------------------------------

    def _export(self) -> None:
        if self.report is None or self.project is None:
            return
        folder = self.project.folder / "exports"
        folder.mkdir(exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("Kennwerte speichern"),
            str(folder / f"{self.project.config.name}_kennwerte.xlsx"),
            self.tr("Excel-Dateien (*.xlsx)"),
        )
        if path:
            self.export_report(Path(path))

    def export_report(self, path: Path) -> None:
        report = self.report
        assert report is not None and self.project is not None
        try:
            export_evaluation(report, path)
        except Exception as exc:
            self.error.setText(error_text(exc))
            return
        self.project.store.log("exported", format="kennwerte", file=path.name)
        self.status.setText(self.tr("Gespeichert: {0}").format(path))

    def _export_report(self) -> None:
        if self.project is None:
            return
        folder = self.project.folder / "exports"
        folder.mkdir(exist_ok=True)
        path, selected = QFileDialog.getSaveFileName(
            self,
            self.tr("Methoden-Report speichern"),
            str(folder / f"{self.project.config.name}_methoden.docx"),
            self.tr("Word (*.docx);;HTML (*.html)"),
        )
        if path:
            self.export_report_to(chosen_path(path, selected))

    def export_report_to(self, path: Path) -> None:
        assert self.project is not None
        report, lang = self.report, output_language_setting()
        note = "" if report is not None else self._without_metrics()
        self._run_export(path, lambda own: export_report(own, report, path, lang), note)

    def _without_metrics(self) -> str:
        return " " + self.tr("(ohne Kennwerte: keine aktuelle Auswertung)")

    def copy_methods(self) -> None:
        if self.project is None:
            return
        report = self.report
        lang = self.methods_lang.currentData() or "de"
        self.error.clear()
        try:  # etwa noch keine aktiven Codes
            text = methods_paragraph(self.project, report, lang)
        except (ValueError, OSError) as exc:
            self.error.setText(error_text(exc))
            return
        QGuiApplication.clipboard().setText(text)
        note = "" if report is not None else self._without_metrics()
        self.status.setText(self.tr("Methodenabsatz in die Zwischenablage kopiert.") + note)

    def _run_export(self, path: Path, fn: Callable[[Project], object], note: str = "") -> None:
        """Report und Statistikdaten großer Projekte im Hintergrund."""
        assert self.project is not None
        if self._export_task is not None and self._export_task.running:
            self.status.setText(self.tr("Ein Export läuft noch; bitte kurz warten."))
            return
        self.error.clear()
        self.status.setText(self.tr("Exportiere {0} …").format(path.name))
        self._export_task = project_task(self, self.project, fn)
        self._export_task.done.connect(
            lambda _: self.status.setText(self.tr("Gespeichert: {0}").format(path) + note)
        )
        self._export_task.failed.connect(lambda exc: self.error.setText(error_text(exc)))
        self._export_task.start()

    def _export_stats(self) -> None:
        if self.project is None:
            return
        fmt = self.stats_combo.currentData()
        folder = self.project.folder / "exports"
        folder.mkdir(exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("Daten speichern"),
            str(folder / f"{self.project.config.name}.{fmt}"),
            f"*.{fmt}",
        )
        if path:
            self.export_stats_to(Path(path), fmt)

    def export_stats_to(self, path: Path, fmt: str) -> None:
        assert self.project is not None
        # Wie die Auswertung: vor dem Freeze nur Dev.
        self._run_export(path, lambda own: export_stats(own, path, fmt, only_dev=True))


def _enable(widget: QWidget, tip: str, reason: str | None = None) -> None:
    """Bedienbar ohne `reason`; sonst gesperrt, und der Tooltip nennt den Grund."""
    widget.setEnabled(reason is None)
    widget.setToolTip(reason or tip)


def _evaluate_in_thread(
    folder: Path,
    config: ProjectConfig,
    subset: str,
    resamples: int,
    seed: int,
    subgroup: str | None,
    model: str | None = None,
    cancel: threading.Event | None = None,
) -> EvaluationReport:
    own = Project.open(folder)
    try:
        own.config = config
        with cancellable(cancel or threading.Event()):
            return evaluate(
                own, subset=subset, resamples=resamples, seed=seed, subgroup=subgroup,
                model=model,
            )  # fmt: skip
    finally:
        own.close()


def _confusion(labels: list[str], matrix: list[list[int]], normalized: bool = False) -> str:
    """`normalized`: Anteile je Zeile, auf der Diagonale also der Recall je Referenz-Code."""
    peak = max((v for row in matrix for v in row), default=0) or 1
    head = "".join(f"<th>{escape(label)}</th>" for label in labels)
    body = []
    for label, row in zip(labels, matrix, strict=True):
        cells = []
        total = sum(row) or 1
        for j, value in enumerate(row):
            share = value / total if normalized else value / peak
            shown = f"{100 * value / total:.0f} %" if normalized else str(value)
            # Treffer blaugrün, Verwechslungen rot; Qt-Rich-Text kennt kein rgba(), also mischen.
            color = _blend((37, 107, 134) if labels[j] == label else (179, 38, 30), share)
            text = "white" if share > 0.55 else "black"
            cells.append(f"<td align='right' style='background:{color};color:{text}'>{shown}</td>")
        body.append(f"<tr><th align='left'>{escape(label)}</th>{''.join(cells)}</tr>")
    table = "<table cellpadding='4' border='1' style='border-collapse:collapse'>"
    return f"{table}<tr><th></th>{head}</tr>{''.join(body)}</table>"


def _blend(rgb: tuple[int, int, int], share: float) -> str:
    r, g, b = (round(255 + (c - 255) * share) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"
