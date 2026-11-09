from collections import Counter

import pytest
from helpers import FakeProvider, engine
from test_evaluation import ROWS, llm_answer, make_project

from llmanno.core import engine as engine_module
from llmanno.core.config import UnitSettings
from llmanno.core.engine import FreezeBroken
from llmanno.core.evaluation import evaluate
from llmanno.core.validation import (
    count_test_evaluations,
    create_split,
    freeze,
    freeze_intact,
    split_sha256,
    unfreeze,
)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(engine_module, "make_provider", lambda cfg: FakeProvider(llm_answer))
    project = make_project(tmp_path, run=False)
    yield project
    project.close()


def test_grouped_split_keeps_transcripts_together(project):
    parts = create_split(project)
    group = {r[0]: r[1] for r in ROWS}
    for g in {r[1] for r in ROWS}:
        assert len({parts[u] for u in parts if group[u] == g}) == 1
    assert Counter(parts.values())["dev"] >= 0.25 * len(ROWS)
    assert project.store.audit()[-1].details["grouped"] is True
    again = dict(parts)
    assert create_split(project) == again  # gleicher Seed, gleicher Split


def test_stratified_split_per_unit(project):
    project.update(
        coding=project.config.coding.model_copy(update={"units": UnitSettings(mode="isolated")}),
        split=project.config.split.model_copy(update={"independent_units": True}),
    )
    parts = create_split(project)
    truth = {r[0]: r[4] for r in ROWS}
    for code in ("K1", "K2", "K3"):
        members = [u for u in parts if truth[u] == code]
        assert sum(parts[u] == "dev" for u in members) == round(0.25 * len(members))
    other = project.config.split.model_copy(update={"seed": 2})
    project.update(split=other)
    assert split_sha256(create_split(project)) != split_sha256(parts)


def test_test_set_is_locked_until_freeze(project):
    parts = create_split(project)
    dev = {u for u, p in parts.items() if p == "dev"}
    summary = engine(project, FakeProvider(llm_answer)).run(item_ids=dev)
    assert {c.item_id for c in project.store.calls()} == dev and summary.calls > 0

    report = evaluate(project, subset="dev", resamples=20)
    assert report.subset == "dev" and report.comparisons[0].agreement.n == len(dev)
    for subset in ("test", "all"):
        with pytest.raises(PermissionError, match="gesperrt"):
            evaluate(project, subset=subset, resamples=20)
    assert count_test_evaluations(project) == 0


def test_freeze_protects_the_instrument(project):
    create_split(project)
    state = freeze(project)
    assert freeze_intact(project) is True and state.split_sha256
    engine(project, FakeProvider(llm_answer)).run()

    first = evaluate(project, subset="test", resamples=20)
    assert first.test_evaluations == 1 and first.instrument_hash == state.instrument_hash
    second = evaluate(project, subset="test", resamples=20)
    assert any("2-mal" in w for w in second.warnings)
    with pytest.raises(PermissionError, match="schon ausgewertet|eingefroren"):
        create_split(project)

    seed = project.config.coding.base_seed
    project.update(coding=project.config.coding.model_copy(update={"base_seed": seed + 1}))
    assert freeze_intact(project) is False
    with pytest.raises(FreezeBroken, match="Freeze"):
        engine(project, FakeProvider(llm_answer)).run()
    assert project.store.audit()[-1].event == "run_aborted"
    assert any(e.event == "freeze_broken" for e in project.store.audit())
    assert any("gebrochen" in w for w in evaluate(project, subset="test", resamples=20).warnings)

    with pytest.raises(ValueError, match="begründen"):
        unfreeze(project, " ")
    unfreeze(project, "Seed versehentlich geändert, neuer Freeze folgt")
    assert project.config.freeze is None and project.store.audit()[-1].event == "unfrozen"
    engine(project, FakeProvider(llm_answer)).run(item_limit=1)


def test_digest_change_breaks_the_freeze(project):
    freeze(project)

    class Updated(FakeProvider):
        def list_models(self):
            return [type(super().list_models()[0])("fake", digest="neu", context_length=32768)]

    with pytest.raises(FreezeBroken, match="ollama/fake"):
        engine(project, Updated(llm_answer)).run()


def test_without_split_everything_may_be_evaluated_with_a_warning(project):
    engine(project, FakeProvider(llm_answer)).run()
    report = evaluate(project, resamples=20)
    assert not report.has_split and any("Kein Dev/Test-Split" in w for w in report.warnings)
    with pytest.raises(ValueError, match="keinen Dev/Test-Split"):
        evaluate(project, subset="dev")


def test_preregistration_contains_the_frozen_instrument(project, tmp_path):
    from llmanno.core.export.prereg import export_preregistration, preregistration

    draft = preregistration(project)
    assert "**Entwurf.**" in draft
    create_split(project)
    state = freeze(project)
    path = tmp_path / "prereg.md"
    export_preregistration(project, path)
    text = path.read_text(encoding="utf-8")
    assert state.instrument_hash in text and "Entwurf" not in text
    assert "### K1 — a" in text  # Codebuch verbatim
    assert '"candidates"' in text and "<zieltext" in text
    assert "Dev:" in text and "Split-SHA-256" in text
    assert state.model_hashes["ollama/fake"] in text


def test_isolated_units_are_grouped_by_default(project):
    """Roadmap 15: Isoliert kodierte Moves weniger Transkripte galten als unabhängig."""
    from llmanno.core.evaluation import evaluate
    from llmanno.core.validation import grouped_units

    project.update(
        coding=project.config.coding.model_copy(update={"units": UnitSettings(mode="isolated")})
    )
    assert grouped_units(project.config)
    parts = create_split(project)
    group = {r[0]: r[1] for r in ROWS}
    for g in {r[1] for r in ROWS}:
        assert len({parts[u] for u in parts if group[u] == g}) == 1
    assert evaluate(project, resamples=20, subset="dev").clustered
    project.update(split=project.config.split.model_copy(update={"independent_units": True}))
    assert not grouped_units(project.config)
    holistic = project.config.coding.model_copy(update={"units": UnitSettings(mode="holistic")})
    project.update(coding=holistic)
    assert not grouped_units(project.config)
