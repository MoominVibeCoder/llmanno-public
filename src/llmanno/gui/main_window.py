"""Hauptfenster: Tabs in der Reihenfolge des Arbeitsablaufs (docs/PLAN.md, Abschnitt 9)."""

from __future__ import annotations

import platform
import sqlite3
from collections.abc import Callable
from datetime import datetime
from importlib.metadata import version
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths, QTimer, qVersion
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from llmanno.core import codesystems
from llmanno.core.demo import create_demo, is_demo
from llmanno.core.lock import ProjectLocked
from llmanno.core.project import Project
from llmanno.core.prompt import TEMPLATE_VERSION
from llmanno.core.steps import next_step, workflow
from llmanno.core.transfer import transfer_instrument
from llmanno.gui.audit_dialog import AuditDialog
from llmanno.gui.codebook_page import CodebookPage
from llmanno.gui.codesystem_dialog import CodesystemDialog
from llmanno.gui.common import (
    OUTPUT_LANGUAGE_KEY,
    Page,
    Task,
    advanced_setting,
    output_language_setting,
    set_advanced,
)
from llmanno.gui.data_page import DataPage
from llmanno.gui.evaluation_page import EvaluationPage
from llmanno.gui.export_page import ExportPage
from llmanno.gui.help import GLOSSARY, help_window, show_help
from llmanno.gui.models_page import ModelsPage
from llmanno.gui.project_page import RECENT_KEY, ProjectPage, recent_projects
from llmanno.gui.prompt_page import PromptPage
from llmanno.gui.results_page import ResultsPage
from llmanno.gui.review_page import ReviewPage
from llmanno.gui.run_page import RunPage
from llmanno.gui.setup_wizard import run_setup_wizard
from llmanno.gui.split_page import SplitPage, freeze_status
from llmanno.gui.steps_bar import StepsBar

MAX_RECENT = 8
STEPS_KEY = "ui/steps"  # Leiste „Nächster Schritt“ zeigen (Menü Ansicht)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.project: Project | None = None
        # Weitere Codesysteme (core.codesystems) und das gewählte; ohne Wahl das Hauptprojekt.
        self.codesystems: dict[str, Project] = {}
        self.active: Project | None = None
        self.resize(1280, 820)

        self.project_page = ProjectPage()
        self.project_page.new_requested.connect(self.new_project)
        self.project_page.open_requested.connect(self.open_project)
        self.project_page.demo_requested.connect(self.open_demo)
        self.project_page.recent_requested.connect(self.open_recent)
        self._demo_task: Task | None = None
        self.project_page.save_requested.connect(self.save_project)
        self.project_page.changed.connect(self._update_title)

        self.data_page = DataPage()
        self.codebook_page = CodebookPage()
        self.models_page = ModelsPage()
        self.prompt_page = PromptPage()
        self.split_page = SplitPage()
        self.run_page = RunPage()
        self.results_page = ResultsPage()
        self.review_page = ReviewPage()
        self.evaluation_page = EvaluationPage()
        self.export_page = ExportPage()
        self.pages: list[Page] = [
            self.data_page, self.codebook_page, self.models_page, self.prompt_page,
            self.split_page, self.run_page, self.results_page, self.review_page,
            self.evaluation_page, self.export_page,
        ]  # fmt: skip
        # Diese Tabs zeigen das gewählte Codesystem; die übrigen gelten für alle.
        self.system_pages: list[Page] = [
            self.codebook_page, self.prompt_page, self.split_page, self.results_page,
            self.review_page, self.evaluation_page, self.export_page,
        ]  # fmt: skip
        self.export_page.other_systems = self._other_systems
        self.run_page.codesystems = lambda: list(self.codesystems.values())
        self.run_page.prepare_codesystems = self._prepare_codesystems
        self.data_page.other_calls = lambda: any(
            p.store.count_calls() for p in self.codesystems.values()
        )
        self.prompt_page.frozen_systems = lambda: [
            name for name, p in self.codesystems.items() if p.config.freeze is not None
        ]
        for page in self.pages:
            page.changed.connect(self._update_title)
        self.run_page.running_changed.connect(self._lock_for_run)
        self.run_page.results_changed.connect(self.results_page.refresh)
        self.run_page.results_changed.connect(self.export_page.refresh)
        self.export_page.current_report = lambda: self.evaluation_page.report

        self.tabs = QTabWidget()
        self.tabs.addTab(self.project_page, self.tr("Projekt"))
        self.tabs.addTab(self.data_page, self.tr("Daten"))
        self.tabs.addTab(self.codebook_page, self.tr("Codebuch"))
        self.tabs.addTab(self.models_page, self.tr("Modelle"))
        self.tabs.addTab(self.prompt_page, self.tr("Prompt && Einheiten"))
        self.tabs.addTab(self.split_page, self.tr("Splits && Freeze"))
        self.tabs.addTab(self.run_page, self.tr("Lauf"))
        self.tabs.addTab(self.results_page, self.tr("Ergebnisse"))
        self.tabs.addTab(self.review_page, self.tr("Review"))
        self.tabs.addTab(self.evaluation_page, self.tr("Evaluation"))
        self.tabs.addTab(self.export_page, self.tr("Export"))
        tips = {
            self.project_page: self.tr("Name, Sprache der Anweisungen, ein oder mehrere Codes je "
                                       "Einheit"),
            self.data_page: self.tr("Tabelle mit den Texten importieren, Spalten zuordnen, "
                                    "menschliche Kodierungen (Gold) wählen"),
            self.codebook_page: self.tr("Codebuch laden und festlegen, welche Codes das Modell "
                                        "vergeben darf"),
            self.models_page: self.tr("Anbieter einrichten und die Modelle wählen, die kodieren "
                                      "(Panel)"),
            self.prompt_page: self.tr("Was das Modell auf einmal kodiert, wie oft, und die "
                                      "Vorschau der Anweisungen"),
            self.split_page: self.tr("Daten in Übungsteil (Dev) und Prüfteil (Test) aufteilen, "
                                     "das Instrument einfrieren (Freeze), Präregistrierung"),
            self.run_page: self.tr("Kodieren lassen: erst Pilot, dann alle Einheiten"),
            self.results_page: self.tr("Codes, Konsistenz und Belege je Einheit"),
            self.review_page: self.tr("Unsichere Fälle und eine Zufallsstichprobe selbst "
                                      "kodieren"),
            self.evaluation_page: self.tr("Wie gut kodiert das LLM? Kennwerte gegen Gold und "
                                          "Reviews, Methoden-Report"),
            self.export_page: self.tr("Für Excel, QDA-Programme, Label Studio, Report und "
                                      "Replikationspaket"),
        }  # fmt: skip
        for page, tip in tips.items():
            self.tabs.setTabToolTip(self.tabs.indexOf(page), tip)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.system_combo = QComboBox()
        self.system_combo.setToolTip(
            self.tr("Codebuch, Prompt, Split, Ergebnisse, Review und Evaluation zeigen dieses "
                    "Codesystem. Der Lauf kodiert alle.")
        )  # fmt: skip
        self.system_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.system_combo.currentIndexChanged.connect(self._system_chosen)
        self.add_system_button = QPushButton(self.tr("+ Codesystem…"))
        self.add_system_button.clicked.connect(self.add_codesystem)
        self.gold_system_button = QPushButton(self.tr("Codesystem…"))
        self.gold_system_button.setToolTip(
            self.tr("Gold-Spalten, Modus und k des gewählten weiteren Codesystems ändern")
        )
        self.gold_system_button.clicked.connect(self.edit_codesystem_gold)
        corner = QWidget()
        corner_layout = QHBoxLayout(corner)
        corner_layout.setContentsMargins(0, 0, 4, 0)
        corner_layout.addWidget(QLabel(self.tr("Codesystem:")))
        corner_layout.addWidget(self.system_combo)
        corner_layout.addWidget(self.gold_system_button)
        corner_layout.addWidget(self.add_system_button)
        self.tabs.setCornerWidget(corner)
        self.steps_bar = StepsBar()
        self.steps_bar.go_to.connect(self.go_to_step)
        self._steps_timer = QTimer(self)  # Änderungen bündeln: workflow liest den Store
        self._steps_timer.setSingleShot(True)
        self._steps_timer.setInterval(300)
        self._steps_timer.timeout.connect(self._update_steps)
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self.steps_bar)
        central_layout.addWidget(self.tabs)
        self.setCentralWidget(central)
        self.run_page.results_changed.connect(self._steps_timer.start)
        for page in (*self.pages, self.project_page):
            page.changed.connect(self._steps_timer.start)
        self.export_page.exported.connect(self._steps_timer.start)
        self.freeze_label = QLabel()
        self.statusBar().addPermanentWidget(self.freeze_label)

        self._build_menu()
        self.set_project(None)

    def _build_menu(self) -> None:
        menu = self.menuBar().addMenu(self.tr("&Datei"))
        self.new_action = self._action(
            menu, self.tr("&Neues Projekt…"), QKeySequence.StandardKey.New, self.new_project
        )
        self.new_action.setStatusTip(
            self.tr("Ein Assistent fragt Ordner, Daten, Codebuch und Modell ab.")
        )
        self.empty_action = QAction(self.tr("Neues &leeres Projekt…"), self)
        self.empty_action.setStatusTip(
            self.tr("Nur einen leeren Ordner wählen; alles Weitere in den Tabs.")
        )
        self.empty_action.triggered.connect(self.new_empty_project)
        menu.addAction(self.empty_action)
        self.open_action = self._action(
            menu, self.tr("Projekt ö&ffnen…"), QKeySequence.StandardKey.Open, self.open_project
        )
        self.demo_action = QAction(self.tr("&Demo öffnen"), self)
        self.demo_action.triggered.connect(self.open_demo)
        menu.addAction(self.demo_action)
        self.recent_menu = menu.addMenu(self.tr("&Zuletzt geöffnet"))
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        self.save_action = self._action(
            menu, self.tr("&Speichern"), QKeySequence.StandardKey.Save, self.save_project
        )
        self.transfer_action = QAction(self.tr("Neues Projekt aus diesem &Instrument…"), self)
        self.transfer_action.triggered.connect(self.transfer_project)
        menu.addAction(self.transfer_action)
        self.audit_action = QAction(self.tr("&Protokoll anzeigen…"), self)
        self.audit_action.triggered.connect(self.show_audit)
        menu.addAction(self.audit_action)
        menu.addSeparator()
        self._action(menu, self.tr("&Beenden"), QKeySequence.StandardKey.Quit, self.close)

        languages = self.menuBar().addMenu(self.tr("&Sprache"))
        languages.addSection(self.tr("Oberfläche (ab dem nächsten Start)"))
        group = QActionGroup(self)
        current = str(QSettings().value("ui/language", "de"))
        self.language_actions: dict[str, QAction] = {}
        for code, name in (("de", "Deutsch"), ("en", "English")):
            action = QAction(name, self, checkable=True, checked=code == current)
            action.triggered.connect(lambda _=False, c=code: self.set_language(c))
            group.addAction(action)
            languages.addAction(action)
            self.language_actions[code] = action
        languages.addSection(self.tr("Report, Präregistrierung, Replikationspaket"))
        outputs = QActionGroup(self)
        chosen = output_language_setting()
        self.output_actions: dict[str, QAction] = {}
        for code, name in (("de", "Deutsch"), ("en", "English")):
            action = QAction(name, self, checkable=True, checked=code == chosen)
            action.triggered.connect(
                lambda _=False, c=code: QSettings().setValue(OUTPUT_LANGUAGE_KEY, c)
            )
            outputs.addAction(action)
            languages.addAction(action)
            self.output_actions[code] = action

        view = self.menuBar().addMenu(self.tr("&Ansicht"))
        self.advanced_action = QAction(
            self.tr("&Erweiterte Einstellungen zeigen"), self, checkable=True
        )
        self.advanced_action.setChecked(advanced_setting())
        self.advanced_action.setStatusTip(
            self.tr("Öffnet alle Bereiche „Erweitert“; ihre Vorgaben passen für den Einstieg.")
        )
        self.advanced_action.toggled.connect(set_advanced)
        view.addAction(self.advanced_action)
        self.steps_action = QAction(self.tr("&Nächsten Schritt zeigen"), self, checkable=True)
        self.steps_action.setChecked(QSettings().value(STEPS_KEY, True) in (True, "true", "1"))
        self.steps_action.toggled.connect(self._show_steps)
        view.addAction(self.steps_action)

        help_menu = self.menuBar().addMenu(self.tr("&Hilfe"))
        self._action(
            help_menu, self.tr("&Anleitung zu diesem Tab"), QKeySequence.StandardKey.HelpContents,
            self.show_tab_help,
        )  # fmt: skip
        contents = QAction(self.tr("Anleitung: &Inhalt"), self)
        contents.triggered.connect(lambda: show_help(self, ("Arbeitsablauf",)))
        help_menu.addAction(contents)
        glossary = QAction(self.tr("&Glossar"), self)
        glossary.triggered.connect(lambda: help_window(self).show_topic(GLOSSARY))
        help_menu.addAction(glossary)
        help_menu.addSeparator()
        about = QAction(self.tr("Ü&ber llmanno…"), self)
        about.triggered.connect(
            lambda: QMessageBox.about(self, self.tr("Über llmanno"), self.about_text())
        )
        help_menu.addAction(about)

    def show_tab_help(self) -> None:
        page = self.tabs.currentWidget()
        show_help(self, getattr(page, "help_topics", ()))

    def about_text(self) -> str:
        return self.tr(
            "llmanno {0}\nPrompt-Template Version {1}\nPython {2} · Qt {3}\n"
            "Lizenz: GNU AGPL, Version 3 oder später\n\n"
            "Zum Zitieren im Methodenteil: llmanno, Version {0} (Prompt-Template {1}). Der "
            "Methoden-Report nennt Version und alle Parameter automatisch."
        ).format(version("llmanno"), TEMPLATE_VERSION, platform.python_version(), qVersion())

    def _fill_recent(self) -> None:
        self.recent_menu.clear()
        folders = [f for f in recent_projects() if Path(f).is_dir()]
        for folder in folders:
            action = self.recent_menu.addAction(f"{Path(folder).name}  ({folder})")
            action.triggered.connect(lambda _=False, f=folder: self.open_recent(f))
        if not folders:
            self.recent_menu.addAction(self.tr("(noch keine)")).setEnabled(False)

    def open_recent(self, folder: str) -> None:
        if not self.run_page.running and self._confirm_discard():
            self._load(lambda: Project.open(Path(folder)))

    def demo_folder(self) -> Path:
        """Ordner der Demo im Benutzerprofil (Tests setzen einen eigenen)."""
        location = QStandardPaths.StandardLocation.AppLocalDataLocation
        return Path(QStandardPaths.writableLocation(location)) / "Demo"

    def open_demo(self) -> None:
        """Beispielprojekt mit simulierten Antworten; eine vorhandene Demo wird geöffnet
        oder auf Wunsch neu angelegt (in einem neuen Ordner, gelöscht wird nichts)."""
        if self.run_page.running or self.demo_busy or not self._confirm_discard():
            return
        folder = self.demo_folder()
        if (folder / "project.json").is_file() and not _demo_complete(folder):
            # Abgebrochener Aufbau: daneben neu anlegen, nichts löschen.
            folder = folder.with_name(f"Demo_{datetime.now():%Y-%m-%d_%H-%M-%S}")
        elif (folder / "project.json").is_file():
            choice = self._ask_demo()
            if choice == "open":
                self._load(lambda: Project.open(folder))
                return
            if choice != "new":
                return
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            folder = folder.with_name(f"Demo_{stamp}")
        self.create_demo_in(folder)

    def _ask_demo(self) -> str | None:
        """„open“, „new“ oder None (abgebrochen)."""
        box = QMessageBox(self)
        box.setWindowTitle(self.tr("Demo öffnen"))
        box.setText(
            self.tr("Es gibt schon eine Demo. Die vorhandene öffnen oder eine neue anlegen? Eine "
                    "neue kommt in einen eigenen Ordner; gelöscht wird nichts.")
        )  # fmt: skip
        open_button = box.addButton(self.tr("Öffnen"), QMessageBox.ButtonRole.AcceptRole)
        new_button = box.addButton(self.tr("Neu anlegen"), QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(open_button)
        box.exec()
        return {open_button: "open", new_button: "new"}.get(box.clickedButton())

    @property
    def demo_busy(self) -> bool:
        return self._demo_task is not None and self._demo_task.running

    def create_demo_in(self, folder: Path) -> None:
        self.set_project(None)
        self.statusBar().showMessage(self.tr("Demo wird angelegt …"))
        # Kein anderes Projekt öffnen, solange die Demo entsteht: Sie würde es danach ersetzen.
        for action in (self.demo_action, self.new_action, self.empty_action, self.open_action,
                       self.transfer_action):  # fmt: skip
            action.setEnabled(False)
        self.recent_menu.setEnabled(False)
        self.project_page.setEnabled(False)

        def build(report: object) -> None:  # eigener Thread, eigene Store-Verbindung
            folder.parent.mkdir(parents=True, exist_ok=True)
            create_demo(folder).close()

        self._demo_task = Task(self, build)
        self._demo_task.done.connect(lambda _: self._demo_ready(folder))
        self._demo_task.failed.connect(
            lambda exc: QMessageBox.critical(self, self.tr("Demo öffnen"), str(exc))
        )
        self._demo_task.finished.connect(self._demo_finished)
        self._demo_task.start()

    def _demo_finished(self) -> None:
        for action in (self.demo_action, self.new_action, self.empty_action, self.open_action):
            action.setEnabled(True)
        self.recent_menu.setEnabled(True)
        self.project_page.setEnabled(True)

    def _demo_ready(self, folder: Path) -> None:
        self.statusBar().showMessage(
            self.tr("Demo angelegt in {0}. Die Antworten stammen von einem Simulator, nicht "
                    "von einem LLM.").format(folder),
            15000,
        )  # fmt: skip
        self._load(lambda: Project.open(folder))
        self.tabs.setCurrentWidget(self.results_page)

    def show_audit(self) -> None:
        if self.project is not None:
            AuditDialog(self.project, self).exec()

    def set_language(self, code: str) -> None:
        """Gilt ab dem nächsten Start: Qt übersetzt Texte beim Aufbau der Oberfläche."""
        QSettings().setValue("ui/language", code)
        self.statusBar().showMessage(
            self.tr("Sprache gespeichert; sie gilt ab dem nächsten Start."), 10000
        )

    def _action(
        self, menu, text: str, shortcut: QKeySequence.StandardKey, slot: Callable[[], object]
    ) -> QAction:
        action = QAction(text, self)
        action.setShortcut(shortcut)
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    # --- Projekt -------------------------------------------------------------------------------

    def new_project(self) -> None:
        """Mit Assistent; „Neues leeres Projekt…“ wählt nur den Ordner."""
        if self.run_page.running or not self._confirm_discard():
            return
        project = run_setup_wizard(self)
        if project is not None:
            self.set_project(project)
            if (step := next_step(workflow(project))) is not None:
                self.go_to_step(step.key)

    def new_empty_project(self) -> None:
        if self.run_page.running or self.demo_busy or not self._confirm_discard():
            return
        folder = QFileDialog.getExistingDirectory(
            self, self.tr("Leeren Ordner für das neue Projekt wählen")
        )
        if folder:
            path = Path(folder)
            self._load(lambda: Project.create(path, name=path.name))

    def open_project(self) -> None:
        if not self._confirm_discard():
            return
        folder = QFileDialog.getExistingDirectory(self, self.tr("Projektordner öffnen"))
        if folder:
            self._load(lambda: Project.open(Path(folder)))

    def transfer_project(self) -> None:
        """Dasselbe Instrument auf einer neuen Erhebungswelle, ohne Split und Freeze."""
        if self.project is None or not self._confirm_discard():
            return
        if self.codesystems:
            QMessageBox.information(
                self,
                self.tr("Weitere Codesysteme"),
                self.tr("Übernommen wird das Instrument des Hauptprojekts. Weitere "
                        "Codesysteme im neuen Projekt mit „+ Codesystem…“ anlegen."),
            )  # fmt: skip
        folder = QFileDialog.getExistingDirectory(
            self, self.tr("Leeren Ordner für das neue Projekt wählen")
        )
        if not folder:
            return
        data, _ = QFileDialog.getOpenFileName(
            self, self.tr("Neue Daten wählen"), "", self.tr("Daten (*.xlsx *.xlsm *.csv *.tsv)")
        )
        if data:
            # Vom gespeicherten Stand: Wer eben „Verwerfen“ gewählt hat, will die
            # ungespeicherten Änderungen nicht im neuen Projekt.
            source = self.project.folder
            self._load(lambda: _transfer_from_disk(source, Path(folder), Path(data)))

    def save_project(self) -> None:
        if self.project is None:
            return
        self.project.save()
        for system in self.codesystems.values():
            system.save()
        self.project_page.refresh()
        self._update_title()

    def set_project(self, project: Project | None) -> None:
        for system in self.codesystems.values():
            system.close()
        self.codesystems = {}
        if self.project is not None:
            self.project.close()
        self.project = self.active = project
        if project is not None:
            self.codesystems, notes = codesystems.open_all(project)
            if notes:
                QMessageBox.warning(self, self.tr("Weitere Codesysteme"), "\n\n".join(notes))
        self._fill_systems()
        self.project_page.set_project(project)
        # Nur der sichtbare Tab lädt sofort; die übrigen beim Wechsel (_tab_changed), sonst
        # rechnet das Öffnen großer Projekte alle Tabs auf einmal.
        current = self.tabs.currentWidget()
        for page in self.pages:
            page.set_project(project, refresh=project is None or page is current)
        for index in range(1, self.tabs.count()):
            self.tabs.setTabEnabled(index, project is not None)
        self.save_action.setEnabled(project is not None)
        self.transfer_action.setEnabled(project is not None)
        self.audit_action.setEnabled(project is not None)
        if project is not None:
            folder = str(project.folder.resolve())
            recent = [folder] + [f for f in recent_projects() if f != folder]
            QSettings().setValue(RECENT_KEY, recent[:MAX_RECENT])
        self._update_title()
        self._update_steps()

    def _show_steps(self, on: bool) -> None:
        QSettings().setValue(STEPS_KEY, on)
        self._update_steps()

    def _update_steps(self) -> None:
        project = self.active or self.project
        visible = project is not None and self.steps_action.isChecked()
        self.steps_bar.setVisible(visible)
        if visible:
            try:
                self.steps_bar.show_steps(workflow(project))
            except (OSError, ValueError, sqlite3.Error):
                self.steps_bar.hide()  # die Leiste hilft nur; ein Fehler zeigt sich im Tab

    def go_to_step(self, key: str) -> None:
        pages = {
            "data": self.data_page, "codebook": self.codebook_page, "models": self.models_page,
            "split": self.split_page, "freeze": self.split_page, "pilot": self.run_page,
            "dev": self.run_page, "full": self.run_page, "test": self.evaluation_page,
            "review": self.review_page, "export": self.export_page,
        }  # fmt: skip
        index = self.tabs.indexOf(pages[key])
        if self.tabs.isTabEnabled(index):
            self.tabs.setCurrentIndex(index)
        if key == "dev" and self.run_page.dev_only.isEnabled():
            self.run_page.dev_only.setChecked(True)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.evaluation_page.cancel()  # Kennwerte vor dem Schließen nicht zu Ende rechnen
        busy = (self.split_page, self.evaluation_page, self.models_page, self.export_page)
        if any(page.busy for page in busy) or self.demo_busy:
            # Ein laufender QThread darf nicht mit dem Fenster zerstört werden.
            self.statusBar().showMessage(
                self.tr("Eine Hintergrundaufgabe läuft noch; bitte kurz warten."), 10000
            )
            event.ignore()
            return
        if self.run_page.running:
            answer = QMessageBox.question(
                self,
                self.tr("Lauf aktiv"),
                self.tr(
                    "Den laufenden Kodierlauf anhalten und beenden? Bisherige Ergebnisse bleiben "
                    "erhalten."
                ),
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.run_page.stop_and_wait()
        if not self._confirm_discard():
            event.ignore()
            return
        self.set_project(None)
        event.accept()

    def _load(self, factory: Callable[[], Project]) -> None:
        try:
            project = factory()
        except ProjectLocked as exc:
            answer = QMessageBox.question(
                self,
                self.tr("Projekt ist gesperrt"),
                str(exc) + "\n\n" + self.tr("Sperre aufheben und trotzdem öffnen?"),
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            folder = exc.folder
            self._load(lambda: Project.open(folder, force=True))
            return
        except (OSError, ValueError, sqlite3.Error) as exc:
            QMessageBox.critical(self, self.tr("Projekt konnte nicht geladen werden"), str(exc))
            return
        self.set_project(project)

    def _confirm_discard(self) -> bool:
        if self.project is None or not self._dirty():
            return True
        buttons = QMessageBox.StandardButton
        answer = QMessageBox.question(
            self,
            self.tr("Ungespeicherte Änderungen"),
            self.tr("Änderungen am Projekt speichern?"),
            buttons.Save | buttons.Discard | buttons.Cancel,
        )
        if answer == buttons.Save:
            self.save_project()
        return answer in (buttons.Save, buttons.Discard)

    # --- Zustand -------------------------------------------------------------------------------

    def _tab_changed(self, index: int) -> None:
        widget = self.tabs.widget(index)
        if isinstance(widget, Page) and self.project is not None:
            widget.refresh()
        self._steps_timer.start()

    def _lock_for_run(self, running: bool) -> None:
        """Während eines Laufs bleiben nur Lauf und Ergebnisse bedienbar: der Lauf arbeitet mit
        der Konfiguration, die bei seinem Start gespeichert war."""
        open_tabs = {self.tabs.indexOf(self.run_page), self.tabs.indexOf(self.results_page)}
        for index in range(self.tabs.count()):
            if index not in open_tabs:
                self.tabs.setTabEnabled(index, not running and self.project is not None)
        for action in (self.new_action, self.empty_action, self.open_action, self.save_action,
                       self.transfer_action):  # fmt: skip
            action.setEnabled(not running)
        self.system_combo.setEnabled(not running and self.project is not None)
        self.gold_system_button.setEnabled(not running and self.active is not self.project)
        self._update_add_system(running)
        self.recent_menu.setEnabled(not running)

    def _update_title(self) -> None:
        app_name = self.tr("LLM-Annotation")
        if self.project is None:
            self.setWindowTitle(app_name)
            self.setWindowModified(False)
            self.freeze_label.clear()
            return
        # Nach dem Freeze bricht jede Änderung in einem anderen Tab das Instrument; das soll
        # sofort sichtbar sein, nicht erst beim nächsten Laufstart.
        text, warn = freeze_status(self.active or self.project)
        if self.codesystems and self.active is not None:
            text = f"{self.active.config.name}: {text}" if text else text
        frozen = [n for n, p in self.codesystems.items() if p.config.freeze is not None]
        if frozen and self.active is self.project:
            # Daten, Modelle, Einheiten und Namensliste gelten auch dort.
            text = " · ".join(
                filter(None, [text, self.tr("auch eingefroren: {0}").format(", ".join(frozen))])
            )
        self.freeze_label.setText(text)
        self.freeze_label.setStyleSheet("color: #b3261e; font-weight: bold" if warn else "")
        self.setWindowTitle(f"{self.project.config.name}[*] – {app_name}")
        self.setWindowModified(self._dirty())
        self._update_add_system(self.run_page.running)

    def _dirty(self) -> bool:
        projects = [self.project, *self.codesystems.values()]
        return any(p is not None and p.dirty for p in projects)

    # --- Codesysteme ---------------------------------------------------------------------------

    def _fill_systems(self) -> None:
        self.system_combo.blockSignals(True)
        self.system_combo.clear()
        if self.project is not None:
            self.system_combo.addItem(self.tr("Hauptprojekt"), None)
            for name in self.codesystems:
                self.system_combo.addItem(name, name)
            active = self.active.config.name if self.active is not self.project else None
            self.system_combo.setCurrentIndex(max(self.system_combo.findData(active), 0))
        self.system_combo.blockSignals(False)
        self.system_combo.setEnabled(self.project is not None)
        self._update_add_system(False)
        self.gold_system_button.setVisible(bool(self.codesystems))
        self.gold_system_button.setEnabled(self.active is not self.project)

    def _update_add_system(self, running: bool) -> None:
        """Ein weiteres Codesystem kodiert die Einheiten des Hauptprojekts: erst mit Daten."""
        has_data = self.project is not None and self.project.config.data is not None
        self.add_system_button.setEnabled(has_data and not running)
        self.add_system_button.setToolTip(
            self.tr("Weiteres Codebuch auf denselben Daten, mit eigener Auswertung")
            if has_data
            else self.tr("Erst Daten importieren (Tab Daten): Ein weiteres Codesystem kodiert "
                         "dieselben Einheiten.")
        )  # fmt: skip

    def _system_chosen(self, index: int) -> None:
        if self.project is None:
            return
        name = self.system_combo.itemData(index)
        chosen = self.codesystems.get(name) if name else self.project
        if chosen is None or chosen is self.active:
            return
        if chosen is not self.project:
            # Was im Hauptprojekt geändert wurde (Modelle, Einheiten), gilt auch hier; nur im
            # Speicher, gespeichert wird mit „Speichern“ oder beim Laufstart.
            try:
                notes = codesystems.sync(self.project, chosen, save=False)
            except (OSError, ValueError, sqlite3.Error) as exc:
                QMessageBox.warning(self, self.tr("Codesystem nicht abgeglichen"), str(exc))
                self._fill_systems()  # Auswahl zurück auf das bisherige
                return
            if notes:
                QMessageBox.warning(self, self.tr("Weitere Codesysteme"), "\n\n".join(notes))
        self.active = chosen
        self.gold_system_button.setEnabled(chosen is not self.project)
        current = self.tabs.currentWidget()
        for page in self.system_pages:
            page.set_project(chosen, refresh=page is current)
        self._update_title()
        self._update_steps()

    def add_codesystem(self) -> None:
        if self.project is None:
            return
        dialog = CodesystemDialog(self.project, self)
        if not dialog.exec():
            return
        assert dialog.codebook is not None
        try:
            system = codesystems.create(
                self.project,
                dialog.name.text(),
                dialog.codebook,
                dialog.gold_columns(),
                dialog.consensus_column(),
                mode=dialog.mode.currentData(),
                k=dialog.k.value(),
            )
        except (OSError, ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, self.tr("Codesystem nicht angelegt"), str(exc))
            return
        self.codesystems[system.config.name] = system
        self.codesystems = dict(sorted(self.codesystems.items()))
        self._fill_systems()
        self.system_combo.setCurrentIndex(self.system_combo.findData(system.config.name))
        self.tabs.setCurrentWidget(self.codebook_page)  # Code-Liste prüfen
        self.codebook_page.suggest_if_empty()
        self.statusBar().showMessage(
            self.tr("Codesystem „{0}“ angelegt. Im Tab Codebuch die Code-Liste prüfen.").format(
                system.config.name
            ),
            15000,
        )

    def edit_codesystem_gold(self) -> None:
        main, system = self.project, self.active
        if main is None or system is None or system is main:
            return
        dialog = CodesystemDialog(main, self, system)
        own = system.config.data
        if not dialog.exec() or main.config.data is None or main.data_path is None or not own:
            return
        gold, consensus = dialog.gold_columns(), dialog.consensus_column()
        try:
            if (gold, consensus) != (own.mapping.gold, own.mapping.consensus):
                report = system.update_gold(
                    main.data_path,
                    main.config.data.sheet,
                    gold,
                    consensus,
                    main.config.data.mapping.analysis,
                )
                self.statusBar().showMessage(
                    self.tr("Gold-Spalten übernommen ({0} Einheiten).").format(len(report.units)),
                    10000,
                )
            system.update(mode=dialog.mode.currentData(), k=dialog.k.value())
        except (OSError, ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, self.tr("Codesystem nicht geändert"), str(exc))
            return
        self._update_title()
        current = self.tabs.currentWidget()
        if isinstance(current, Page):
            current.refresh()

    def _other_systems(self) -> dict[str, Project]:
        """Alle Codesysteme außer dem gewählten, das Hauptprojekt unter seinem Namen."""
        if self.project is None:
            return {}
        main = self.project.config.name
        if main in self.codesystems:  # gleicher Name: das Hauptprojekt nicht verdecken
            main = f"{main} ({self.tr('Hauptprojekt')})"
        systems = {main: self.project, **self.codesystems}
        return {name: p for name, p in systems.items() if p is not self.active}

    def _prepare_codesystems(self) -> list[Project]:
        """Vor einem Lauf: alle weiteren Codesysteme abgleichen und speichern."""
        assert self.project is not None
        for system in self.codesystems.values():
            for note in codesystems.sync(self.project, system):
                self.run_page.note(note)
        return list(self.codesystems.values())


def _transfer_from_disk(source: Path, folder: Path, data: Path) -> Project:
    saved = Project.open(source)
    try:
        return transfer_instrument(saved, folder, data)
    finally:
        saved.close()


def _demo_complete(folder: Path) -> bool:
    """Fertig ist eine Demo erst mit dem Protokolleintrag am Ende des Aufbaus."""
    try:
        project = Project.open(folder)
    except (OSError, ValueError, sqlite3.Error, ProjectLocked):
        return False
    try:
        return is_demo(project)
    finally:
        project.close()
