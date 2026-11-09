"""Unstimmigkeiten: Code-Paare gegen Gold und zwischen Modellen, LLM-Vorschläge, Arbeitsblatt."""

import openpyxl
import pytest
from helpers import FakeProvider, engine
from test_evaluation import CODES, TRUTH, WRONG, llm_answer, make_project

from llmanno.core.config import GWDG_CHAT_AI, ModelConfig
from llmanno.core.disagreements import (
    NONE,
    SUGGESTED,
    _in_scope,
    _normal,
    code_pairs,
    export_overview,
    overview,
    suggest,
    suggestions,
)
from llmanno.core.providers.base import ModelInfo
from llmanno.core.units import Item, Line
from llmanno.core.validation import create_split

DISSENT = {"U01", "U02"}  # dort weicht das zweite Modell ab


class TwoModels(FakeProvider):
    def list_models(self):
        return [ModelInfo("fake", digest="d1"), ModelInfo("zwei", digest="d2")]


def two_models(request, n):
    if request.model == "fake":
        return llm_answer(request, n)
    unit = request.messages[1]["content"].split('<zieltext id="')[1].split('"')[0]
    truth = TRUTH[unit]
    code = CODES[(CODES.index(truth) + 2) % 3] if unit in DISSENT else truth
    return (
        '{"rationale": "zwei sagt ' + code + '", "candidates": '
        f'[{{"evidence": ["Text"], "code": "{code}", "confidence": 70}}]}}'
    )


@pytest.fixture
def project(tmp_path):
    project = make_project(tmp_path, coders=("A",), run=False)
    project.update(panel=[*project.config.panel, ModelConfig(provider="ollama", model="zwei")])
    engine(project, TwoModels(two_models)).run()
    yield project
    project.close()


def test_code_pairs_single_and_multi():
    assert code_pairs(("K1",), ("K2",)) == {("K1", "K2")}
    assert code_pairs(("K1",), ("K1",)) == set()
    assert code_pairs(("K1", "K3"), ("K1", "K2")) == {("K2", "K3")}
    assert code_pairs(("K1",), ("K1", "K2")) == {("K2", NONE)}  # zu viel: gegen „kein Code“
    assert code_pairs(("K1", "K2"), ()) == {("K1", NONE), ("K2", NONE)}


def test_abstain_is_normalized_like_the_result():
    """Review: Multi-Label wie das Ergebnis, Restkategorie neben Codes fällt weg."""
    assert _normal((), "multi", "X") == ("X",)
    assert _normal(("K1", "X"), "multi", "X") == ("K1",)
    assert _normal(("X",), "single", "X") == ("X",)
    assert not code_pairs(_normal((), "multi", "X"), _normal(("X",), "multi", "X"))


def test_scope_survives_a_changed_unit_mode():
    """Review: Split auf Zeilen, dann ganze Gruppen kodiert: Test darf nicht durchrutschen."""
    lines = (Line("U1", None, "a"), Line("U2", None, "b"))
    parts = {"U1": "dev", "U2": "test"}
    assert not _in_scope(Item("T1", lines), parts, set())  # Gruppe mit einer Test-Zeile
    assert _in_scope(Item("T1", lines[:1]), parts, set())
    assert not _in_scope(Item("U9", (Line("U9", None, "c"),)), parts, set())  # nicht im Split
    assert _in_scope(Item("U9", (Line("U9", None, "c"),)), {}, set())  # ohne Split: alles
    assert not _in_scope(Item("T1", lines[:1]), parts, {"U1"})  # Beispiel im Prompt


def test_overview_counts_gold_and_model_disagreements(project):
    found = overview(project)
    assert not found.dev_only and found.reference == "A" and found.n_units == 60
    assert found.models == ["ollama/fake", "ollama/zwei"]
    seen = {c.item.item_id for p in found.pairs for c in p.cases}
    assert seen == WRONG | DISSENT
    for pair in found.pairs:
        for case in pair.cases:
            truth = TRUTH[case.item.item_id]
            assert case.reference == (truth,)
            assert case.gold == (case.item.item_id in WRONG or case.item.item_id in DISSENT)
            assert case.rationales["ollama/fake"] == "r"
            assert case.rationales["ollama/zwei"].startswith("zwei sagt ")
    assert [len(p.cases) for p in found.pairs] == sorted((len(p.cases) for p in found.pairs),
                                                         reverse=True)  # fmt: skip


def test_test_part_never_appears(project):
    parts = create_split(project)
    found = overview(project)
    test = {i for i, p in parts.items() if p == "test"}
    assert found.dev_only and test
    assert not test & {c.item.item_id for p in found.pairs for c in p.cases}
    assert found.n_units == 60 - len(test)


def test_suggestion_sends_codebook_and_cases_and_is_logged(project):
    pair = overview(project).pairs[0]
    provider = FakeProvider(lambda r, n: "**Regel:** K1 nur bei Fragen.")
    text = suggest(project, pair, "ollama/fake", provider_factory=lambda cfg: provider)
    assert text == "**Regel:** K1 nur bei Fragen."
    [request] = provider.requests
    assert request.schema is None and request.max_tokens >= 2048
    assert request.num_ctx >= 4096  # Ollama kürzte sonst still auf sein Standardfenster
    user = request.messages[1]["content"]
    assert "### K1 — a" in user and "Fall 1 (Einheit" in user
    assert "Gold (menschlich):" in user and "Begründung:" in user
    assert user.count("### Fall ") == min(len(pair.cases), 12)
    [event] = [e for e in project.store.audit() if e.event == SUGGESTED]
    assert event.details["codes"] == list(pair.codes) and not event.details["remote_consent"]
    assert text not in str(event.details)  # der Vorschlag steht im Store, nicht im Protokoll
    [saved] = suggestions(project)
    assert (saved.codes, saved.model, saved.text) == (pair.codes, "ollama/fake", text)


def test_remote_suggestion_needs_consent(project):
    project.update(
        providers=[*project.config.providers, GWDG_CHAT_AI],
        panel=[*project.config.panel, ModelConfig(provider="gwdg", model="gpt-oss-120b")],
    )
    pair = overview(project).pairs[0]
    provider = FakeProvider(lambda r, n: "x")
    with pytest.raises(PermissionError):
        suggest(project, pair, "gwdg/gpt-oss-120b", provider_factory=lambda cfg: provider)
    assert provider.requests == []

    failing = FakeProvider(lambda r, n: RuntimeError("500"))
    with pytest.raises(RuntimeError):
        suggest(project, pair, "gwdg/gpt-oss-120b", remote_consent=True,
                provider_factory=lambda cfg: failing)  # fmt: skip
    [consent] = [e for e in project.store.audit() if e.event == "remote_consent"]
    assert consent.details["purpose"] == SUGGESTED  # vermerkt, obwohl die Anfrage scheiterte
    assert failing.requests[0].num_ctx is None  # nur Ollama


def test_truncated_ollama_prompt_is_reported(project):
    pair = overview(project).pairs[0]
    provider = FakeProvider(lambda r, n: "x", tokens_in=10**6)
    with pytest.raises(ValueError, match="gekürzt"):
        suggest(project, pair, "ollama/fake", provider_factory=lambda cfg: provider)
    assert not suggestions(project)


def test_dialog_shows_pairs_cases_and_fetches_a_suggestion(project, qtbot, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    from llmanno.gui.codebook_page import CodebookPage
    from llmanno.gui.disagreement_dialog import DisagreementDialog

    page = CodebookPage()
    qtbot.addWidget(page)
    page.set_project(project)
    assert page.disagree_button.isEnabled()

    provider = FakeProvider(lambda r, n: "**Regel:** schärfer")
    dialog = DisagreementDialog(project, provider_factory=lambda cfg: provider)
    qtbot.addWidget(dialog)
    pair = dialog.selected_pair()
    assert pair is dialog.found.pairs[0] and dialog.pairs.rowCount() == len(dialog.found.pairs)
    assert dialog.cases.rowCount() == len(pair.cases)
    assert dialog.cases.horizontalHeaderItem(3).text() == "ollama/fake"
    assert "Noch kein Vorschlag" in dialog.suggestion.toPlainText()
    assert pair.cases[0].item.item_id in dialog.detail.toPlainText()

    dialog.suggest_button.click()  # lokal: ohne Rückfrage
    qtbot.waitUntil(lambda: dialog.copy_button.isEnabled(), timeout=10000)
    assert "Regel: schärfer" in dialog.suggestion.toPlainText()
    assert "ungeprüft" in dialog.suggestion.toPlainText() and len(provider.requests) == 1

    path = tmp_path / "blatt.xlsx"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(path), ""))
    dialog.save_button.click()
    assert path.is_file() and "Gespeichert" in dialog.scope.text()

    project.update(
        providers=[*project.config.providers, GWDG_CHAT_AI],
        panel=[*project.config.panel, ModelConfig(provider="gwdg", model="gpt-oss-120b")],
    )
    dialog = DisagreementDialog(project, provider_factory=lambda cfg: provider)
    qtbot.addWidget(dialog)
    dialog.model_combo.setCurrentIndex(dialog.model_combo.findData("gwdg/gpt-oss-120b"))
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a: asked.append(a[2]) or QMessageBox.StandardButton.No)  # fmt: skip
    dialog.suggest_button.click()
    assert asked and "GWDG" in asked[0] and len(provider.requests) == 1  # abgelehnt: nichts raus


def test_worksheet_lists_pairs_cases_and_latest_suggestion(project, tmp_path):
    found = overview(project)
    pair = found.pairs[0]
    suggest(project, pair, "ollama/fake",
            provider_factory=lambda cfg: FakeProvider(lambda r, n: "Vorschlag"))  # fmt: skip
    path = tmp_path / "abgrenzungen.xlsx"
    export_overview(project, found, path)
    book = openpyxl.load_workbook(path)
    assert book.sheetnames == ["Info", "Abgrenzungen", "Fälle"]
    rows = list(book["Abgrenzungen"].iter_rows(values_only=True))
    assert rows[1][:3] == (*pair.codes, len(pair.cases)) and rows[1][5] == "Vorschlag"
    cases = list(book["Fälle"].iter_rows(values_only=True))
    assert cases[0][-4:] == ("ollama/fake: Code", "ollama/fake: Begründung",
                             "ollama/zwei: Code", "ollama/zwei: Begründung")  # fmt: skip
    assert len(cases) - 1 == sum(len(p.cases) for p in found.pairs)
    assert any(e.event == "exported" and e.details["format"] == "abgrenzungen"
               for e in project.store.audit())  # fmt: skip
