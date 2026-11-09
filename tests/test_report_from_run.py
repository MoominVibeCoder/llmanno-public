"""Report und GUIDE-LLM beschreiben den Lauf, nicht die Konfiguration von heute (Roadmap 1)."""

from helpers import FakeProvider, engine
from test_evaluation import llm_answer, make_project

from llmanno.core.config import ModelConfig
from llmanno.core.export.guide import guide_rows
from llmanno.core.export.report import methods_paragraph, report_html, run_models


def changed_after_the_run(tmp_path):
    """Gelaufen: fake mit Temperatur 0,7, max. 400 Tokens, N = 3. Danach umgestellt."""
    project = make_project(tmp_path, run=False)
    model = ModelConfig(provider="ollama", model="fake", temperature=0.7, max_tokens=400)
    project.update(panel=[model], coding=project.config.coding.model_copy(update={"n_runs": 3}))
    engine(project, FakeProvider(llm_answer)).run()
    project.update(
        panel=[
            model.model_copy(update={"temperature": 0.2, "max_tokens": 999}),
            ModelConfig(provider="ollama", model="nie-gelaufen"),
        ],
        coding=project.config.coding.model_copy(update={"n_runs": 5}),
    )
    return project


def test_methods_and_tables_come_from_the_run(tmp_path):
    project = changed_after_the_run(tmp_path)
    (run,) = run_models(project)
    assert (run.temperature, run.config.max_tokens, run.n_runs, run.logged) == (0.7, 400, 3, True)

    paragraph = methods_paragraph(project, None, "de")
    assert "mit fake kodiert" in paragraph and "nie-gelaufen" not in paragraph
    assert "bis zu 3 Durchläufe mit Temperatur 0,7" in paragraph
    assert "up to 3 times per model at temperature 0.7" in methods_paragraph(project, None, "en")

    html = report_html(project, None)
    assert "nie-gelaufen" not in html and "999" not in html
    assert "Das Instrument wurde nach dem Lauf geändert" in html
    params = dict((topic, text) for _, topic, text in guide_rows(project, None))["Parameter"]
    assert "fake: Temperatur 0,7, max. 400 Tokens" in params and "bis zu 3 Läufe" in params
    project.close()


def test_runs_without_the_full_log_and_plans_are_marked(tmp_path):
    project = changed_after_the_run(tmp_path)
    (run,) = run_models(project)
    new_keys = ("model_config", "provider_config", "coding", "reasoning_effort")
    old = {k: v for k, v in run.fact.details.items() if k not in new_keys}
    project.store.log("run_started", **old)  # wie ein Lauf aus einer älteren Version
    (run,) = run_models(project)
    assert not run.logged and run.temperature == 0.7 and run.config.max_tokens == 999
    assert "ohne vollständiges Protokoll" in report_html(project, None)
    project.close()

    planned = make_project(tmp_path / "plan", run=False)
    assert "Noch kein Lauf" in report_html(planned, None)
    planned.close()
