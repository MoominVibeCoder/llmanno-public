import os

# GUI-Tests laufen ohne sichtbares Fenster, auch in CI.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from helpers import TEXTS, write_xlsx  # noqa: E402

from llmanno.core.config import Code, ColumnMapping, ModelConfig  # noqa: E402
from llmanno.core.project import Project  # noqa: E402


@pytest.fixture
def ready_project(tmp_path):
    project = Project.create(tmp_path / "p", name="Test")
    data = write_xlsx(
        tmp_path / "d.xlsx", ["ID", "Text"], [[i + 1, t] for i, t in enumerate(TEXTS)]
    )
    project.import_data(data, "Daten", ColumnMapping(id="ID", text=["Text"]))
    codebook = tmp_path / "cb.txt"
    codebook.write_text("### K1 — Frage\n### K2 — Antwort\n", encoding="utf-8")
    project.load_codebook(codebook)
    project.update(
        codebook=project.config.codebook.model_copy(
            update={"codes": [Code(id="K1"), Code(id="K2")]}
        ),
        panel=[ModelConfig(provider="ollama", model="fake")],
    )
    project.save()
    yield project
    project.close()


@pytest.fixture(autouse=True)
def no_blocking_dialogs(monkeypatch):
    """Ein modaler Dialog würde den Offscreen-Test für immer blockieren; lieber laut scheitern."""
    from PySide6.QtWidgets import QMessageBox

    def fail(*args, **kwargs):
        raise AssertionError(f"Unerwarteter Dialog: {args[1:3]}")

    for name in ("question", "critical", "warning", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(fail))


@pytest.fixture(autouse=True, scope="session")
def isolated_settings(tmp_path_factory):
    """QSettings in eine Ini-Datei im Temp-Ordner statt in die Registry des Rechners."""
    from PySide6.QtCore import QSettings

    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    folder = str(tmp_path_factory.mktemp("settings"))
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, folder)
