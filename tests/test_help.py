"""Anleitung im Programm (F1), Glossar und „Erweitert“-Bereiche."""

from PySide6.QtCore import QUrl

from llmanno.gui import common
from llmanno.gui.help import ENGLISH, GLOSSARY, SCHEME, HelpWindow, manual_path, sections


def test_every_tab_points_to_existing_sections():
    from llmanno.gui import (
        codebook_page,
        data_page,
        evaluation_page,
        export_page,
        models_page,
        project_page,
        prompt_page,
        results_page,
        review_page,
        run_page,
        split_page,
    )  # fmt: skip

    titles = {s.title for s in sections(manual_path().read_text(encoding="utf-8"))}
    pages = [
        project_page.ProjectPage, data_page.DataPage, codebook_page.CodebookPage,
        models_page.ModelsPage, prompt_page.PromptPage, split_page.SplitPage,
        run_page.RunPage, results_page.ResultsPage, review_page.ReviewPage,
        evaluation_page.EvaluationPage, export_page.ExportPage,
    ]  # fmt: skip
    for page in pages:
        assert page.help_topics, page
        assert set(page.help_topics) <= titles, (page, set(page.help_topics) - titles)
    assert GLOSSARY in titles


def test_sections_nest_and_skip_code_blocks():
    text = "# T\n\n## A\neins\n```\n## kein Titel\n```\n### A1\nzwei\n## B\ndrei\n"
    found = {s.title: s for s in sections(text)}
    assert list(found) == ["T", "A", "A1", "B"]
    assert "zwei" in found["A"].text and "drei" not in found["A"].text
    assert found["A1"].level == 3


def test_window_shows_topic_related_links_and_search(qtbot):
    window = HelpWindow()
    qtbot.addWidget(window)
    window.show_topic("2. Daten", ("Datenschutz", GLOSSARY))
    text = window.view.toPlainText()
    assert text.startswith("2. Daten") and "Siehe auch" in text and "Glossar" in text
    glossary = window.index(GLOSSARY)
    window._link(QUrl(f"{SCHEME}:{glossary}"))
    assert window.view.toPlainText().startswith("Glossar")
    window.search.setText("Kontingent")
    visible = [
        window.sections[i].title
        for i, item in enumerate(window._items)
        if item.treeWidget() is not None and not item.isHidden()
    ]
    assert "Kosten und Kontingent" in visible and "3. Codebuch" not in visible


def test_english_manual_has_the_same_sections():
    """Das Hilfefenster ordnet die Abschnitte über ihre Stelle zu."""
    german = sections(manual_path().read_text(encoding="utf-8"))
    english = sections(manual_path(ENGLISH).read_text(encoding="utf-8"))
    assert [s.level for s in english] == [s.level for s in german]
    assert [s.title.split(".")[0] for s in english if s.title[0].isdigit()] == [
        s.title.split(".")[0] for s in german if s.title[0].isdigit()
    ]


def test_english_ui_shows_the_english_manual(qtbot, monkeypatch):
    monkeypatch.setattr(common, "UI_LANGUAGE", "en")
    window = HelpWindow()
    qtbot.addWidget(window)
    assert not window.german
    window.show_topic("4. Modelle", ("Datenschutz",))
    english = sections(manual_path(ENGLISH).read_text(encoding="utf-8"))
    index = window.index("4. Modelle")
    assert window.view.toPlainText().startswith(english[index].title)
    assert english[window.index("Datenschutz")].title in window.view.toPlainText()  # Siehe auch
    assert window.index(GLOSSARY) is not None


def test_f1_opens_the_section_of_the_current_tab(qtbot):
    from llmanno.gui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    window.tabs.setCurrentWidget(window.models_page)
    window.show_tab_help()
    assert window._help_window.view.toPlainText().startswith("4. Modelle")
    assert window.tabs.tabToolTip(window.tabs.indexOf(window.split_page))


def test_advanced_sections_follow_the_view_menu(qtbot):
    from llmanno.gui.common import Collapsible, advanced_setting, set_advanced

    set_advanced(False)
    box = Collapsible()
    qtbot.addWidget(box)
    box.show()
    assert not box.body.isVisible()
    box.toggle.click()
    assert box.body.isVisible() and not advanced_setting()  # einzeln, ohne Menü
    set_advanced(True)
    other = Collapsible()
    qtbot.addWidget(other)
    other.show()
    assert other.body.isVisible() and advanced_setting()
    set_advanced(False)
    assert not box.body.isVisible() and not other.body.isVisible()
