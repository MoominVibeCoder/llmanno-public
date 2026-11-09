import csv
import io
import json
import zipfile

import pytest
from helpers import FakeProvider
from test_evaluation import ROWS, llm_answer, make_project

from llmanno.core.evaluation import evaluate
from llmanno.core.export.replication import ROOT, export_replication
from llmanno.core.review import add_review
from llmanno.rerun import main, rerun


@pytest.fixture
def project(tmp_path):
    project = make_project(tmp_path)
    yield project
    project.close()


def _files(path):
    with zipfile.ZipFile(path) as archive:
        return {n.removeprefix(f"{ROOT}/"): archive.read(n) for n in archive.namelist()}


def test_package_without_texts_keeps_raw_data_out(project, tmp_path):
    add_review(project, "U01", "Ana", "blind", ["K1"], note="Zitat aus dem Text")
    report = evaluate(project, resamples=20)
    path = tmp_path / "ohne.zip"
    export_replication(project, path, report=report)
    files = _files(path)
    assert {"README.md", "projekt/project.json", "projekt/codebook.txt", "calls.jsonl"} <= set(
        files
    )
    assert {"units.csv", "results.csv", "reviews.csv", "metrics.json", "environment.txt"} <= set(
        files
    )
    assert "texts.jsonl" not in files and "projekt/data.xlsx" not in files
    everything = b"".join(files.values()).decode("utf-8", errors="ignore")
    for unit_text in (r[3] for r in ROWS[:5]):
        assert unit_text not in everything  # weder Text noch Prompt noch Roh-Antwort
    assert "Zitat aus dem Text" not in everything
    units = list(csv.reader(io.StringIO(files["units.csv"].decode("utf-8"))))
    assert units[0][:5] == ["unit_id", "zeile", "gruppe", "reihenfolge", "sprecher"]
    assert "text" not in units[0] and units[0][-2:] == ["gold_A", "gold_B"]
    call = json.loads(files["calls.jsonl"].decode("utf-8").splitlines()[0])
    assert "raw" not in call and call["decision"] and call["candidates"][0]["confidence"]
    metrics = json.loads(files["metrics.json"])
    assert any(r["kennwert"] == "Cohens Kappa" for r in metrics["rows"])
    assert "nicht enthalten" in files["README.md"].decode("utf-8")
    assert project.store.audit()[-1].details["include_texts"] is False


def test_package_with_texts_and_rerun_reproduces(project, tmp_path):
    path = tmp_path / "mit.zip"
    export_replication(project, path, include_texts=True)
    files = _files(path)
    assert "projekt/data.xlsx" in files and "texts.jsonl" in files
    assert ROWS[0][3] in files["units.csv"].decode("utf-8")

    report = rerun(path, tmp_path / "rerun", provider_factory=lambda cfg: FakeProvider(llm_answer))
    model = report.models[0]
    assert model.same_identity and model.digest_original == model.digest_rerun == "d1"
    assert model.compared > 0 and model.identical == model.compared
    assert report.items_identical == report.items_compared == len(ROWS)
    assert report.final_kappa == pytest.approx(1.0)
    assert (tmp_path / "rerun" / "rerun_vergleich.md").is_file()


def test_rerun_reports_a_changed_model_build(project, tmp_path):
    path = tmp_path / "mit.zip"
    export_replication(project, path, include_texts=True)
    report = rerun(
        path,
        tmp_path / "rerun",
        provider_factory=lambda cfg: FakeProvider(llm_answer, digest="d2"),
    )
    assert not report.models[0].same_identity
    assert any("anderer Modell-Digest" in w for w in report.warnings)


def test_rerun_without_texts_needs_the_original_data(project, tmp_path):
    path = tmp_path / "ohne.zip"
    export_replication(project, path)
    assert main([str(path), "--out", str(tmp_path / "r1")]) == 1
    data = tmp_path / "d.xlsx"  # die Originaldatei aus make_project
    report = rerun(
        path, tmp_path / "r2", data=data, provider_factory=lambda cfg: FakeProvider(llm_answer)
    )
    assert report.items_identical == len(ROWS)

    from helpers import write_xlsx

    other = write_xlsx(tmp_path / "x.xlsx", ["ID", "Transkript", "Nr", "Text", "A", "B", "C"],
                       [list(r) for r in ROWS[:-1]])  # fmt: skip
    with pytest.raises(ValueError, match="weichen vom Original ab"):
        rerun(path, tmp_path / "r3", data=other, provider_factory=lambda c: FakeProvider(None))


def test_export_page_writes_the_package(qtbot, project, tmp_path):
    from llmanno.core.project import Project
    from llmanno.gui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    window.set_project(Project.open(project.folder))
    page = window.export_page
    page.refresh()
    assert page.replication_button.isEnabled() and not page.include_texts.isChecked()
    page.export_replication_to(tmp_path / "p.zip")
    qtbot.waitUntil(lambda: not page.busy, timeout=60000)
    assert not page.error.text()
    assert "metrics.json" not in _files(tmp_path / "p.zip")  # noch keine Auswertung
    page.include_texts.setChecked(True)
    page.export_replication_to(tmp_path / "t.zip")
    qtbot.waitUntil(lambda: not page.busy, timeout=60000)
    assert "texts.jsonl" in _files(tmp_path / "t.zip")


def test_rerun_cli_reports_an_aborted_run_without_traceback(project, tmp_path, capsys, monkeypatch):
    from llmanno.core import engine as engine_module
    from llmanno.core.providers.base import ProviderError

    class Offline(FakeProvider):
        def list_models(self):
            raise ProviderError("Verbindung abgelehnt", retryable=False)

    export_replication(project, tmp_path / "p.zip", include_texts=True)
    monkeypatch.setattr(engine_module, "make_provider", lambda cfg: Offline(llm_answer))
    assert main([str(tmp_path / "p.zip"), "--out", str(tmp_path / "r")]) == 1
    assert "nicht erreichbar" in capsys.readouterr().err


def test_package_without_texts_pseudonymizes_people_and_drops_guessable_hashes(tmp_path):
    """Roadmap 33: Sprecher-Hashes per Wörterbuch umkehrbar, Reviewer-Namen und zitierte
    Antworttexte im Protokoll, Hashes kurzer Texte erratbar."""
    from helpers import ans, engine, write_xlsx

    from llmanno.core.config import Code, ColumnMapping, ModelConfig
    from llmanno.core.project import Project
    from llmanno.core.store import sha256_text

    project = Project.create(tmp_path / "p", name="Datenschutz")
    rows = [[f"U{i:02d}", "Frau Keller" if i % 2 else "Ben", f"Wort{i} Text K1", "K1"]
            for i in range(12)]  # fmt: skip
    rows[3][2] = "Ja."
    data = write_xlsx(tmp_path / "d.xlsx", ["ID", "Sprecher", "Text", "A"], rows)
    project.import_data(
        data, "Daten", ColumnMapping(id="ID", text=["Text"], speaker="Sprecher", gold=["A"])
    )
    codebook = tmp_path / "cb.txt"
    codebook.write_text("### K1 — a\n### K2 — b\n", encoding="utf-8")
    project.load_codebook(codebook)
    codes = [Code(id="K1"), Code(id="K2")]
    project.update(
        codebook=project.config.codebook.model_copy(update={"codes": codes}),
        panel=[ModelConfig(provider="ollama", model="fake")],
    )
    project.save()
    engine(project, FakeProvider(lambda r, n: ans("K1", evidence=["Text"]))).run()
    add_review(project, "U01", "Anna Schmidt", "blind", ["K1"])
    echo = 'HTTP 400: {"echo": "Wort3 Ja. vertraulich"}'
    project.store.log("run_aborted", reason=echo, type="RunAborted")
    export_replication(project, tmp_path / "ohne.zip")
    files = _files(tmp_path / "ohne.zip")
    everything = b"".join(files.values()).decode("utf-8", errors="ignore")
    for secret in ("Frau Keller", "Anna Schmidt", "vertraulich", sha256_text("Frau Keller"),
                   sha256_text("Ja."), sha256_text("Ben")):  # fmt: skip
        assert secret not in everything, secret
    units = list(csv.DictReader(io.StringIO(files["units.csv"].decode("utf-8"))))
    assert [u["sprecher"] for u in units[:3]] == ["S01", "S02", "S01"]
    reviews = list(csv.DictReader(io.StringIO(files["reviews.csv"].decode("utf-8"))))
    assert reviews[0]["person"] == "R01"
    assert "HTTP 400 (Antworttext entfernt)" in files["audit.jsonl"].decode("utf-8")
    call = json.loads(files["calls.jsonl"].decode("utf-8").splitlines()[0])
    assert call["user_sha"] is None and call["system_sha"]

    export_replication(project, tmp_path / "mit.zip", include_texts=True)
    with_texts = _files(tmp_path / "mit.zip")
    assert "Anna Schmidt" in with_texts["reviews.csv"].decode("utf-8")
    assert "Frau Keller" in with_texts["units.csv"].decode("utf-8")
    project.close()


def test_pseudonymized_ids_stay_out_and_rerun_restores_them(project, tmp_path):
    import re

    add_review(project, "U03", "Ana", "blind", ["K1"])
    path = tmp_path / "pseudo.zip"
    export_replication(project, path, pseudonymize_ids=True)
    files = _files(path)
    everything = b"".join(files.values()).decode("utf-8", errors="ignore")
    assert not re.search(r"\bU\d\d\b", everything)  # Einheiten-IDs
    assert not re.search(r"\bT\d\b", everything)  # Gruppen-IDs
    units = list(csv.reader(io.StringIO(files["units.csv"].decode("utf-8"))))
    assert units[1][:3] == ["E01", "2", "G01"] and units[-1][2] == "G03"
    reviews = list(csv.reader(io.StringIO(files["reviews.csv"].decode("utf-8"))))
    assert reviews[1][0] == "E04"
    assert json.loads(files["paket.json"]) == {"rohtexte": False, "ids_pseudonym": True}
    assert "E01, G01" in files["README.md"].decode("utf-8")

    report = rerun(
        path, tmp_path / "r", data=tmp_path / "d.xlsx",
        provider_factory=lambda cfg: FakeProvider(llm_answer),
    )  # fmt: skip
    assert report.models[0].identical == report.models[0].compared > 0
    assert report.items_identical == report.items_compared == len(ROWS)


def test_pseudonymized_ids_only_without_texts(project, tmp_path):
    with pytest.raises(ValueError, match="nur ohne Rohtexte"):
        export_replication(project, tmp_path / "x.zip", include_texts=True, pseudonymize_ids=True)


def test_export_page_offers_id_pseudonyms_only_without_texts(qtbot):
    from llmanno.gui.export_page import ExportPage

    page = ExportPage()
    qtbot.addWidget(page)
    assert page.pseudonymize_ids.isEnabled() and not page.pseudonymize_ids.isChecked()
    page.include_texts.setChecked(True)
    assert not page.pseudonymize_ids.isEnabled()


def test_rerun_without_order_column(tmp_path):
    """Ohne Reihenfolge-Spalte scheiterte jeder Rerun: Der Import rechnete die Zeilennummer
    ganzzahlig in die Prüfsumme, der Store liefert sie als Kommazahl."""
    from helpers import engine, write_xlsx

    from llmanno.core.config import Code, ColumnMapping, ModelConfig
    from llmanno.core.data_import import units_sha256
    from llmanno.core.project import Project

    data = write_xlsx(tmp_path / "d.xlsx", ["ID", "Text"], [[r[0], r[3]] for r in ROWS])
    project = Project.create(tmp_path / "p", name="Ohne Nr")
    project.import_data(data, "Daten", ColumnMapping(id="ID", text=["Text"]))
    assert project.config.data.units_sha256 == units_sha256(project.store.units())
    codebook = tmp_path / "cb.txt"
    codebook.write_text("### K1\n### K2\n### K3\n", encoding="utf-8")
    project.load_codebook(codebook)
    project.update(
        codebook=project.config.codebook.model_copy(
            update={"codes": [Code(id=c) for c in ("K1", "K2", "K3")]}
        ),
        panel=[ModelConfig(provider="ollama", model="fake")],
    )
    project.save()
    engine(project, FakeProvider(llm_answer)).run()
    # Prüfsumme wie vor der Korrektur (ganze Zahlen): Solche Projekte bleiben rerun-fähig.
    from dataclasses import replace

    old = units_sha256([replace(u, order=int(u.order)) for u in project.store.units()])
    project.update(data=project.config.data.model_copy(update={"units_sha256": old}))
    project.save()
    export_replication(project, tmp_path / "paket.zip")
    project.close()
    report = rerun(
        tmp_path / "paket.zip",
        tmp_path / "rerun",
        data=data,
        provider_factory=lambda cfg: FakeProvider(llm_answer),
    )
    assert report.items_compared == len(ROWS)
