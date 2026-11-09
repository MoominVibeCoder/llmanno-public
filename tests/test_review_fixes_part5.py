"""Befunde des fünften Reviews (Entscheidungen, 35, 42–44, Demo)."""

import threading
import time
import zipfile

import pytest
from helpers import FakeProvider
from test_evaluation import TRUTH, llm_answer, make_project
from test_qda_import import _code_as_qda

from llmanno.core.config import ColumnMapping
from llmanno.core.demo import DEMO_PROVIDER, create_demo
from llmanno.core.engine import Engine, RunAborted
from llmanno.core.export.guide import guide_rows
from llmanno.core.export.prereg import preregistration
from llmanno.core.export.refi import export_qdpx
from llmanno.core.export.replication import ROOT, export_replication
from llmanno.core.export.report import report_html
from llmanno.core.i18n import output_language
from llmanno.core.project import Project
from llmanno.core.providers.base import ProviderError
from llmanno.core.qda_import import add_qda_gold, default_coders, read_qdpx


def _parallel(project, n, **update):
    project.update(
        providers=[
            p.model_copy(update={"parallel": n}) if p.key == "ollama" else p
            for p in project.config.providers
        ],
        coding=project.config.coding.model_copy(update=update) if update else project.config.coding,
    )


def test_qda_reimport_replaces_the_column(tmp_path):
    project = make_project(tmp_path)
    export_qdpx(project, tmp_path / "e.qdpx")
    coded = _code_as_qda(tmp_path / "e.qdpx", tmp_path / "k.qdpx")
    qda = read_qdpx(coded, project)
    add_qda_gold(project, qda, ["Mara"], tmp_path / "1.xlsx")
    assert default_coders(read_qdpx(coded, project), project) == []  # schon übernommen
    second = _code_as_qda(tmp_path / "e.qdpx", tmp_path / "k2.qdpx", wrong=("U01", "K3"))
    add_qda_gold(project, read_qdpx(second, project), ["Mara"], tmp_path / "2.xlsx")
    assert project.config.data.mapping.gold == ["A", "B", "Mara (QDA)"]
    gold = {u.unit_id: u.gold.get("Mara (QDA)") for u in project.store.units()}
    assert gold["U00"] == TRUTH["U00"] and gold["U01"] == "K3"  # ersetzt, nicht ergänzt
    project.close()


def test_qda_gold_for_csv_projects_with_control_characters(tmp_path):
    rows = [["ID", "Text", "A"]] + [[f"U{i:02d}", f"Seite\x0c{i} Text", "K1"] for i in range(5)]
    data = tmp_path / "d.csv"
    data.write_text("\n".join(";".join(r) for r in rows) + "\n", encoding="utf-8")
    project = Project.create(tmp_path / "p", name="CSV")
    project.import_data(data, "CSV", ColumnMapping(id="ID", text=["Text"], gold=["A"]))
    codebook = tmp_path / "cb.txt"
    codebook.write_text("### K1 — a\n### K2 — b\n### K3 — c\n", encoding="utf-8")
    project.load_codebook(codebook)
    from llmanno.core.config import Code

    project.update(
        codebook=project.config.codebook.model_copy(
            update={"codes": [Code(id=c) for c in ("K1", "K2", "K3")]}
        )
    )
    project.save()
    export_qdpx(project, tmp_path / "e.qdpx")
    qda = read_qdpx(_code_as_qda(tmp_path / "e.qdpx", tmp_path / "k.qdpx"), project)
    add_qda_gold(project, qda, ["Mara"], tmp_path / "out.xlsx")
    assert project.data_path.suffix == ".tsv"
    assert all(u.gold.get("Mara (QDA)") for u in project.store.units() if u.unit_id in TRUTH)
    project.close()


def test_short_spill_and_evidence_codes_do_not_count(tmp_path):
    from llmanno.core.qda_import import _code_map

    root = __import__("xml.etree.ElementTree", fromlist=["x"]).fromstring(
        "<P><Codes><Code guid='a' name='K1 (Evidenz)'/><Code guid='b' name='K1: Frage'/>"
        "</Codes></P>"
    )
    mapping, _ = _code_map(root, ["K1", "K10"])
    assert mapping == {"a": None, "b": "K1"}


class _Network(FakeProvider):
    """Fällt aus, bis der Lauf eine Netzpause einlegt."""

    def __init__(self):
        super().__init__(llm_answer)
        self.down = True

    def chat(self, request):
        if self.down:
            raise ProviderError("Verbindung fehlgeschlagen", retryable=True)
        return super().chat(request)


def test_outage_with_parallel_requests_pauses_once(tmp_path):
    project = make_project(tmp_path, run=False)
    _parallel(project, 8, adaptive=False, n_runs=3)
    provider, pauses = _Network(), []

    def sleep(seconds):
        if seconds >= 60:
            pauses.append(seconds)
            provider.down = False

    Engine(project, provider_factory=lambda cfg: provider, sleep=sleep).run()
    assert pauses == [60]
    assert {c.status for c in project.store.calls()} == {"ok"} or len(
        [c for c in project.store.calls() if c.status == "error"]
    ) <= 5
    project.close()


def test_abort_keeps_finished_parallel_answers(tmp_path):
    project = make_project(tmp_path, run=False)
    _parallel(project, 4)
    answered = []
    lock = threading.Lock()

    def script(request, n):
        if 'id="U05"' in request.messages[1]["content"]:
            return ProviderError("HTTP 429: Kontingent", retryable=True, retry_after=3600)
        with lock:
            answered.append(n)
        return llm_answer(request, n)

    with pytest.raises(RunAborted):
        Engine(
            project, provider_factory=lambda cfg: FakeProvider(script), sleep=lambda s: None
        ).run()
    stored = [c for c in project.store.calls() if c.status == "ok"]
    assert len(stored) == len(answered) > 0  # nichts Beantwortetes weggeworfen
    project.close()


def test_no_new_requests_after_stop(tmp_path):
    project = make_project(tmp_path, run=False)
    _parallel(project, 4)
    stop = threading.Event()
    late = []

    class Provider(FakeProvider):
        def chat(self, request):
            if stop.is_set():
                late.append(request)
            response = super().chat(request)
            if len(self.requests) == 10:
                stop.set()
                time.sleep(0.1)  # Zeit, in der früher weitere Anfragen starteten
            return response

    summary = Engine(
        project, provider_factory=lambda cfg: Provider(llm_answer), sleep=lambda s: None
    ).run(should_stop=stop.is_set)
    assert summary.stopped and late == []  # nach dem Anhalten startet nichts mehr
    project.close()


def test_demo_is_labelled_as_simulated_everywhere(tmp_path):
    project = create_demo(tmp_path / "demo")
    assert "Demo-Projekt: Die Antworten stammen von einem Simulator" in report_html(project, None)
    with output_language("en"):
        assert "the answers come from a simulator" in report_html(project, None)
    assert "Simulator, nicht von einem LLM" in preregistration(project)
    rows = {row[0]: row[2] for row in guide_rows(project, None)}
    assert "Demo-Simulator, kein LLM" in rows["B.2"] and "Simulator" in rows["A.1"]
    export_replication(project, tmp_path / "p.zip")
    with zipfile.ZipFile(tmp_path / "p.zip") as archive:
        assert "Simulator" in archive.read(f"{ROOT}/README.md").decode("utf-8")
    assert any(p.key == DEMO_PROVIDER.key for p in project.config.providers)
    project.close()


def test_half_built_demo_is_not_reopened(qtbot, tmp_path, monkeypatch):
    from llmanno.gui.main_window import MainWindow

    Project.create(tmp_path / "Demo", name="halb").close()  # Aufbau abgebrochen
    monkeypatch.setattr(MainWindow, "demo_folder", lambda self: tmp_path / "Demo")
    window = MainWindow()
    qtbot.addWidget(window)
    window.open_demo()
    assert not window.open_action.isEnabled()  # während des Aufbaus gesperrt
    # demo_busy fällt, bevor der Abschluss-Slot läuft: auf die Freigabe selbst warten
    qtbot.waitUntil(
        lambda: window.project is not None and window.open_action.isEnabled(), timeout=60000
    )
    assert window.project.folder.name.startswith("Demo_")
    window.set_project(None)


def test_small_model_after_runs_keeps_the_repair(qtbot, tmp_path):
    from llmanno.core.providers.base import ModelInfo
    from llmanno.gui.models_page import ModelsPage

    project = make_project(tmp_path)  # hat schon Läufe
    page = ModelsPage()
    qtbot.addWidget(page)
    page.set_project(project)
    page.provider_list.setCurrentRow(0)
    provider = page._selected()
    page._models[provider.key] = [
        ModelInfo("klein", capabilities=("completion",), details={"parameter_size": "3B"})
    ]
    page.model_combo.setEditText("klein")
    page._add_to_panel()
    assert project.config.coding.evidence_repair is True
    assert "bleibt an" in page.notes.text()
    project.close()


def test_concurrency_limit_of_the_provider_lowers_parallelism(tmp_path):
    """Abnahme KI:connect: „Too many concurrent requests“ (429) bei 4 parallelen Anfragen."""
    project = make_project(tmp_path, run=False)
    _parallel(project, 4)
    lock, state = threading.Lock(), {"active": 0}

    class Limited(FakeProvider):
        def chat(self, request):
            with lock:
                state["active"] += 1
                busy = state["active"]
            try:
                if busy > 2:
                    raise ProviderError("HTTP 429: too many concurrent", retryable=True, status=429)
                time.sleep(0.005)
                return super().chat(request)
            finally:
                with lock:
                    state["active"] -= 1

    Engine(project, provider_factory=lambda cfg: Limited(llm_answer), sleep=lambda s: None).run()
    reduced = [e.details for e in project.store.audit() if e.event == "parallel_reduced"]
    assert reduced and reduced[-1]["parallel"] <= 2
    assert {c.status for c in project.store.calls()} == {"ok"}
    project.close()
