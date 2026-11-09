"""Codebuch-Historie (Roadmap 14)."""

import zipfile

from helpers import FakeProvider, engine
from test_evaluation import llm_answer, make_project

from llmanno.core.export.replication import ROOT, export_replication
from llmanno.core.export.report import report_html
from llmanno.core.history import codebook_history


def test_every_codebook_version_is_kept_and_reported(tmp_path):
    project = make_project(tmp_path)  # Fassung 1, ein Lauf
    path = tmp_path / "cb2.txt"
    path.write_text("### K1 — a\nNeu: genauer.\n### K2 — b\n### K3 — c\n", encoding="utf-8")
    project.load_codebook(path)
    coding = project.config.coding.model_copy(update={"max_rationale_words": 30})
    project.update(coding=coding)
    engine(project, FakeProvider(llm_answer)).run(item_limit=3)

    v1, v2 = codebook_history(project)
    assert (v1.number, v1.runs, v1.added) == (1, 1, None) and v1.text.startswith("### K1")
    assert (v2.added, v2.removed, v2.runs, v2.source) == (1, 0, 1, "cb2.txt")
    html = report_html(project, None)
    assert "Fassungen des Codebuchs" in html and "+1 / −0" in html

    export_replication(project, tmp_path / "p.zip")
    with zipfile.ZipFile(tmp_path / "p.zip") as archive:
        names = archive.namelist()
        diff = archive.read(f"{ROOT}/codebook_history/v02.diff").decode("utf-8")
    assert f"{ROOT}/codebook_history/v01_{v1.sha256[:12]}.txt" in names
    assert "+Neu: genauer." in diff
    project.close()
