"""Befunde des Reviews zu Roadmap Teil 2."""

import threading

import pytest
from helpers import FakeProvider
from test_evaluation import llm_answer, make_project

from llmanno.core.config import OLLAMA_LOCAL, Code, CodebookConfig, UnitSettings
from llmanno.core.engine import Engine
from llmanno.core.providers.base import ProviderError


def test_stop_during_an_outage_pause_is_a_stop(ready_project, monkeypatch):
    """Befund 1: Anhalten in der Netzpause wurde als run_aborted mit „_Stopped“ protokolliert."""
    from llmanno.core import engine as engine_module

    monkeypatch.setattr(engine_module, "MAX_CONSECUTIVE_ERRORS", 1)
    stop = threading.Event()

    def wait(seconds):
        if seconds >= 60:  # die Netzpause, nicht der kurze Backoff
            stop.set()
        return stop.is_set()

    down = lambda r, n: ProviderError("Verbindung fehlgeschlagen", retryable=True)  # noqa: E731
    summary = Engine(ready_project, provider_factory=lambda c: FakeProvider(down), sleep=wait).run(
        should_stop=stop.is_set
    )
    events = [e.event for e in ready_project.store.audit()]
    assert summary.stopped and "run_stopped" in events and "run_aborted" not in events


def test_grouping_is_pinned_by_the_freeze_and_named_in_the_preregistration(tmp_path, monkeypatch):
    """Befunde 3 und 4: Präregistrierung prüfte nur den Kontextmodus; früher eingefrorene
    Projekte wechselten still zur geclusterten Auswertung."""
    from llmanno.core import engine as engine_module
    from llmanno.core.export.prereg import preregistration
    from llmanno.core.validation import create_split, freeze, grouped_units

    project = make_project(tmp_path, mode="isolated")
    monkeypatch.setattr(engine_module, "make_provider", lambda cfg: FakeProvider(llm_answer))
    create_split(project)
    text = preregistration(project)
    assert "Ziehung gruppenweise" in text and "Ziehungen, gruppenweise" in text
    state = freeze(project)
    assert state.grouped is True and grouped_units(project.config)
    project.update(split=project.config.split.model_copy(update={"independent_units": True}))
    assert grouped_units(project.config)  # nach dem Freeze zählt der Analyseplan
    old = state.model_copy(update={"grouped": None})  # vor dieser Angabe eingefroren
    project.update(freeze=old)
    assert not grouped_units(project.config)  # isoliert: damals je Einheit
    project.close()


def test_isolated_units_without_group_are_their_own_clusters(tmp_path):
    """Befund 5: Alle Einheiten ohne Gruppe bildeten einen einzigen Block."""
    from llmanno.core.validation import cluster_key

    project = make_project(tmp_path, mode="isolated", run=False)
    cfg = project.config
    assert cluster_key(cfg, "U1", None) != cluster_key(cfg, "U2", None)
    assert cluster_key(cfg, "U1", "T0") == cluster_key(cfg, "U2", "T0") == "T0"
    context = cfg.coding.model_copy(update={"units": UnitSettings(mode="context")})
    project.update(coding=context)
    assert cluster_key(project.config, "U1", None) is None  # Kontext: geteilt
    project.close()


def test_combining_marks_belong_to_the_word():
    """Befund 6: „Ba“ passte normalisiert mitten in „Bär“."""
    import unicodedata

    from llmanno.core.evidence import locate

    text = unicodedata.normalize("NFD", "Der Bär schläft")
    assert locate("Ba", text).status in ("fuzzy", "missing")
    assert locate("Bär", text).status == "normalized"


def test_lock_counter_is_thread_safe_and_create_does_not_leak(tmp_path, monkeypatch):
    """Befunde 7 und 8."""
    from llmanno.core import lock
    from llmanno.core.project import Project

    folder = tmp_path / "x"
    folder.mkdir()

    def churn():
        for _ in range(200):
            lock.acquire(folder)
            lock.release(folder)

    threads = [threading.Thread(target=churn) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not (folder / lock.LOCK_FILE).exists() and folder.resolve() not in lock._held

    monkeypatch.setattr(
        Project, "_write_config", lambda self: (_ for _ in ()).throw(OSError("voll"))
    )
    with pytest.raises(OSError, match="voll"):
        Project.create(tmp_path / "neu", name="X")
    assert (tmp_path / "neu").resolve() not in lock._held
    assert not (tmp_path / "neu" / lock.LOCK_FILE).exists()


def test_residual_hint_and_loopback_are_exact():
    """Befunde 9 und 10."""
    from llmanno.core.codebook import code_list_issues

    base = {"source_name": "c", "sha256": "x", "encoding": "utf-8"}
    codes = [
        Code(id="MIX", label="Mischform"),
        Code(id="AP", label="Andere Perspektive"),
        Code(id="U", label="Unklarheit benennen"),
        Code(id="S", label="Sonstiges"),
    ]
    text = "MIX AP U S"
    (issue,) = code_list_issues(CodebookConfig(**base, codes=codes), text)
    assert issue.startswith("„S“ sieht nach einer eigenen Restkategorie aus")
    remote = OLLAMA_LOCAL.model_copy(update={"base_url": "http://127.example.org:11434"})
    assert not remote.on_this_machine and remote.privacy_level == "external"
    assert OLLAMA_LOCAL.model_copy(update={"base_url": "http://127.0.0.2"}).on_this_machine
