"""Fehlerprotokoll für die Exe ohne Konsole (Roadmap 30)."""

import sys


def test_unexpected_errors_are_logged_and_shown(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from llmanno.gui.app import install_excepthook

    shown = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a: shown.append(a))
    monkeypatch.setattr(sys, "__excepthook__", lambda *a: None)
    log = tmp_path / "logs" / "fehler.log"
    previous = install_excepthook(log)
    try:
        try:
            raise RuntimeError("Slot kaputt")
        except RuntimeError:
            sys.excepthook(*sys.exc_info())
    finally:
        sys.excepthook = previous
    text = log.read_text(encoding="utf-8")
    assert "RuntimeError: Slot kaputt" in text and "llmanno" in text
    assert len(shown) == 1 and "Slot kaputt" in shown[0][2] and str(log) in shown[0][2]
