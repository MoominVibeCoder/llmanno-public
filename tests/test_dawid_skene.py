"""Dawid-Skene als Gegenprobe zur gleichgewichteten Mittelung (Backlog v2)."""

import json

import numpy as np
import pytest

from llmanno.core.metrics.dawid_skene import dawid_skene


def _hard(votes):
    """Eine Stimme je Modell als Anteil 1."""
    return [{m: {code: 1.0} for m, code in row.items()} for row in votes]


def test_equal_models_decide_like_the_panel_mean():
    """Review: a=AAABB, b=AAABB, c=BBBBB. Harte Stimmen ergäben A, das Panel-Mittel der
    Self-Consistency B; mit Anteilen entscheidet Dawid-Skene wie das Panel."""
    split = {"a": {"A": 0.6, "B": 0.4}, "b": {"A": 0.6, "B": 0.4}, "c": {"B": 1.0}}
    same = [{m: {c: 1.0} for m in "abc"} for c in "AB" * 15]
    result = dawid_skene([split, *same], ["A", "B"])
    assert result.labels[0] == "B"


def _simulate(rng, n, accuracies, classes=("A", "B", "C")):
    truth = rng.choice(classes, size=n, p=[0.5, 0.3, 0.2])
    votes = []
    for t in truth:
        row = {}
        for m, acc in accuracies.items():
            wrong = [c for c in classes if c != t]
            row[m] = t if rng.random() < acc else rng.choice(wrong)
        votes.append(row)
    return list(truth), votes


@pytest.mark.parametrize("seed", range(3))
def test_recovers_accuracies_and_beats_the_majority_with_one_weak_model(seed):
    """Volles Modell (Konfusionsmatrizen) lag hier bis 6 Punkte unter dem besten Modell allein."""
    rng = np.random.default_rng(seed)
    truth, votes = _simulate(rng, 2000, {"gut": 0.9, "mittel": 0.75, "schwach": 0.45})
    result = dawid_skene(_hard(votes), ["A", "B", "C"])
    assert result.accuracy["gut"] == pytest.approx(0.9, abs=0.05)
    assert result.accuracy["schwach"] == pytest.approx(0.45, abs=0.05)
    ds = np.mean([a == b for a, b in zip(result.labels, truth, strict=True)])
    alone = np.mean([row["gut"] == t for row, t in zip(votes, truth, strict=True)])
    majority = []  # Gleichstand: das beste Modell (Wissen, das Dawid-Skene nicht hat)
    for row in votes:
        values = list(row.values())
        best = max(set(values), key=values.count)
        majority.append(best if values.count(best) > 1 else row["gut"])
    assert ds >= np.mean([a == b for a, b in zip(majority, truth, strict=True)]) - 0.005
    assert ds >= alone - 0.01


def test_evaluation_reports_the_check_from_three_models(qtbot, tmp_path):
    import re

    from helpers import FakeProvider, engine
    from test_evaluation import CODES, TRUTH, make_project

    from llmanno.core.config import ModelConfig
    from llmanno.core.evaluation import evaluate
    from llmanno.core.export.report import report_html
    from llmanno.gui.evaluation_page import EvaluationPage

    wrong = {
        "b": {f"U{i:02d}" for i in range(0, 60, 4)},
        "c": {f"U{i:02d}" for i in range(1, 60, 3)},
    }

    def answer(request, n):
        unit = re.search(r'<zieltext id="([^"]+)"', request.messages[1]["content"]).group(1)
        code = TRUTH[unit]
        if unit in wrong.get(request.model, ()):
            code = CODES[(CODES.index(code) + 1) % 3]
        candidate = {"evidence": [f"Wort{int(unit[1:])}"], "code": code, "confidence": 80}
        return json.dumps({"rationale": "r", "candidates": [candidate]})

    class ThreeModels(FakeProvider):
        def list_models(self):
            info = super().list_models()[0]
            return [type(info)(m, digest="d1", context_length=32768) for m in "abc"]

    project = make_project(tmp_path, run=False)
    project.update(panel=[ModelConfig(provider="ollama", model=m) for m in "abc"])
    project.save()
    engine(project, ThreeModels(answer)).run()
    check = evaluate(project, resamples=20).dawid_skene
    keys = {m.key: m.model for m in project.config.panel}
    accuracy = {keys[k]: v for k, v in check.accuracy.items()}
    assert accuracy["a"] > accuracy["b"] > accuracy["c"]
    assert check.n == 60 and check.kappa_weighted >= check.kappa_panel
    report = evaluate(project, resamples=20)
    assert "Dawid-Skene" in report_html(project, report)
    page = EvaluationPage()
    qtbot.addWidget(page)
    page.set_project(project)
    page._show(report, page._stamp())
    page.all_metrics.setChecked(True)  # Expertenabschnitt
    assert "Dawid-Skene (Gegenprobe" in page.view.toPlainText()
    project.update(panel=project.config.panel[:2])
    assert evaluate(project, resamples=20).dawid_skene is None  # zwei Modelle: keine Gegenprobe
    project.close()


def test_missing_votes_and_unanimous_data():
    votes = [{"a": "X", "b": "X"}, {"a": "Y"}, {"b": "Y", "a": "Y"}]
    result = dawid_skene(_hard(votes), ["X", "Y"])
    assert result.labels == ["X", "Y", "Y"]
    assert all(0.9 < v <= 1 for v in result.accuracy.values())
