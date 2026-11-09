"""Tab „Export“: Ergebnisse als XLSX, REFI-QDA für QDA-Programme."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from llmanno.core.config import ProjectConfig
from llmanno.core.evaluation import EvaluationReport
from llmanno.core.export.labelstudio import export_labelstudio
from llmanno.core.export.refi import export_qdc, export_qdpx
from llmanno.core.export.replication import export_replication
from llmanno.core.export.report import export_report
from llmanno.core.export.xlsx import export_xlsx
from llmanno.core.project import Project
from llmanno.core.validation import exposed_test_items
from llmanno.gui.common import (
    Collapsible,
    Page,
    Task,
    chosen_path,
    error_label,
    error_text,
    hint_label,
    output_language_setting,
    project_task,
)


class ExportPage(Page):
    exported = Signal()  # nach jedem gespeicherten Export (Schrittleiste)

    help_topics = (
        "11. Export",
        "Exporte",
        "REFI-QDA in QDA-Programmen",
        "Replikation",
        "GUIDE-LLM-Checkliste",
    )

    def __init__(self) -> None:
        super().__init__()
        # Liefert die zuletzt berechnete Auswertung (Tab Evaluation) für metrics.json.
        self.current_report: Callable[[], EvaluationReport | None] = lambda: None
        # Die übrigen Codesysteme (Name → Projekt) für gemeinsame XLSX- und REFI-QDA-Dateien.
        self.other_systems: Callable[[], dict[str, Project]] = lambda: {}
        self.all_systems = QCheckBox(
            self.tr(
                "Alle Codesysteme exportieren (XLSX: je eigene Blätter, REFI-QDA: je "
                "eigener Code-Zweig)"
            )
        )
        self.all_systems.setChecked(True)
        self.all_systems.toggled.connect(lambda _: self.refresh())
        self._task: Task | None = None
        self.xlsx_button = QPushButton(self.tr("Ergebnisse als XLSX exportieren…"))
        self.xlsx_button.clicked.connect(
            lambda: self._ask(
                "_ergebnisse.xlsx",
                self.tr("Excel-Dateien (*.xlsx)"),
                self.export_to,
                True,
                scope=self._chosen_systems(),
            )
        )
        self.qdpx_button = QPushButton(self.tr("REFI-QDA-Projekt (.qdpx) exportieren…"))
        self.qdpx_button.clicked.connect(
            lambda: self._ask(
                ".qdpx",
                self.tr("REFI-QDA-Projekt (*.qdpx)"),
                self.export_qdpx_to,
                True,
                scope=self._chosen_systems(),
            )
        )
        self.qdc_button = QPushButton(self.tr("Nur Codesystem (.qdc) exportieren…"))
        self.qdc_button.clicked.connect(
            lambda: self._ask(".qdc", self.tr("REFI-QDA-Codesystem (*.qdc)"), self.export_qdc_to)
        )
        self.ls_predictions = QCheckBox(self.tr("mit LLM-Vorschlägen (Codes und Signalwörter)"))
        self.ls_predictions.setToolTip(
            self.tr("Vorschläge lenken das Urteil wie eine assistierte Review; für blinde "
                    "Kodierung ausgeschaltet lassen.")
        )  # fmt: skip
        self.ls_button = QPushButton(self.tr("Label Studio (.json) exportieren…"))
        self.ls_button.clicked.connect(
            lambda: self._ask(
                "_labelstudio.json",
                self.tr("Label-Studio-Aufgaben (*.json)"),
                self.export_labelstudio_to,
                self.ls_predictions.isChecked(),
            )
        )
        self.report_button = QPushButton(self.tr("Methoden-Report (Word/HTML)…"))
        self.report_button.clicked.connect(
            lambda: self._ask(
                "_methoden.docx",
                self.tr("Word (*.docx);;HTML (*.html)"),
                self.export_report_to,
            )
        )
        self.include_texts = QCheckBox(
            self.tr("Rohtexte einschließen (Daten, Prompts, Antworten, Begründungen)")
        )
        self.include_texts.setToolTip(
            self.tr("Datenschutz: Das Paket enthält dann die Texte selbst, meist also "
                    "personenbezogene Daten. Nur weitergeben, wenn Einwilligung und "
                    "Datenschutzkonzept das erlauben.")
        )  # fmt: skip
        self.pseudonymize_ids = QCheckBox(
            self.tr("Einheiten- und Gruppen-IDs pseudonymisieren (E01, G01 …)")
        )
        self.pseudonymize_ids.setToolTip(
            self.tr("Nur ohne Rohtexte. Der Rerun stellt die Zuordnung aus den Originaldaten "
                    "wieder her; die Zuordnung selbst steht nicht im Paket.")
        )  # fmt: skip
        self.include_texts.toggled.connect(
            lambda texts: self.pseudonymize_ids.setEnabled(not texts)
        )
        self.replication_button = QPushButton(self.tr("Replikationspaket (.zip) exportieren…"))
        self.replication_button.clicked.connect(
            lambda: self._ask(
                "_replikation.zip",
                self.tr("ZIP-Archiv (*.zip)"),
                self.export_replication_to,
                True,
                partial=False,
            )  # fmt: skip
        )
        self.dev_only = QCheckBox(
            self.tr("Vor dem Freeze nur Dev exportieren (Test-Ergebnisse bleiben verborgen)")
        )
        self.dev_only.setChecked(True)
        self._dev_tip = self.tr(
            "Gilt für XLSX, REFI-QDA und Label Studio. Ohne den Schalter enthält der "
            "Export LLM-Ergebnisse der Test-Einheiten (XLSX und REFI-QDA auch Gold); "
            "das zählt im Methoden-Report als Test-Einsicht vor dem Freeze."
        )
        self.dev_only.setToolTip(self._dev_tip)
        self.status = hint_label()
        self.error = error_label()
        self.open_folder = QPushButton(self.tr("Ordner öffnen"))
        self.open_folder.hide()
        self.open_folder.clicked.connect(self._open_folder)
        self._saved: Path | None = None
        # Was ein Button liefert; deaktiviert steht stattdessen der Grund im Tooltip.
        self._tips = {
            self.xlsx_button: self.tr("Eine Zeile je Einheit samt Aufteilung und Review-Status, "
                                      "dazu alle Einzelläufe, Codes, Reviews, Hashes und "
                                      "Parameter."),
            self.report_button: self.tr("Bericht für den Methodenteil mit der GUIDE-LLM-"
                                        "Checkliste (Berichtsstandard für LLM-Studien); Sprache "
                                        "im Menü Sprache."),
            self.qdpx_button: self.tr("Für MAXQDA, ATLAS.ti, NVivo und QualCoder: dort über den "
                                      "Import für REFI-QDA-Projekte öffnen."),
            self.qdc_button: self.tr("Nur die Codes als REFI-QDA-Codesystem, etwa um das "
                                     "Codebuch in einem QDA-Programm anzulegen; ohne Texte und "
                                     "Kodierungen."),
            self.ls_button: self.tr("Aufgaben (.json) und passende Oberfläche (.xml) für Label "
                                    "Studio."),
            self.replication_button: self.tr("Alles, damit andere die Kodierung nachprüfen und "
                                             "mit „llmanno-rerun“ neu rechnen können."),
        }  # fmt: skip

        evaluate_box = QGroupBox(self.tr("Auswerten"))
        evaluate_layout = QVBoxLayout(evaluate_box)
        evaluate_layout.addWidget(
            hint_label(
                self.tr(
                    "Die XLSX-Datei enthält die Ergebnisse je Einheit, alle Einzelläufe, die "
                    "Code-Liste, die Reviews und alle Hashes und Parameter."
                )
            )
        )
        evaluate_layout.addWidget(self.xlsx_button)
        evaluate_layout.addWidget(
            hint_label(
                self.tr(
                    "Der Methoden-Report beschreibt Daten, Codebuch, Modelle, Läufe und die "
                    "GUIDE-LLM-Checkliste. Kennwerte enthält er, wenn im Tab Evaluation eine "
                    "aktuelle Auswertung berechnet ist; in der Pilotphase geht es auch ohne."
                )
            )
        )
        evaluate_layout.addWidget(self.report_button)
        evaluate_layout.addStretch()

        qda_box = QGroupBox(self.tr("QDA-Programme"))
        qda_layout = QVBoxLayout(qda_box)
        qda_layout.addWidget(
            hint_label(
                self.tr(
                    "Das REFI-QDA-Projekt öffnet sich in MAXQDA, ATLAS.ti, QualCoder und NVivo: "
                    "Texte je Gruppe, LLM-Kodierungen als Person „LLM-Panel“, jede Gold-Spalte "
                    "und jede Review-Person als eigene Person (für den Intercoder-Vergleich im "
                    "QDA-Programm), Signalwörter im eigenen Zweig „LLM-Evidenz“, je Einheit eine "
                    "Notiz mit Konfidenzen und Begründung."
                )
            )
        )
        qda_layout.addWidget(self.qdpx_button)
        qdc_fold = Collapsible()
        qdc_layout = QVBoxLayout(qdc_fold.body)
        qdc_layout.setContentsMargins(0, 0, 0, 0)
        qdc_layout.addWidget(self.qdc_button)
        qda_layout.addWidget(qdc_fold)
        qda_layout.addStretch()

        ls_box = QGroupBox(self.tr("Label Studio"))
        ls_layout = QVBoxLayout(ls_box)
        ls_layout.addWidget(
            hint_label(self.tr("Je Einheit eine Aufgabe, um im Browser-Werkzeug Label Studio "
                               "von Hand zu kodieren."))
        )  # fmt: skip
        ls_fold = Collapsible()
        ls_fold_layout = QVBoxLayout(ls_fold.body)
        ls_fold_layout.setContentsMargins(0, 0, 0, 0)
        ls_fold_layout.addWidget(
            hint_label(
                self.tr(
                    "Label Studio: je Einheit eine Aufgabe mit Zieltext und Kontext, dazu die "
                    "passende Oberfläche (.xml, unter Settings → Labeling Interface einfügen). "
                    "LLM-Vorschläge lenken das Urteil wie eine assistierte Review; für blinde "
                    "Kodierung ohne sie exportieren."
                )
            )
        )
        ls_fold_layout.addWidget(self.ls_predictions)
        ls_fold_layout.addWidget(self.ls_button)
        ls_layout.addWidget(ls_fold)
        ls_layout.addStretch()

        publish_box = QGroupBox(self.tr("Veröffentlichen"))
        publish_layout = QVBoxLayout(publish_box)
        publish_layout.addWidget(
            hint_label(
                self.tr(
                    "Das Replikationspaket enthält Konfiguration, Codebuch, alle Aufrufe, "
                    "Ergebnisse, Protokoll, Software-Versionen und, falls berechnet, die "
                    "Kennwerte; mit „llmanno-rerun“ lässt sich die Kodierung neu rechnen. Ohne "
                    "Rohtexte nur IDs, Codes und Kennzahlen, Sprecher und Review-Personen als "
                    "Pseudonyme; für einen Rerun braucht es dann die Originaldaten. Einheiten- und "
                    "Gruppen-IDs bleiben unverändert, außer mit dem Schalter zum Pseudonymisieren."
                )
            )
        )
        publish_layout.addWidget(self.include_texts)
        publish_layout.addWidget(self.pseudonymize_ids)
        publish_layout.addWidget(self.replication_button)
        publish_layout.addStretch()

        grid = QGridLayout()
        grid.addWidget(evaluate_box, 0, 0)
        grid.addWidget(qda_box, 0, 1)
        grid.addWidget(ls_box, 1, 0)
        grid.addWidget(publish_box, 1, 1)
        saved_row = QHBoxLayout()
        saved_row.addWidget(self.status, 1)
        saved_row.addWidget(self.open_folder)
        layout = QVBoxLayout(self)
        layout.addWidget(
            hint_label(
                self.tr(
                    "Weitere Exporte: Statistik-Daten (SPSS, Stata, R) und Kennwerte als XLSX im "
                    "Tab Evaluation · Präregistrierung im Tab Splits & Freeze · Sprache von "
                    "Report, Präregistrierung und Paket im Menü Sprache."
                )
            )
        )
        layout.addWidget(self.dev_only)
        layout.addWidget(self.all_systems)
        layout.addLayout(grid)
        layout.addLayout(saved_row)
        layout.addWidget(self.error)
        layout.addStretch()

    def refresh(self) -> None:
        if self.project is None:
            return
        cfg = self.project.config
        if self.busy:
            return
        calls = (
            None
            if self.project.store.count_calls()
            else self.tr("Noch keine Ergebnisse: erst im Tab Lauf kodieren.")
        )
        ready = (
            None
            if cfg.data is not None and cfg.codebook is not None
            else self.tr("Erst Daten (Tab Daten) und Codebuch (Tab Codebuch) festlegen.")
        )
        codebook = None if cfg.codebook is not None else self.tr("Erst das Codebuch festlegen.")
        for button, reason in (
            (self.xlsx_button, calls),
            (self.qdpx_button, ready),
            (self.qdc_button, codebook),
            (self.ls_button, ready),
            (self.replication_button, ready),
            (self.report_button, ready),
        ):
            button.setEnabled(reason is None)
            button.setToolTip(reason or self._tips[button])
        self.all_systems.setVisible(bool(self.other_systems()))
        exposed = any(exposed_test_items(p) for p in self._chosen_systems().values())
        self.dev_only.setEnabled(exposed)
        self.dev_only.setToolTip(
            self._dev_tip
            if exposed
            else self.tr("Derzeit ohne Wirkung: Kein Export enthielte kodierte Einheiten des "
                         "Prüfteils (Test) vor dem Einfrieren.")
        )  # fmt: skip

    def _chosen_systems(self) -> dict[str, Project]:
        """Was in XLSX und REFI-QDA geht: das gewählte Codesystem, auf Wunsch alle."""
        assert self.project is not None
        chosen = {self.project.config.name: self.project}
        if self.all_systems.isChecked():
            chosen |= self.other_systems()
        return chosen

    def _ask(
        self,
        suffix: str,
        filter_: str,
        target: Callable[[Path], None],
        exposes: bool = False,
        partial: bool = True,
        scope: dict[str, Project] | None = None,
    ) -> None:
        """`exposes`: Der Export enthält Ergebnisse; `partial`: er kann nur Dev enthalten;
        `scope`: exportierte Codesysteme (sonst das gewählte)."""
        assert self.project is not None
        if exposes and not (partial and self.only_dev) and not self._confirm_exposure(scope):
            return
        folder = self.project.folder / "exports"
        folder.mkdir(exist_ok=True)
        default = folder / f"{self.project.config.name}{suffix}"
        path, selected = QFileDialog.getSaveFileName(
            self, self.tr("Speichern"), str(default), filter_
        )
        if path:
            target(chosen_path(path, selected))

    @property
    def only_dev(self) -> bool:
        return self.dev_only.isEnabled() and self.dev_only.isChecked()

    def _confirm_exposure(self, scope: dict[str, Project] | None = None) -> bool:
        assert self.project is not None
        scope = scope or {self.project.config.name: self.project}
        counts = {name: len(exposed_test_items(p)) for name, p in scope.items()}
        counts = {name: n for name, n in counts.items() if n}
        if not counts:
            return True
        exposed = range(sum(counts.values()))
        answer = QMessageBox.question(
            self,
            self.tr("Test-Ergebnisse vor dem Freeze exportieren?"),
            self.tr(
                "Der Export enthält LLM-Ergebnisse (XLSX und REFI-QDA auch Gold) für {0} "
                "Test-Einheiten. Wer sie vor "
                "dem Freeze ansieht, kann das Instrument am Test-Set ausrichten; der Export wird "
                "im Methoden-Report als Test-Einsicht vermerkt."
            ).format(len(exposed))
            + (
                "\n\n" + "; ".join(f"{name}: {n}" for name, n in counts.items())
                if len(scope) > 1
                else ""
            ),
        )
        return answer == QMessageBox.StandardButton.Yes

    @property
    def busy(self) -> bool:
        return self._task is not None and self._task.running

    def _run(self, path: Path, export: Callable[[Project], object], note: str = "") -> None:
        """Exporte großer Projekte dauern; sie laufen im Hintergrund. `note` steht nach dem
        Speichern hinter dem Pfad."""
        assert self.project is not None
        if self.busy:
            return
        self.error.clear()
        self.open_folder.hide()
        self.status.setText(self.tr("Exportiere {0} …").format(path.name))
        buttons = (
            self.xlsx_button,
            self.qdpx_button,
            self.qdc_button,
            self.ls_button,
            self.replication_button,
            self.report_button,
        )
        for button in buttons:
            button.setEnabled(False)
        self._task = project_task(self, self.project, export)
        self._task.done.connect(lambda _: self._done(path, note))
        self._task.failed.connect(lambda exc: self.error.setText(error_text(exc)))
        self._task.failed.connect(lambda exc: self.status.clear())
        self._task.finished.connect(self.refresh)
        self._task.start()

    def _done(self, path: Path, note: str) -> None:
        self.status.setText(self.tr("Gespeichert: {0}").format(path) + note)
        self.exported.emit()
        self._saved = path
        self.open_folder.show()

    def _open_folder(self) -> None:
        if self._saved is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._saved.parent)))

    def export_to(self, path: Path) -> None:
        assert self.project is not None
        only_dev, others = self.only_dev, self._others()
        self._run(
            path,
            lambda own: _with_others(
                others, lambda opened: export_xlsx(own, path, only_dev=only_dev, others=opened)
            ),
        )

    def export_qdpx_to(self, path: Path) -> None:
        assert self.project is not None
        only_dev, others = self.only_dev, self._others()
        # Codesysteme ohne aktive Codes haben noch keinen Zweig; sie blockieren nicht.
        skipped = [n for n, (_, cfg) in others.items() if not _codable(cfg)]
        others = {n: o for n, o in others.items() if n not in skipped}
        note = (
            " · " + self.tr("ohne {0} (noch keine aktiven Codes)").format(", ".join(skipped))
            if skipped
            else ""
        )
        self._run(
            path,
            lambda own: _with_others(
                others, lambda opened: export_qdpx(own, path, only_dev=only_dev, others=opened)
            ),
            note,
        )

    def _others(self) -> dict[str, tuple[Path, ProjectConfig]]:
        """Die übrigen Codesysteme für den Worker, falls alle exportiert werden."""
        if not self.all_systems.isChecked():
            return {}
        return {name: (p.folder, p.config) for name, p in self.other_systems().items()}

    def export_replication_to(self, path: Path) -> None:
        assert self.project is not None
        texts, report = self.include_texts.isChecked(), self.current_report()
        ids = not texts and self.pseudonymize_ids.isChecked()
        lang = output_language_setting()
        self._run(
            path,
            lambda own: export_replication(
                own, path, include_texts=texts, pseudonymize_ids=ids, report=report, lang=lang
            ),
        )

    def export_report_to(self, path: Path) -> None:
        assert self.project is not None
        report, lang = self.current_report(), output_language_setting()
        self._run(path, lambda own: export_report(own, report, path, lang))

    def export_labelstudio_to(self, path: Path) -> None:
        assert self.project is not None
        predictions, only_dev = self.ls_predictions.isChecked(), self.only_dev
        self._run(
            path,
            lambda own: export_labelstudio(own, path, predictions=predictions, only_dev=only_dev),
        )

    def export_qdc_to(self, path: Path) -> None:
        assert self.project is not None
        self._run(path, lambda own: export_qdc(own, path))


def _codable(cfg: ProjectConfig) -> bool:
    return cfg.data is not None and cfg.codebook is not None and bool(cfg.codebook.active_codes())


def _with_others(
    others: dict[str, tuple[Path, ProjectConfig]], export: Callable[[dict[str, Project]], object]
) -> None:
    """Im Worker: die übrigen Codesysteme mit eigener Verbindung öffnen (SQLite je Thread)."""
    opened: dict[str, Project] = {}
    try:
        for name, (folder, config) in others.items():
            opened[name] = Project.open(folder)
            opened[name].config = config
        export(opened)
    finally:
        for other in opened.values():
            other.close()
