import pytest
from helpers import FakeProvider, engine
from test_evaluation import TRUTH, make_project
from test_review import SHAKY, shaky_answer

from llmanno.core.project import Project
from llmanno.core.review import REVIEW_RANDOM
from llmanno.gui.main_window import MainWindow


@pytest.fixture
def window(qtbot, tmp_path):
    project = make_project(tmp_path, run=False)
    engine(project, FakeProvider(shaky_answer)).run()
    project.close()
    # Ohne qtbot.addWidget: pytest-qt schlösse das Fenster vor diesem Teardown, und die
    # ungespeicherten Review-Einstellungen lösten die Speichern-Rückfrage aus.
    window = MainWindow()
    window.set_project(Project.open(tmp_path / "p"))
    yield window
    window.set_project(None)
    window.deleteLater()


def test_blind_and_assisted_review_through_the_gui(window):
    project, page = window.project, window.review_page
    window.tabs.setCurrentWidget(page)
    assert page.position.text().startswith("Noch keine Prüfliste")
    page.tau.setValue(0.7)
    page.random_share.setValue(20)
    assert project.config.review.random_share == pytest.approx(0.2)
    page.queue_button.click()
    queue = project.store.queue()
    assert len(page._queue) == len(queue) > len(SHAKY)
    assert "unsicher" in page.queue_info.text()
    assert len(page.group.buttons()) == 4  # K1–K3 und Restkategorie

    # Blind: kein Vorschlag, nichts vorausgewählt, ohne Namen kein Speichern.
    first = page.current.item_id
    assert not page.suggestion.text() and page.selected() == []
    assert first in page.position.text() and "Text" in page.text.toPlainText()
    page.name.setText("")
    page._toggle(1)
    page.save_and_next()
    assert "Namen" in page.error.text() and page.current.item_id == first
    page.name.setText("Ana")
    page._toggle(2)  # Single-Label: ersetzt die Auswahl
    assert page.selected() == ["K2"]
    page._toggle(0)
    assert page.selected() == ["KEIN_CODE"]
    page._toggle(1)
    page.note.setText("klar")
    page.save_and_next()
    review = project.store.reviews()[-1]
    assert (review.item_id, review.reviewer, review.mode, review.codes) == (
        first, "Ana", "blind", ["K1"],
    )  # fmt: skip
    assert review.note == "klar" and page.current.item_id != first

    # Zurück zeigt die gespeicherte Review; der Neustart springt zur ersten offenen Einheit.
    page.back_button.click()
    assert page.selected() == ["K1"] and "Bereits gespeichert" in page.saved.text()
    page._restart()
    assert page.current.item_id == queue[1].item_id

    # Assistiert: Vorschlag sichtbar und vorausgewählt; danach ist blind gesperrt.
    page.mode.setCurrentIndex(page.mode.findData("assisted"))
    assert page.current.item_id == first
    assert "LLM-Vorschlag" in page.suggestion.text() and page.selected()
    second = queue[1].item_id
    page._go(1)
    page.save_and_next()
    assert project.store.reviews()[-1].mode == "assisted"
    page.mode.setCurrentIndex(page.mode.findData("blind"))
    assert page.current.item_id == second
    page.save_and_next()
    assert "schon gesehen" in page.error.text()
    # Nach dem Speichern zeigte der assistierte Modus schon die nächste Einheit samt Vorschlag:
    # Auch sie ist für Ana nicht mehr blind, obwohl dort nichts gespeichert wurde.
    third = queue[2].item_id
    page._go(2)
    page._select([TRUTH[third]])
    page.save_and_next()
    assert "schon gesehen" in page.error.text() and page.current.item_id == third

    # Ohne Namen zeigt der assistierte Modus keinen Vorschlag.
    page.mode.setCurrentIndex(page.mode.findData("assisted"))
    page.name.setText("")
    page._show()
    assert "zuerst den Namen" in page.suggestion.text() and page.selected() == []
    page.name.setText("Ana")
    page.mode.setCurrentIndex(page.mode.findData("blind"))

    # Alle übrigen blind abarbeiten; die Stichprobe erscheint in der Evaluation.
    page._go(3)
    while page.current is not None:
        page._select([TRUTH[page.current.item_id]])
        page.save_and_next()
        assert not page.error.text()
    # Die zweite und dritte Einheit hat Ana assistiert gesehen; blind bleiben sie offen.
    assert page.position.text().startswith("Ende der Prüfliste; 2 Einheiten")
    from llmanno.core.evaluation import evaluate

    names = [c.reference for c in evaluate(project, resamples=20).comparisons]
    assert REVIEW_RANDOM in names


def test_review_safeguards(window, monkeypatch):
    """Roadmap 22: Neuziehen ohne Rückfrage, „anna“ blind nach „Anna“ assistiert, zweite
    blinde Review überschrieb die erste, Tastenkürzel nur für 9 Codes."""
    from PySide6.QtWidgets import QMessageBox

    from llmanno.core.evaluation import evaluate
    from llmanno.core.review import add_review, build_queue, mark_suggestion_shown, review_gold

    project, page = window.project, window.review_page
    build_queue(project)
    queue = [e.item_id for e in project.store.queue()]
    first, second = queue[0], queue[1]

    mark_suggestion_shown(project, first, "Anna")
    with pytest.raises(ValueError, match="schon gesehen"):
        add_review(project, first, " anna ", "blind", [TRUTH[first]])
    assert add_review(project, second, "anna", "blind", [TRUTH[second]]).reviewer == "Anna"

    other = "K1" if TRUTH[second] != "K1" else "K2"
    add_review(project, second, "Ben", "blind", [other])  # zweite Person, anderes Urteil
    layers = review_gold(project, [second])
    assert all(layer[second] == TRUTH[second] for layer in layers.values())  # Anna zählt
    report = evaluate(project, resamples=20)
    assert report.review_alpha is not None and report.review_alpha[1] == 1
    from llmanno.core.export.report import methods_paragraph

    assert "Blinde Reviews verschiedener Personen" in methods_paragraph(project, report, "de")

    asked = []
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a: asked.append(a) or QMessageBox.StandardButton.No
    )
    page.refresh()
    page.make_queue()
    assert len(asked) == 1 and [e.item_id for e in project.store.queue()] == queue


def test_letters_reach_codes_after_nine(qtbot, tmp_path):
    from llmanno.core.config import Code
    from llmanno.core.review import build_queue
    from llmanno.gui.review_page import ReviewPage

    project = make_project(tmp_path, run=False)
    codes = [Code(id=f"C{i:02d}") for i in range(12)]
    project.update(codebook=project.config.codebook.model_copy(update={"codes": codes}))
    project.save()
    build_queue(project)
    page = ReviewPage()
    qtbot.addWidget(page)
    page.set_project(project)
    labels = [b.text() for b in page.group.buttons()]
    assert labels[9].startswith("a  C09") and labels[11].startswith("c  C11")
    page._toggle("b")
    assert page.selected() == ["C10"]
    project.close()
