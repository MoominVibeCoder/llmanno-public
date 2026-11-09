import re
from collections import Counter

import pytest
from helpers import FakeProvider, engine, write_xlsx
from sklearn.metrics import cohen_kappa_score

from llmanno.core.config import Code, ColumnMapping, GoldConfig, ModelConfig, UnitSettings
from llmanno.core.evaluation import MAJORITY, evaluate
from llmanno.core.gold import build_gold, map_cell
from llmanno.core.project import Project
from llmanno.core.units import build_items

CODES = ["K1", "K2", "K3"]
N = 60


def _rows():
    rows = []
    for i in range(N):
        a = CODES[i % 3]
        b = a if i % 10 else CODES[(i + 1) % 3]  # B weicht bei jeder zehnten Einheit ab
        c = a if i % 7 else CODES[(i + 2) % 3]
        rows.append([f"U{i:02d}", f"T{i // 20}", i % 20, f"Wort{i} Text {a}", a, b, c])
    return rows


ROWS = _rows()
WRONG = {f"U{i:02d}" for i in range(0, N, 6)}  # das LLM irrt bei jeder sechsten Einheit
TRUTH = {r[0]: r[4] for r in ROWS}


def llm_answer(request, n):
    unit = re.search(r'<zieltext id="([^"]+)"', request.messages[1]["content"]).group(1)
    truth = TRUTH[unit]
    code = CODES[(CODES.index(truth) + 1) % 3] if unit in WRONG else truth
    word = f"Wort{int(unit[1:])}"
    alternative = truth if unit in WRONG else CODES[0]
    return (
        '{"rationale": "r", "candidates": ['
        f'{{"evidence": ["{word}"], "code": "{code}", "confidence": 80}}, '
        f'{{"evidence": ["Text"], "code": "{alternative}", "confidence": 10}}]}}'
    )


def make_project(tmp_path, coders=("A", "B"), mode="context", run=True):
    project = Project.create(tmp_path / "p", name="Eval")
    header = ["ID", "Transkript", "Nr", "Text", "A", "B", "C"]
    data = write_xlsx(tmp_path / "d.xlsx", header, ROWS)
    project.import_data(
        data, "Daten",
        ColumnMapping(id="ID", text=["Text"], group="Transkript", order="Nr", gold=list(coders)),
    )  # fmt: skip
    codebook = tmp_path / "cb.txt"
    codebook.write_text("### K1 — a\n### K2 — b\n### K3 — c\n", encoding="utf-8")
    project.load_codebook(codebook)
    project.update(
        codebook=project.config.codebook.model_copy(update={"codes": [Code(id=c) for c in CODES]}),
        coding=project.config.coding.model_copy(update={"units": UnitSettings(mode=mode)}),
        panel=[ModelConfig(provider="ollama", model="fake")],
    )
    project.save()
    if run:
        engine(project, FakeProvider(llm_answer)).run()
    return project


def test_gold_mapping_rules(tmp_path):
    project = Project.create(tmp_path / "g", name="G")
    codebook = tmp_path / "cb.txt"
    codebook.write_text("x", encoding="utf-8")
    project.load_codebook(codebook)
    project.update(
        codebook=project.config.codebook.model_copy(
            update={"codes": [Code(id="K1"), Code(id="K_2")]}
        ),
        gold=GoldConfig(value_map={"eins": "K1", "egal": None}),
    )
    unmapped = Counter()
    cfg = project.config
    raw = ["K1", " k1 ", "K 2", "eins", "egal", "", None]
    assert [map_cell(v, cfg, unmapped) for v in raw] == [
        "K1", "K1", "K_2", "K1", None, None, None,
    ]  # fmt: skip
    assert map_cell("K9", cfg, unmapped) is None and unmapped == Counter({"K9": 1})
    project.update(mode="multi")
    assert map_cell("K1; k_2;K9", project.config, unmapped) == frozenset({"K1", "K_2"})
    project.close()


def test_holistic_gold_must_be_consistent_within_a_group(tmp_path):
    project = make_project(tmp_path, mode="holistic", run=False)
    items = build_items(project.store.units(), project.config.coding.units)
    gold = build_gold(project.config, project.store.units(), items)
    assert gold.inconsistent == ["T0", "T1", "T2"]  # A wechselt innerhalb jeder Gruppe
    assert all(v is None for v in gold.labels["A"].values())
    project.close()


def test_evaluation_against_known_truth(tmp_path):
    project = make_project(tmp_path)
    report = evaluate(project, resamples=50)
    assert report.n_items == N and report.n_scored == N and report.clustered
    names = [c.reference for c in report.comparisons]
    assert names == ["A", "B", MAJORITY]

    vs_a = report.comparisons[0]
    expected_accuracy = 1 - len(WRONG) / N
    assert vs_a.accuracy.value == pytest.approx(expected_accuracy)
    assert vs_a.accuracy.low <= expected_accuracy <= vs_a.accuracy.high
    llm = [CODES[(CODES.index(TRUTH[u]) + 1) % 3] if u in WRONG else TRUTH[u] for u in TRUTH]
    assert vs_a.agreement.kappa.value == pytest.approx(cohen_kappa_score(list(TRUTH.values()), llm))
    assert vs_a.top_k[2].value == 1.0  # die Alternative ist bei Fehlern immer die Wahrheit
    assert vs_a.calibration["p_verbal"].n == N
    assert vs_a.single.per_class["K1"].support == 20
    assert vs_a.low_support == ["KEIN_CODE"]  # K1 bis K3 je genau 20 Fälle, KEIN_CODE keiner

    # Mehrheit bei zwei Kodierenden: nur Einheiten, in denen A und B übereinstimmen.
    assert report.comparisons[2].agreement.n == N - len(range(0, N, 10))

    baseline = report.baseline
    a = [r[4] for r in ROWS]
    b = [r[5] for r in ROWS]
    assert baseline.pairs[("A", "B")].kappa.value == pytest.approx(cohen_kappa_score(a, b))
    assert baseline.alpha_with_llm < baseline.alpha  # das LLM irrt öfter als B
    assert report.stability.intra_alpha["ollama/fake"] == pytest.approx(1.0)
    assert report.stability.inter_alpha is None
    assert report.alt_test is None and any("Alt-Test" in w for w in report.warnings)
    assert report.n_clusters == 3 and any("nur 3" in w for w in report.warnings)
    project.close()


def test_alt_test_with_three_coders(tmp_path):
    project = make_project(tmp_path, coders=("A", "B", "C"))
    report = evaluate(project, resamples=20)
    assert report.alt_test is not None and len(report.alt_test.annotators) == 3
    assert report.alt_test.epsilon == 0.2
    project.close()


def test_evaluation_needs_gold(ready_project):
    with pytest.raises(ValueError, match="Gold-Spalten"):
        evaluate(ready_project)


def test_evaluation_per_model_of_the_panel(tmp_path):
    project = make_project(tmp_path, run=False)
    project.update(panel=[ModelConfig(provider="ollama", model=m) for m in ("fake", "k1")])
    project.save()

    def answer(request, n):  # "k1" vergibt immer K1, "fake" kodiert wie llm_answer
        if request.model == "k1":
            return '{"rationale": "r", "candidates": [{"evidence": ["Text"], "code": "K1", "confidence": 50}]}'  # noqa: E501
        return llm_answer(request, n)

    class TwoModels(FakeProvider):
        def list_models(self):
            info = super().list_models()[0]
            return [info, type(info)("k1", digest="d1", context_length=32768)]

    engine(project, TwoModels(answer)).run()
    panel = evaluate(project, resamples=20)
    good = evaluate(project, resamples=20, model="ollama/fake")
    bad = evaluate(project, resamples=20, model="ollama/k1")
    accuracy = {r.model: r.comparisons[0].accuracy.value for r in (panel, good, bad)}
    assert accuracy["ollama/fake"] == pytest.approx(1 - len(WRONG) / N)
    assert accuracy["ollama/k1"] == pytest.approx(sum(t == "K1" for t in TRUTH.values()) / N)
    assert good.stability.inter_alpha == panel.stability.inter_alpha  # Stabilität bleibt Panel
    with pytest.raises(ValueError, match="nicht im Panel"):
        evaluate(project, model="ollama/fehlt")
    project.close()


def test_incomplete_panel_is_reported(tmp_path):
    """Roadmap 4: Das zweite Modell lief nur auf 5 von 60 Einheiten, ohne Hinweis."""
    from llmanno.core.aggregate import PANEL_INCOMPLETE
    from llmanno.core.engine import results
    from llmanno.core.providers.base import ModelInfo

    project = make_project(tmp_path)
    project.update(panel=[*project.config.panel, ModelConfig(provider="ollama", model="fake2")])
    provider = FakeProvider(llm_answer)
    provider.list_models = lambda: [ModelInfo(m, digest="d1") for m in ("fake", "fake2")]
    engine(project, provider).run(item_limit=5)
    report = evaluate(project, resamples=20)
    assert any(w.startswith("55 Einheiten mit unvollständigem Panel") for w in report.warnings)
    by_item = results(project, [f"U{i:02d}" for i in range(60)])
    assert sum(PANEL_INCOMPLETE in r.flags for r in by_item.values()) == 55
    single = evaluate(project, resamples=20, model=project.config.panel[0].key)
    assert not any("unvollständigem Panel" in w for w in single.warnings)
    project.close()
