"""Projektsperre und riskante Speicherorte (Roadmap 29)."""

import json
import os
import socket
from pathlib import Path

import pytest

from llmanno.core.lock import LOCK_FILE, ProjectLocked, storage_warning
from llmanno.core.project import Project


def test_second_program_is_locked_out_and_stale_locks_are_taken_over(tmp_path):
    project = Project.create(tmp_path / "p", name="Sperre")
    lock = project.folder / LOCK_FILE
    holder = json.loads(lock.read_text(encoding="utf-8"))
    assert holder["pid"] == os.getpid() and holder["host"] == socket.gethostname()
    again = Project.open(project.folder)  # derselbe Prozess (Lauf-Thread): erlaubt
    again.close()
    assert lock.is_file()
    project.close()
    project.close()  # doppeltes Schließen gibt nichts doppelt frei
    assert not lock.is_file()

    lock.write_text(json.dumps({"host": "anderer-rechner", "pid": 1, "since": "heute"}))
    with pytest.raises(ProjectLocked, match="Rechner anderer-rechner"):
        Project.open(project.folder)
    Project.open(project.folder, force=True).close()

    # Abgestürzt auf diesem Rechner: Der Prozess läuft nicht mehr, die Sperre ist verwaist.
    lock.write_text(json.dumps({"host": socket.gethostname(), "pid": 2**22 + 12345}))
    Project.open(project.folder).close()


def test_main_window_offers_to_lift_a_lock(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from llmanno.gui.main_window import MainWindow

    project = Project.create(tmp_path / "p", name="Sperre")
    folder = project.folder
    project.close()
    (folder / LOCK_FILE).write_text(json.dumps({"host": "anderer-rechner", "pid": 1}))
    window = MainWindow()
    qtbot.addWidget(window)
    answers = [QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes]
    monkeypatch.setattr(QMessageBox, "question", lambda *a: answers.pop(0))
    window._load(lambda: Project.open(folder))
    assert window.project is None
    window._load(lambda: Project.open(folder))
    assert window.project is not None and window.project.folder == folder
    window.set_project(None)


def test_storage_warning():
    assert storage_warning(Path("C:/Users/x/OneDrive - Uni/Projekt")).startswith(
        "Der Projektordner liegt in einem synchronisierten Cloud-Ordner"
    )
    assert storage_warning(Path("C:/Users/x/sciebo/Projekt")) is not None
    assert storage_warning(Path("C:/Users/x/Documents/Projekt")) is None
