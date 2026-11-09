"""Einstiegspunkt der Desktop-Oberfläche."""

from __future__ import annotations

import sys
import traceback
from collections.abc import Callable
from datetime import datetime
from importlib.metadata import version
from pathlib import Path

from PySide6.QtCore import (
    QCoreApplication,
    QLibraryInfo,
    QSettings,
    QStandardPaths,
    QTranslator,
)
from PySide6.QtWidgets import QApplication, QMessageBox

from llmanno.gui import common
from llmanno.gui.main_window import MainWindow

# Quellsprache der Oberfläche ist Deutsch; weitere Sprachen liegen als llmanno_<sprache>.qm hier.
I18N_DIR = Path(__file__).resolve().parent.parent / "i18n"


def _install_translators(app: QApplication, language: str) -> None:
    # Qt-eigene Texte (Dialogknöpfe wie „Speichern“/„Abbrechen“) in der Oberflächensprache.
    qt = QTranslator(app)
    if qt.load(f"qtbase_{language}", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(qt)
    own = QTranslator(app)
    if language != "de" and own.load(f"llmanno_{language}", str(I18N_DIR)):
        app.installTranslator(own)


def error_log() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    return Path(base) / "fehler.log"


def install_excepthook(log: Path) -> Callable:
    """Ausnahmen in Slots verschwinden in der Exe ohne Konsole sonst spurlos: Traceback in die
    Logdatei, Meldung mit deren Pfad. Liefert den vorigen Hook zurück."""
    previous = sys.excepthook
    showing = []

    def hook(kind, value, tb) -> None:
        text = "".join(traceback.format_exception(kind, value, tb))
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as handle:
                stamp = datetime.now().isoformat(timespec="seconds")
                handle.write(f"--- {stamp} · llmanno {version('llmanno')} ---\n{text}\n")
        except OSError:
            pass
        sys.__excepthook__(kind, value, tb)
        if QApplication.instance() is None or showing:
            return
        showing.append(True)
        try:
            QMessageBox.critical(
                None,
                QCoreApplication.translate("app", "Unerwarteter Fehler"),
                QCoreApplication.translate(
                    "app",
                    "{0}\n\nDetails stehen in {1}. Bitte die Datei bei einer Fehlermeldung "
                    "mitschicken; sie liegt nur auf diesem Rechner und kann Textauszüge enthalten.",
                ).format(value, log),
            )
        finally:
            showing.clear()

    sys.excepthook = hook
    return previous


def main() -> int:
    app = QApplication(sys.argv)
    app.setOrganizationName("llmanno")
    app.setApplicationName("LLM-Annotation")
    language = str(QSettings().value("ui/language", "de"))
    _install_translators(app, language)
    common.UI_LANGUAGE = language
    install_excepthook(error_log())
    window = MainWindow()
    window.show()
    return app.exec()
