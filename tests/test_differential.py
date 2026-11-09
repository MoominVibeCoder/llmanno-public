"""Differentielle Messfehler: Wald-Test der Fehlerrate je Subgruppe (Egami et al. 2023)."""

import re

import openpyxl
import pytest
from helpers import FakeProvider, engine
from test_evaluation import CODES, TRUTH, llm_answer, make_project

from llmanno.core.evaluation import evaluate
from llmanno.core.export.evaluation import export_evaluation
from llmanno.core.export.report import methods_paragraph, report_html
from llmanno.core.metrics.differential import error_rate_test, holm


def test_two_levels_match_the_hc1_formula():
    errors = [True] * 6 + [False] * 14 + [True] * 2 + [False] * 18
    values = ["a"] * 20 + ["b"] * 20
    test = error_rate_test("v", errors, values)
    # Varianz mit den Residuen unter H0 (gemeinsame Rate 0,2); bei gleich großen Gruppen ist
    # das der gepoolte Zwei-Stichproben-Test, hier mit dem HC1-Faktor n/(n − k).
    p1, p2, n, pooled = 0.3, 0.1, 40, 0.2
    expected = (p1 - p2) ** 2 / (n / (n - 2) * pooled * (1 - pooled) * (1 / 20 + 1 / 20))
    assert test.statistic == pytest.approx(expected)
    assert test.df == (1, 38) and not test.clustered
    assert test.levels == {"a": (20, 0.3), "b": (20, 0.1)}


def test_equal_rates_no_errors_small_levels_and_few_clusters():
    same = error_rate_test("v", [i % 4 < 2 for i in range(40)], ["a", "b"] * 20)  # je 50 %
    assert same.p_value > 0.5
    assert error_rate_test("v", [False] * 40, ["a", "b"] * 20).p_value == 1.0
    small = error_rate_test("v", [False] * 23, ["a"] * 10 + ["b"] * 10 + ["c"] * 3)
    assert small.dropped == 3 and set(small.levels) == {"a", "b"}
    assert error_rate_test("v", [False] * 12, ["a"] * 4 + ["b"] * 8) is None  # nur eine Stufe
    clusters = ["k1"] * 20 + ["k2"] * 20
    assert error_rate_test("v", [True, False] * 20, ["a", "b"] * 20, clusters) is None


def test_clustered_test_is_more_cautious():
    errors = [i < 8 for i in range(20)] + [i < 2 for i in range(20)]
    values = ["a"] * 20 + ["b"] * 20
    clusters = [f"k{i // 4}" for i in range(40)]  # 10 Cluster, je Stufe 5
    plain, clustered = (
        error_rate_test("v", errors, values),
        error_rate_test("v", errors, values, clusters),
    )
    assert clustered.clustered and clustered.df == (1, 9)
    assert clustered.p_value > plain.p_value


def test_holm():
    assert holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert holm([]) == []


def independent(project):
    """Isoliert und ohne Cluster: Erst so lässt sich die Fehlerrate je Gruppe testen."""
    split = project.config.split.model_copy(update={"independent_units": True})
    project.update(split=split)


def _skewed(request, n):
    """Das LLM irrt nur im ersten Transkript, dort bei jeder zweiten Einheit."""
    unit = re.search(r'<zieltext id="([^"]+)"', request.messages[1]["content"]).group(1)
    i, truth = int(unit[1:]), TRUTH[unit]
    code = CODES[(CODES.index(truth) + 1) % 3] if i < 20 and i % 2 == 0 else truth
    return (
        '{"rationale": "r", "candidates": ['
        f'{{"evidence": ["Wort{i}"], "code": "{code}", "confidence": 80}}]}}'
    )


@pytest.mark.parametrize("answer, flagged", [(llm_answer, False), (_skewed, True)])
def test_evaluation_flags_errors_that_depend_on_the_group(tmp_path, answer, flagged):
    project = make_project(tmp_path, mode="isolated", run=False)
    independent(project)  # ungeclustert: Gruppe testbar
    engine(project, FakeProvider(answer)).run()
    report = evaluate(project, resamples=20)
    assert report.differential_reference == "Mehrheit der Kodierenden"
    (test,) = report.differential
    assert test.variable == "Gruppe (Transkript)" and set(test.levels) == {"T0", "T1", "T2"}
    assert (test.p_holm < 0.05) is flagged
    assert any("Fehlerrate des LLM hängt" in w for w in report.warnings) is flagged
    paragraph = methods_paragraph(project, report, "de")
    assert ("Hinweise darauf ergaben sich" in paragraph) is flagged
    assert "Wald-Tests" in methods_paragraph(project, report, "en").replace(
        "Wald tests", "Wald-Tests"
    )
    assert "Messfehler nach Subgruppen" in report_html(project, report)
    export_evaluation(report, tmp_path / "k.xlsx")
    rows = list(openpyxl.load_workbook(tmp_path / "k.xlsx")["Messfehler"].values)
    assert rows[0][:4] == ("Variable", "Ausprägung", "n", "Fehlerrate") and len(rows) >= 4
    project.close()


def test_clustered_projects_skip_the_cluster_variable(tmp_path):
    project = make_project(tmp_path)  # Kontextmodus: nach Transkript geclustert
    report = evaluate(project, resamples=20)
    assert report.clustered and report.differential == []
    project.close()


def test_complete_separation_is_detected():
    """Review-Befund: 0 % gegen 100 % Fehler machte die Kovarianz singulär (p = 0,13)."""
    errors = [False] * 30 + [True] * 10 + [True] * 2 + [False] * 8
    values = ["a"] * 30 + ["b"] * 10 + ["c"] * 10
    assert error_rate_test("v", errors, values).p_value < 0.001
    for n_a, n_b in [(5, 5), (7, 30), (40, 6)]:
        two = error_rate_test("v", [False] * n_a + [True] * n_b, ["a"] * n_a + ["b"] * n_b)
        assert two.p_value < 0.05 and two.df[0] == 1


def test_warning_and_report_leave_out_the_level_values(tmp_path):
    """Review-Befund: Sprecher- und Meta-Werte gelangten über die Warnung ins Paket."""
    import json
    import zipfile

    from llmanno.core.export.replication import ROOT, export_replication

    project = make_project(tmp_path, mode="isolated", run=False)
    independent(project)
    engine(project, FakeProvider(_skewed)).run()
    report = evaluate(project, resamples=20)
    warning = next(w for w in report.warnings if "Fehlerrate des LLM hängt" in w)
    assert "T0" not in warning and "(zwischen 0% und " in warning
    html = report_html(project, report)
    section = html[html.index("Messfehler nach Subgruppen") :].split("<h3")[0]
    assert "T0" not in section and "3 Ausprägungen" in section
    export_replication(project, tmp_path / "p.zip", report=report)
    with zipfile.ZipFile(tmp_path / "p.zip") as archive:
        metrics = json.loads(archive.read(f"{ROOT}/metrics.json"))
    assert not any("T0" in w for w in metrics["warnings"])
    project.close()
