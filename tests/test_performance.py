"""Roadmap 43: Leistung für große Projekte (parallele Anfragen, Store, Tabelle, Bootstrap)."""

import threading
import time

from helpers import FakeProvider, engine
from test_evaluation import llm_answer, make_project

from llmanno.core.engine import fingerprint, results


def _parallel(project, n):
    project.update(
        providers=[
            p.model_copy(update={"parallel": n}) if p.key == "ollama" else p
            for p in project.config.providers
        ]
    )


class SlowProvider(FakeProvider):
    """Antwortet verzögert und zählt, wie viele Anfragen gleichzeitig laufen."""

    def __init__(self):
        super().__init__(llm_answer)
        self.active = self.peak = 0
        self.lock = threading.Lock()

    def chat(self, request):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        time.sleep(0.02)
        try:
            return super().chat(request)
        finally:
            with self.lock:
                self.active -= 1


def _calls(project):
    return sorted((c.item_id, c.run, c.status, str(c.answer)) for c in project.store.calls())


def test_parallel_requests_give_the_same_results(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    sequential = make_project(tmp_path / "a")
    parallel = make_project(tmp_path / "b", run=False)
    _parallel(parallel, 4)
    assert fingerprint(parallel) == fingerprint(sequential)  # gehört nicht zum Instrument
    provider = SlowProvider()
    summary = engine(parallel, provider).run()
    assert provider.peak > 1 and summary.parallel == 4
    assert _calls(parallel) == _calls(sequential)
    ids = [u.unit_id for u in parallel.store.units()]
    assert {i: r.final for i, r in results(parallel, ids).items()} == {
        i: r.final for i, r in results(sequential, ids).items()
    }
    started = next(e for e in parallel.store.audit() if e.event == "run_started")
    assert started.details["parallel"] == 4
    sequential.close()
    parallel.close()


def test_stopping_keeps_finished_requests_without_duplicates(tmp_path):
    project = make_project(tmp_path, run=False)
    _parallel(project, 4)
    count = {"n": 0}

    def stop_soon():
        count["n"] += 1
        return count["n"] > 20

    first = engine(project, SlowProvider()).run(should_stop=stop_soon)
    assert first.stopped
    kept = len(project.store.calls())
    assert kept >= first.calls - 1  # fertige Vorab-Anfragen sind gespeichert
    engine(project, SlowProvider()).run()
    keys = [(c.item_id, c.model_key, c.run, c.config_hash) for c in project.store.calls()]
    assert len(keys) == len(set(keys))
    ids = [u.unit_id for u in project.store.units()]
    assert all(r.status == "ok" for r in results(project, ids).values())
    project.close()


def test_report_names_parallel_requests(tmp_path):
    from llmanno.core.export.report import report_html

    project = make_project(tmp_path, run=False)
    _parallel(project, 3)
    engine(project, FakeProvider(llm_answer)).run()
    assert "parallele Anfragen: fake 3" in report_html(project, None)
    project.close()


def test_bootstrap_can_be_cancelled():
    import numpy as np
    import pytest

    from llmanno.core.metrics.bootstrap import Cancelled, bootstrap, cancellable

    stop = threading.Event()
    with cancellable(stop):
        assert bootstrap(lambda idx: float(np.mean(idx)), 10, resamples=20).resamples == 20
        stop.set()
        with pytest.raises(Cancelled):
            bootstrap(lambda idx: float(np.mean(idx)), 10, resamples=20)
    assert bootstrap(lambda idx: float(np.mean(idx)), 10, resamples=5).resamples == 5


def test_evaluation_page_cancels(qtbot, tmp_path):
    from llmanno.core.project import Project
    from llmanno.gui.main_window import MainWindow

    project = make_project(tmp_path, coders=("A", "B", "C"))
    folder = project.folder
    project.close()
    window = MainWindow()
    qtbot.addWidget(window)
    window.set_project(Project.open(folder))
    page = window.evaluation_page
    page.resamples.setValue(10000)
    page.compute()
    assert page.cancel_button.isEnabled()
    page.cancel()
    qtbot.waitUntil(lambda: not page.busy, timeout=60000)
    assert page.status.text() == "Berechnung abgebrochen." and page._report is None
    assert not page.error.text() and not page.cancel_button.isEnabled()
    window.set_project(None)


def test_search_waits_for_a_typing_pause(qtbot, tmp_path):
    from llmanno.gui.results_page import ResultsPage

    project = make_project(tmp_path)
    page = ResultsPage()
    qtbot.addWidget(page)
    page.set_project(project)
    page.refresh()
    total = page.table.rowCount()
    page.search.setText("U0")
    assert page.table.rowCount() == total  # noch nicht gefiltert
    qtbot.waitUntil(lambda: page.table.rowCount() == 10, timeout=3000)
    project.close()
