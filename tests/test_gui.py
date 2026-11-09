import pytest

from llmanno.core.project import Project
from llmanno.gui.main_window import MainWindow


@pytest.fixture
def window(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    return window


def test_workflow_tabs_locked_without_project(window):
    assert window.tabs.count() == 11
    assert all(not window.tabs.isTabEnabled(i) for i in range(1, window.tabs.count()))
    assert not window.save_action.isEnabled()


def test_edit_and_save_project(window, tmp_path):
    window.set_project(Project.create(tmp_path / "p", name="Studie"))
    page = window.project_page
    assert all(window.tabs.isTabEnabled(i) for i in range(window.tabs.count()))
    assert page.name_edit.text() == "Studie"
    assert not page.save_button.isEnabled()

    page.k_spin.setValue(2)
    page.mode_combo.setCurrentIndex(page.mode_combo.findData("multi"))
    assert window.isWindowModified()
    assert page.save_button.isEnabled()

    window.save_project()
    assert not window.isWindowModified()
    assert not page.save_button.isEnabled()

    reopened = Project.open(tmp_path / "p")
    assert (reopened.config.k, reopened.config.mode) == (2, "multi")
    reopened.close()


def test_empty_name_blocks_saving(window, tmp_path, qtbot):
    window.set_project(Project.create(tmp_path / "p", name="Studie"))
    page = window.project_page
    page.name_edit.clear()
    qtbot.keyClicks(page.name_edit, " ")
    assert page.error_label.text()
    assert not page.save_button.isEnabled()
    assert window.project.config.name == "Studie"


def test_language_choice_is_stored_for_the_next_start(window):
    from PySide6.QtCore import QSettings

    window.language_actions["en"].trigger()
    assert QSettings().value("ui/language") == "en"
    assert "nächsten Start" in window.statusBar().currentMessage()
    window.language_actions["de"].trigger()


def test_english_translation_covers_the_main_window(qtbot):
    from PySide6.QtCore import QCoreApplication, QTranslator

    from llmanno.gui.app import I18N_DIR

    translator = QTranslator()
    assert translator.load("llmanno_en", str(I18N_DIR))
    QCoreApplication.installTranslator(translator)
    try:
        window = MainWindow()
        qtbot.addWidget(window)
        tabs = [window.tabs.tabText(i) for i in range(window.tabs.count())]
        assert tabs[:3] == ["Project", "Data", "Codebook"]
        assert "Splits && freeze" in tabs
        assert window.review_page.gates["gate_dissent"].text() == "Panel disagreement"
    finally:
        QCoreApplication.removeTranslator(translator)
