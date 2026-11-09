"""Roadmap 35: Report, Präregistrierung, GUIDE-LLM, Paket und Kernmeldungen auf Englisch."""

import pickle
import zipfile

import pytest
from test_evaluation import make_project

from llmanno.core.evaluation import evaluate
from llmanno.core.export.guide import guide_markdown
from llmanno.core.export.prereg import preregistration
from llmanno.core.export.replication import ROOT, export_replication
from llmanno.core.export.report import Bullets, Heading, Para, Pre, Table, report_blocks
from llmanno.core.i18n import Message, localized, output_language
from llmanno.core.project import Project

# Typische Wörter der deutschen Ausgaben; in englischen Ausgaben dürfen sie nur dort stehen, wo
# Daten oder Prompt (deutsches Prompt-Template) zitiert werden.
GERMAN = ["Einheiten", "Kennwerte", "Anbieter", "Durchläufe", "Kodierende", "gültig",
          "Zufallsstichprobe", "Teilmenge", "Ausprägung", "Übereinst", "Stabilität", "ergänzen",
          "Referenzkodierung", "Codebuch", "Modelle", "Läufe"]  # fmt: skip


@pytest.fixture
def project(tmp_path):
    project = make_project(tmp_path, coders=("A", "B", "C"))
    yield project
    project.close()


def _texts(blocks):
    """Alle Texte außer Prompt-Anhang und deutschem Methodenabsatz."""
    out, german = [], False
    for block in blocks:
        if isinstance(block, Heading):
            german = block.text == "Deutsch"
            out.append(block.text)
        elif isinstance(block, Para) and not german:
            out.append(block.text)
        elif isinstance(block, Table):
            out += [str(h) for h in block.header] + [str(c) for r in block.rows for c in r]
        elif isinstance(block, Bullets):
            out += block.items
        elif isinstance(block, Pre):
            continue
    return "\n".join(out)


def test_english_report_has_no_german_output_text(project):
    report = evaluate(project, resamples=20)
    with output_language("en"):
        blocks = report_blocks(project, report)
    text = _texts(blocks)
    assert "Methods report" in text and "7. Results" in text and "GUIDE-LLM checklist" in text
    assert "No dev/test split" in text  # Warnung auf Englisch
    assert [b.text for b in blocks if isinstance(b, Heading) and b.level == 3][:2] == [
        "English",
        "Deutsch",
    ]
    found = [word for word in GERMAN if word in text]
    assert not found, found


def test_german_report_stays_german(project):
    text = _texts(report_blocks(project, evaluate(project, resamples=20)))
    assert "Methoden-Report" in text and "Kein Dev/Test-Split" in text


def test_english_preregistration_and_guide(project):
    text = preregistration(project, "en")
    assert text.startswith("# Preregistration") and "## 6. Analysis plan" in text
    outside_prompt = text.split("## 5. Prompt")[0].split("Full text as it appears")[0]
    assert not [w for w in ("Analyseeinheit", "Kodiereinheiten", "Quelle") if w in outside_prompt]
    guide = guide_markdown(project, None, "en")
    assert "# GUIDE-LLM checklist" in guide and "→ to complete" in guide and "Purpose" in guide
    assert "ergänzen" not in guide


def test_english_replication_package(project, tmp_path):
    path = tmp_path / "en.zip"
    export_replication(project, path, lang="en")
    with zipfile.ZipFile(path) as archive:
        readme = archive.read(f"{ROOT}/README.md").decode("utf-8")
        guide = archive.read(f"{ROOT}/guide_llm.md").decode("utf-8")
    assert readme.startswith("# Replication package") and "Raw texts: **not included**" in readme
    assert "GUIDE-LLM checklist" in guide
    assert project.store.audit()[-1].details["lang"] == "en"


def test_messages_carry_both_languages():
    message = Message("Deutsch", "English")
    assert message == "Deutsch" and localized(message, "en") == "English"
    assert localized(message) == "Deutsch" and localized("plain", "en") == "plain"
    copy = pickle.loads(pickle.dumps(message))
    assert copy == "Deutsch" and copy.en == "English"
    with output_language("en"):
        assert localized(message) == "English"
    with pytest.raises(ValueError):
        with output_language("fr"):
            pass


def test_output_language_menu_drives_the_exports(qtbot, project, tmp_path):
    from llmanno.gui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    window.output_actions["en"].trigger()
    window.set_project(Project.open(project.folder))
    page = window.export_page
    page.refresh()
    page.export_report_to(tmp_path / "en.html")
    qtbot.waitUntil(lambda: not page.busy, timeout=60000)
    html = (tmp_path / "en.html").read_text(encoding="utf-8")
    assert "<html lang='en'>" in html and "Methods report" in html
    window.output_actions["de"].trigger()
    page.export_report_to(tmp_path / "de.html")
    qtbot.waitUntil(lambda: not page.busy, timeout=60000)
    assert "Methoden-Report" in (tmp_path / "de.html").read_text(encoding="utf-8")
    window.set_project(None)
