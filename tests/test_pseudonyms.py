"""Punkt 45: Namen vor dem Senden ersetzen."""

import json
import re
import zipfile

import pytest
from helpers import FakeProvider, engine, write_xlsx

from llmanno.core.config import Code, ColumnMapping, ModelConfig, UnitSettings
from llmanno.core.engine import fingerprint, instrument_fingerprint, prepare_prompts, results
from llmanno.core.export.replication import ROOT, export_replication
from llmanno.core.project import Project
from llmanno.core.pseudonyms import PSEUDONYM_FILE, Pseudonymizer, next_pseudonyms
from llmanno.core.transfer import transfer_instrument
from llmanno.rerun import rerun

ROWS = [
    ["U1", "Lena", "Ich glaube Tim hat recht.", "Klasse von Frau Berg", "K1"],
    ["U2", "Tim", "Lenas Idee ist gut, aber Timo nicht.", "Klasse von Frau Berg", "K2"],
    ["U3", "Lena", "Das Timing passt.", "Klasse von Frau Berg", "K1"],
]
PAIRS = [("Lena", "Person 1"), ("Tim", "Person 2"), ("Frau Berg", "Lehrkraft")]
NAMES = re.compile(r"\b(Lena|Tim|Berg)\b")


def _answer(request, n):
    """Zitiert das erste Wort des Zieltexts, also bei U1/U2 den Ersatznamen."""
    user = request.messages[1]["content"]
    target = re.search(r"<zieltext[^>]*>\n(.*)\n</zieltext>", user).group(1)
    quote = " ".join(target.split()[:3]).rstrip(",.")
    return (
        '{"rationale": "r", "candidates": ['
        f'{{"evidence": ["{quote}"], "code": "K1", "confidence": 80}}]}}'
    )


def _project(tmp_path, name="p"):
    data = write_xlsx(tmp_path / f"{name}.xlsx", ["ID", "Sprecher", "Text", "Kontext", "G"], ROWS)
    project = Project.create(tmp_path / name, name="Namen")
    project.import_data(
        data, "Daten",
        ColumnMapping(id="ID", speaker="Sprecher", text=["Text"], meta=["Kontext"], gold=["G"]),
    )  # fmt: skip
    codebook = tmp_path / "cb.txt"
    codebook.write_text("### K1 — a\n### K2 — b\n", encoding="utf-8")
    project.load_codebook(codebook)
    project.update(
        codebook=project.config.codebook.model_copy(
            update={"codes": [Code(id="K1"), Code(id="K2")]}
        ),
        coding=project.config.coding.model_copy(
            update={"units": UnitSettings(mode="context"), "n_runs": 1}
        ),
        panel=[ModelConfig(provider="ollama", model="fake")],
    )
    project.save()
    return project


def test_whole_words_genitive_and_longest_first():
    p = Pseudonymizer((("Tim", "P2"), ("Tim Berg", "P9"), ("Lena", "P1")))
    assert p.apply("Tim, Timo, Timing, Tims Heft, Tim Berg, Lenas, Malena") == (
        "P2, Timo, Timing, P2s Heft, P9, P1s, Malena"
    )
    assert next_pseudonyms([("A", "Person 1"), ("B", "Person 3")], 2) == ["Person 2", "Person 4"]


def test_no_name_reaches_the_provider(tmp_path):
    project = _project(tmp_path)
    project.set_pseudonyms(PAIRS)
    provider = FakeProvider(_answer)
    engine(project, provider).run()
    sent = "\n".join(m["content"] for r in provider.requests for m in r.messages)
    assert provider.requests and not NAMES.search(sent)
    assert "Person 1s Idee" in sent and "Timo" in sent and "Timing" in sent
    assert "Lehrkraft" in sent and 'sprecher="Person 2"' in sent  # Meta und Sprecher
    stored = {project.store.get_text(c.user_sha) for c in project.store.calls()}
    assert not any(NAMES.search(text) for text in stored)
    project.close()


def test_evidence_is_located_in_the_original_text(tmp_path):
    project = _project(tmp_path)
    project.set_pseudonyms(PAIRS)
    engine(project, FakeProvider(_answer)).run()
    by_item = results(project, ["U1", "U2"])
    for unit_id, original in [("U1", "Ich glaube Tim"), ("U2", "Lenas Idee")]:
        [span] = by_item[unit_id].evidence["K1"]
        text = next(u.text for u in project.store.units() if u.unit_id == unit_id)
        assert span.status == "exact" and text[span.start : span.end] == original
    assert not any("nicht_belegt" in c.flags for c in project.store.calls())
    project.close()


def test_list_is_part_of_the_run_identity_but_not_of_the_instrument(tmp_path):
    project = _project(tmp_path)
    before = fingerprint(project), instrument_fingerprint(project)
    project.set_pseudonyms(PAIRS)
    assert fingerprint(project) != before[0]
    assert instrument_fingerprint(project) == before[1]  # Namen gehören zu den Daten
    project.set_pseudonyms([])
    assert (fingerprint(project), instrument_fingerprint(project)) == before
    assert not (project.folder / PSEUDONYM_FILE).exists()
    project.close()


def test_missing_or_edited_list_stops_before_sending(tmp_path):
    project = _project(tmp_path)
    project.set_pseudonyms(PAIRS)
    path = project.folder / PSEUDONYM_FILE
    path.write_text(path.read_text(encoding="utf-8").replace("Tim\t", "Tom\t"), encoding="utf-8")
    with pytest.raises(ValueError, match="außerhalb von llmanno geändert"):
        prepare_prompts(project, sending=True)
    path.unlink()
    with pytest.raises(ValueError, match="fehlt"):
        engine(project, FakeProvider(_answer)).run()
    project.close()


def test_without_the_list_reports_and_exports_still_work_but_never_show_names(tmp_path):
    """Review: Ein Löschkonzept entfernt die Liste nach dem Kodieren. Bericht, Auswertung und
    Paket gehen weiter, nur ohne Beispiel einer Nutzer-Nachricht (sie enthielte Klarnamen)."""
    from llmanno.core.export.prereg import preregistration
    from llmanno.core.export.report import report_html
    from llmanno.core.prompt import render_user

    project = _project(tmp_path)
    project.set_pseudonyms(PAIRS)
    engine(project, FakeProvider(_answer)).run()
    (project.folder / PSEUDONYM_FILE).unlink()
    spec, items = prepare_prompts(project)
    assert spec.names_missing
    with pytest.raises(ValueError, match="Klarnamen"):
        render_user(spec, items[0])
    html, prereg = report_html(project, None), preregistration(project)
    assert "Namensliste ist nicht vorhanden" in html and not NAMES.search(html)
    assert "Namensliste nicht vorhanden" in prereg and not NAMES.search(prereg)
    export_replication(project, tmp_path / "p.zip", include_texts=True)
    with zipfile.ZipFile(tmp_path / "p.zip") as archive:
        assert f"{ROOT}/prompts/beispiel_user.txt" not in archive.namelist()
    project.close()


def test_unicode_forms_whitespace_and_hashtags(tmp_path):
    import unicodedata

    names = Pseudonymizer((("Jürgen", "Person 1"), ("Frau Berg", "Lehrkraft")))
    nfd = unicodedata.normalize("NFD", "Jürgen sagt")
    assert names.apply(nfd) == "Person 1 sagt"
    for text in ("Frau Berg kommt", "Frau  Berg kommt", "Frau\nBerg kommt"):
        assert names.apply(text) == "Lehrkraft kommt"
    project = _project(tmp_path)
    project.set_pseudonyms([("#Lena", "Person 1"), ("Original", "Person 2")])
    assert project.pseudonym_pairs == [("#Lena", "Person 1"), ("Original", "Person 2")]
    assert Pseudonymizer(()).apply("Lena") == "Lena"  # leere Liste trifft nichts
    with pytest.raises(ValueError, match="enthält selbst einen Namen"):
        project.set_pseudonyms([("Lena", "Tim"), ("Tim", "Person 2")])
    project.close()


def test_earlier_versions_stay_for_reruns(tmp_path):
    from llmanno.core.pseudonyms import HISTORY_DIR, file_sha

    project = _project(tmp_path)
    project.set_pseudonyms(PAIRS)
    first = project.config.coding.pseudonyms_sha256
    project.set_pseudonyms([*PAIRS, ("Mia", "Person 3")])
    kept = project.folder / HISTORY_DIR / f"{first[:12]}.tsv"
    assert file_sha(kept) == first
    project.close()


def test_names_stay_out_of_config_audit_and_package(tmp_path):
    project = _project(tmp_path)
    project.set_pseudonyms(PAIRS)
    assert "Lena" not in (project.folder / "project.json").read_text(encoding="utf-8")
    engine(project, FakeProvider(_answer)).run()
    assert not any(NAMES.search(json.dumps(e.details)) for e in project.store.audit())
    export_replication(project, tmp_path / "ohne.zip")
    with zipfile.ZipFile(tmp_path / "ohne.zip") as archive:
        assert f"{ROOT}/projekt/{PSEUDONYM_FILE}" not in archive.namelist()
        for name in archive.namelist():
            assert not NAMES.search(archive.read(name).decode("utf-8", "replace")), name
    export_replication(project, tmp_path / "mit.zip", include_texts=True)
    with zipfile.ZipFile(tmp_path / "mit.zip") as archive:
        assert f"{ROOT}/projekt/{PSEUDONYM_FILE}" in archive.namelist()
    project.close()


def test_hash_of_the_list_cannot_be_guessed(tmp_path):
    """Gleiche Liste, anderes Projekt: andere Prüfsumme (zufälliger Schlüssel in der Datei)."""
    one, two = _project(tmp_path, "a"), _project(tmp_path, "b")
    one.set_pseudonyms(PAIRS)
    two.set_pseudonyms(PAIRS)
    assert one.config.coding.pseudonyms_sha256 != two.config.coding.pseudonyms_sha256
    sha = one.config.coding.pseudonyms_sha256
    one.set_pseudonyms([*PAIRS, ("Mia", "Person 3")])
    one.set_pseudonyms(PAIRS)
    assert one.config.coding.pseudonyms_sha256 == sha  # Schlüssel bleibt beim Bearbeiten
    one.close()
    two.close()


def test_rerun_needs_the_list_and_reproduces_the_identity(tmp_path):
    project = _project(tmp_path)
    project.set_pseudonyms(PAIRS)
    engine(project, FakeProvider(_answer)).run()
    export_replication(project, tmp_path / "paket.zip")
    names = project.folder / PSEUDONYM_FILE
    data = tmp_path / "p.xlsx"
    project.close()
    factory = lambda cfg: FakeProvider(_answer)  # noqa: E731
    with pytest.raises(ValueError, match="--pseudonyme"):
        rerun(tmp_path / "paket.zip", tmp_path / "r1", data=data, provider_factory=factory)
    report = rerun(
        tmp_path / "paket.zip",
        tmp_path / "r2",
        data=data,
        name_list=names,
        provider_factory=factory,
    )
    assert report.models and all(m.same_identity for m in report.models)
    assert report.items_identical == report.items_compared == 3


def test_transfer_keeps_the_list(tmp_path):
    project = _project(tmp_path)
    project.set_pseudonyms(PAIRS)
    copy = transfer_instrument(project, tmp_path / "neu", tmp_path / "p.xlsx")
    assert copy.pseudonym_pairs == PAIRS
    spec, items = prepare_prompts(copy)
    assert spec.pseudonyms is not None
    copy.close()
    project.close()


def test_prompt_tab_edits_the_list_and_previews_what_is_sent(qtbot, tmp_path, monkeypatch):
    from llmanno.gui.prompt_page import PromptPage
    from llmanno.gui.pseudonym_dialog import PseudonymDialog

    project = _project(tmp_path)
    page = PromptPage()
    qtbot.addWidget(page)
    page.set_project(project)
    assert "aus" in page.names_state.text()

    def fill(dialog):
        dialog._add_speakers()  # Lena, Tim
        dialog._add("Frau Berg", "Lehrkraft")
        assert dialog.table.item(0, 2).text() == "3"  # Lena: zweimal Sprecherin, einmal „Lenas“
        return True

    monkeypatch.setattr(PseudonymDialog, "exec", fill)
    page._edit_names()
    assert project.pseudonym_pairs == [
        ("Lena", "Person 1"), ("Tim", "Person 2"), ("Frau Berg", "Lehrkraft"),
    ]  # fmt: skip
    assert "3 Namen" in page.names_state.text()
    assert not NAMES.search(page.user_view.toPlainText())
    project.close()


def test_outputs_say_that_names_were_replaced(tmp_path):
    from llmanno.core.export.guide import guide_rows
    from llmanno.core.export.prereg import preregistration
    from llmanno.core.export.report import report_html
    from llmanno.core.i18n import output_language

    project = _project(tmp_path)
    assert "Pseudonyme ersetzt" not in report_html(project, None)
    project.set_pseudonyms(PAIRS)
    assert "Namen werden vor dem Senden durch Pseudonyme ersetzt" in preregistration(project)
    engine(project, FakeProvider(_answer)).run()
    assert "Namen wurden vor dem Senden durch Pseudonyme ersetzt" in report_html(project, None)
    with output_language("en"):
        assert "Names were replaced by pseudonyms" in report_html(project, None)
    rows = {row[0]: row[2] for row in guide_rows(project, None)}
    assert any("durch Pseudonyme ersetzt" in text for text in rows.values())
    export_replication(project, tmp_path / "p.zip")
    with zipfile.ZipFile(tmp_path / "p.zip") as archive:
        readme = archive.read(f"{ROOT}/README.md").decode("utf-8")
    assert f"--pseudonyme {PSEUDONYM_FILE}" in readme
    project.close()
