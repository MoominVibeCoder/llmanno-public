"""Trockenlauf vor dem Start (Roadmap 25)."""

from helpers import write_xlsx

from llmanno.core.config import Code, ColumnMapping, ModelConfig
from llmanno.core.engine import run_budget
from llmanno.core.project import Project


def outlier_project(tmp_path):
    project = Project.create(tmp_path / "p", name="Budget")
    rows = [[f"U{i}", f"Kurzer Text {i}."] for i in range(10)] + [["LANG", "Wort " * 3000]]
    data = write_xlsx(tmp_path / "d.xlsx", ["ID", "Text"], rows)
    project.import_data(data, "Daten", ColumnMapping(id="ID", text=["Text"]))
    codebook = tmp_path / "cb.txt"
    codebook.write_text("### K1 — a\n### K2 — b\n", encoding="utf-8")
    project.load_codebook(codebook)
    project.update(
        codebook=project.config.codebook.model_copy(
            update={"codes": [Code(id="K1"), Code(id="K2")]}
        ),
        panel=[ModelConfig(provider="ollama", model="a", max_tokens=400)],
    )
    project.save()
    return project


def test_budget_finds_the_outlier_and_counts_calls_and_tokens(qtbot, tmp_path, monkeypatch):
    project = outlier_project(tmp_path)
    budget = run_budget(project)
    runs = project.config.coding.n_runs
    assert budget.calls == 11 * runs and budget.tokens_out == 11 * runs * 400
    assert budget.longest[0] == "LANG" and budget.outlier
    assert budget.tokens_in > budget.longest[1] * runs
    small = run_budget(project, item_ids={"U1", "U2"}, runs=1)
    assert small.calls == 2 and not small.outlier

    from PySide6.QtWidgets import QMessageBox

    from llmanno.gui.prompt_page import PromptPage
    from llmanno.gui.run_page import RunPage

    page = PromptPage()
    qtbot.addWidget(page)
    page.set_project(project)
    assert "Ausreißer: Einheit LANG" in page.stats.text()

    asked = []
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a: asked.append(a) or QMessageBox.StandardButton.No
    )
    run_page = RunPage()
    qtbot.addWidget(run_page)
    run_page._ask_consent([project.config.providers[0]], 11, budget)
    assert f"{budget.calls_min} bis {budget.calls} Aufrufe" in asked[0][2]
    project.close()
