"""Tab „Lauf“: Pilot und Vollauf im Hintergrund, Fortschritt, Hochrechnung, Stopp."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable, Collection
from dataclasses import replace
from pathlib import Path
from urllib.parse import quote, urlparse

from PySide6.QtCore import QSettings, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from llmanno.core.config import ModelConfig
from llmanno.core.engine import (
    PILOT_ITEMS,
    REASONING_LOW,
    Engine,
    FreezeBroken,
    Progress,
    RunAborted,
    RunBudget,
    RunSummary,
    prepare_prompts,
    run_budget,
)
from llmanno.core.power import keep_awake
from llmanno.core.project import Project
from llmanno.core.sensitivity import (
    DEFAULT_LIMIT,
    can_lower_effort,
    reasoning_models,
    run_sensitivity,
    sensitivity_items,
    variants_for,
)
from llmanno.core.steps import coded_items
from llmanno.core.validation import freeze_intact
from llmanno.gui.common import (
    Page,
    Task,
    error_label,
    error_text,
    format_duration,
    hint_label,
    status_text,
)

LOG_LIMIT = 500


MAIN = "Hauptprojekt"  # im Protokoll des Laufs
# Zeitfenster des Kontingents in der Reihenfolge der Anzeige; unbekannte danach
WINDOWS = ("month", "day", "hour", "minute", "requests", "tokens")

# Ein Codesystem im Lauf: Ordner, Name (None: Hauptprojekt), Einheiten (None: alle).
Job = tuple[Path, str | None, Collection[str] | None]


def _labelled(report: Callable[[Progress], None], name: str | None):
    if name is None:
        return report
    return lambda p: report(replace(p, model_key=f"{name} · {p.model_key}"))


def _merge(total: RunSummary, part: RunSummary) -> None:
    total.calls, total.ok = total.calls + part.calls, total.ok + part.ok
    total.invalid, total.errors = total.invalid + part.invalid, total.errors + part.errors
    total.repaired += part.repaired
    total.stopped = total.stopped or part.stopped
    total.parallel = part.parallel
    total.latencies += part.latencies
    total.calls_per_item += part.calls_per_item
    total.notes += part.notes
    total.tokens_in += part.tokens_in
    total.tokens_out += part.tokens_out
    for key in ("tokens_cached", "tokens_reasoning"):
        if (value := getattr(part, key)) is not None:
            setattr(total, key, (getattr(total, key) or 0) + value)
    total.quota.update(part.quota)


def _run_job(
    jobs: list[Job],
    item_limit: int | None,
    consent: bool,
    stop: threading.Event,
    report: Callable[[Progress], None],
) -> RunSummary:
    """Läuft im Worker-Thread mit eigener Store-Verbindung (SQLite-Verbindungen sind
    an ihren Thread gebunden); die Codesysteme nacheinander."""
    total = RunSummary()
    with keep_awake():
        for folder, name, item_ids in jobs:
            if stop.is_set():
                total.stopped = True
                break
            project = Project.open(folder)
            try:
                summary = Engine(project, sleep=stop.wait).run(
                    item_limit=item_limit,
                    item_ids=item_ids,
                    remote_consent=consent,
                    should_stop=stop.is_set,
                    on_progress=_labelled(report, name),
                )
            except (FreezeBroken, ValueError, PermissionError) as exc:
                if len(jobs) == 1:
                    raise
                # Betrifft nur dieses Codesystem (etwa ein gebrochener Freeze): weiter.
                total.notes.append(f"{name or MAIN}: {exc}")
                continue
            finally:
                project.close()
            _merge(total, summary)
    return total


def _sensitivity_job(
    jobs: list[Job],
    consent: bool,
    stop: threading.Event,
    report: Callable[[Progress], None],
) -> RunSummary:
    total = RunSummary()
    with keep_awake():
        for folder, name, item_ids in jobs:
            if stop.is_set():
                total.stopped = True
                break
            project = Project.open(folder)
            try:
                done = run_sensitivity(
                    project, item_ids or [], remote_consent=consent, should_stop=stop.is_set,
                    on_progress=_labelled(report, name), sleep=stop.wait,
                )  # fmt: skip
            finally:
                project.close()
            for _, s in done:
                _merge(total, s)
    return total


def _total(budgets: list[RunBudget]) -> RunBudget:
    """Ein Budget für alle Codesysteme: Aufrufe und Token addiert."""
    seen = [b.tokens_out_seen for b in budgets]
    providers: dict[str, tuple[int, int]] = {}
    for b in budgets:
        for key, (low, high) in b.per_provider.items():
            was = providers.get(key, (0, 0))
            providers[key] = (was[0] + low, was[1] + high)
    return replace(
        budgets[0],
        calls=sum(b.calls for b in budgets),
        tokens_in=sum(b.tokens_in for b in budgets),
        tokens_out=sum(b.tokens_out for b in budgets),
        calls_min=sum(b.calls_min for b in budgets),
        tokens_in_min=sum(b.tokens_in_min for b in budgets),
        tokens_out_seen=(
            (sum(s[0] for s in seen if s), sum(s[1] for s in seen if s)) if all(seen) else None
        ),
        per_provider=providers,
    )


def _sensitivity_budgets(
    project: Project, item_ids: set[str], models: list[ModelConfig]
) -> list[RunBudget]:
    """Je Variante ein Lauf. „Reasoning niedrig“ läuft nur für denkende Modelle; ob ein Modell
    denkt, zeigt sich womöglich erst im Original, darum zählt es dort nur zur Obergrenze."""
    variants = variants_for(project.config)
    low = REASONING_LOW in variants
    budgets = [
        run_budget(project, item_ids=item_ids, models=models, runs=len(variants) - low,
                   adaptive=False, resume=False)
    ]  # fmt: skip
    if not low:
        return budgets
    known = set(reasoning_models(project))
    for sure in (True, False):
        chosen = [m for m in models
                  if can_lower_effort(m, project.config) and (m.key in known) == sure]  # fmt: skip
        if chosen:
            budget = run_budget(project, item_ids=item_ids, models=chosen, runs=1,
                                adaptive=False, resume=False)  # fmt: skip
            budgets.append(budget if sure else _upper_only(budget))
    return budgets


def _upper_only(budget: RunBudget) -> RunBudget:
    seen = budget.tokens_out_seen
    return replace(
        budget,
        calls_min=0,
        tokens_in_min=0,
        tokens_out_seen=None if seen is None else (0, seen[1]),
        per_provider={key: (0, high) for key, (_, high) in budget.per_provider.items()},
    )


def _quota_key(base_url: str) -> str:
    return "quota/" + quote(base_url, safe="")


def save_quota(base_url: str, windows: dict[str, tuple[int, int | None]]) -> None:
    """Letzte Kontingent-Meldung eines Anbieters, für die Warnung vor dem nächsten Lauf."""
    value = json.dumps({"at": time.time(), "windows": windows})
    QSettings().setValue(_quota_key(base_url), value)


def load_quota(base_url: str) -> tuple[float, dict[str, tuple[int, int | None]]] | None:
    try:
        data = json.loads(str(QSettings().value(_quota_key(base_url), "")))
        windows = {
            str(w): (int(v[0]), None if v[1] is None else int(v[1]))
            for w, v in data["windows"].items()
        }
        return float(data["at"]), windows
    except (TypeError, ValueError, KeyError, IndexError, AttributeError):
        return None  # fehlt oder von Hand verändert: keine Warnung statt eines Absturzes


def quota_current(at: float, window: str) -> bool:
    """Gilt eine Meldung noch? Tag und Monat als Kalender-Fenster (UTC), wie bei GWDG."""
    then, now = time.gmtime(at), time.gmtime()
    size = {"month": 2, "day": 3}.get(window)
    return size is not None and then[:size] == now[:size]


class RunPage(Page):
    help_topics = (
        "7. Lauf",
        "Prompt-Sensitivität",
        "Kosten und Kontingent",
        "Fehlerbehebung",
    )

    running_changed = Signal(bool)
    results_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._task: Task | None = None
        self._stop = threading.Event()
        self._started = 0.0
        self._pilot = False
        self._counts: dict[str, int] = {}
        self._n_items = 0
        self._n_models = 0
        self._split_state: tuple[bool, bool] | None = None
        # Weitere Codesysteme (vom Hauptfenster gesetzt): die geöffneten, und vor einem Lauf
        # abgeglichen und gespeichert.
        self.codesystems: Callable[[], list[Project]] = lambda: []
        self.prepare_codesystems: Callable[[], list[Project]] = lambda: []
        self._job: object = None
        self._job_started = 0.0
        self._remaining_pairs = 0

        self.pilot_button = QPushButton(self.tr("Pilot ({0} Einheiten)").format(PILOT_ITEMS))
        self.pilot_button.setToolTip(
            self.tr("Kodiert die ersten {0} Einheiten mit allen Durchläufen und Modellen: um "
                    "Modell und Prompt zu testen und die Dauer des Vollaufs hochzurechnen.")
            .format(PILOT_ITEMS)
        )  # fmt: skip
        self.pilot_button.clicked.connect(lambda: self.start(pilot=True))
        self.full_button = QPushButton()
        self.full_button.clicked.connect(lambda: self.start(pilot=False))
        self.stop_button = QPushButton(self.tr("Anhalten"))
        self.stop_button.setToolTip(
            self.tr("Hält nach dem laufenden Aufruf an; nichts geht verloren, ein neuer Start "
                    "setzt fort.")
        )  # fmt: skip
        self.stop_button.clicked.connect(self.request_stop)
        self.stop_button.setEnabled(False)
        buttons = QHBoxLayout()
        for button in (self.pilot_button, self.full_button, self.stop_button):
            buttons.addWidget(button)
        buttons.addStretch()
        self.sensitivity_button = QPushButton(self.tr("Prompt-Sensitivität prüfen"))
        self.sensitivity_button.setToolTip(
            self.tr("Kodiert den Übungsteil (ohne Aufteilung alle Einheiten) mit drei "
                    "Umformulierungen der Anweisungen, im Kontextmodus auch ohne Kontext und bei "
                    "Modellen, die vor der Antwort denken, mit niedrigem Reasoning-Aufwand. Am "
                    "besten vor dem Einfrieren prüfen.")
        )  # fmt: skip
        self.sensitivity_button.clicked.connect(self.start_sensitivity)
        self.sensitivity_limit = QSpinBox()
        self.sensitivity_limit.setRange(10, 1000)
        self.sensitivity_limit.setValue(DEFAULT_LIMIT)
        self.sensitivity_limit.setPrefix(self.tr("höchstens "))
        self.sensitivity_limit.setSuffix(self.tr(" Einheiten"))
        self.sensitivity_limit.setToolTip(
            self.tr("Bei mehr Einheiten zieht das Programm eine feste Zufallsstichprobe. Mehr "
                    "Einheiten machen das Ergebnis genauer und kosten mehr Aufrufe.")
        )  # fmt: skip
        sensitivity = QHBoxLayout()
        sensitivity.addWidget(self.sensitivity_button)
        sensitivity.addWidget(self.sensitivity_limit)
        sensitivity.addStretch()
        self.dev_only = QCheckBox(self.tr("nur Übungsteil (Dev) kodieren"))
        self.dev_only.toggled.connect(self._show_full_button)
        self._show_full_button()

        self.phase = QLabel()
        self.phase.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setFormat(self.tr("%v / %m Aufrufe (höchstens)"))
        self.progress.setTextVisible(False)  # erst mit dem ersten Lauf aussagekräftig
        self.state = QLabel()
        self.counts = hint_label()
        self.counts.setToolTip(
            self.tr("gültig: Antwort im erwarteten Format. ungültig: Antwort auch nach Nachfrage "
                    "unbrauchbar; zählt als Unsicherheit. Fehler: keine Antwort (Netz, Anbieter); "
                    "ein neuer Start wiederholt sie.")
        )  # fmt: skip
        self.eta = QLabel()
        self.error = error_label()
        self.log = QListWidget()

        layout = QVBoxLayout(self)
        layout.addWidget(
            hint_label(
                self.tr(
                    "Erst den Pilot, dann den Vollauf. Ein Vollauf überspringt alles, was schon "
                    "kodiert ist; Anhalten verliert nichts, ein neuer Start setzt fort. Der "
                    "Rechner bleibt während des Laufs wach."
                )
            )
        )
        layout.addWidget(self.phase)
        layout.addLayout(buttons)
        layout.addWidget(self.dev_only)
        layout.addWidget(
            hint_label(
                self.tr(
                    "Prompt-Sensitivität: dieselben Einheiten mit umformulierten Anweisungen "
                    "kodieren, je Variante ein Lauf; die Evaluation zeigt dann, ob die Codes vom "
                    "Wortlaut abhängen. Ergebnisse und Freeze bleiben unberührt."
                )
            )
        )
        layout.addLayout(sensitivity)
        layout.addWidget(self.progress)
        layout.addWidget(self.state)
        layout.addWidget(self.counts)
        layout.addWidget(self.eta)
        layout.addWidget(self.error)
        layout.addWidget(QLabel(self.tr("Protokoll")))
        layout.addWidget(self.log, 1)

    @property
    def running(self) -> bool:
        return self._task is not None and self._task.running

    def refresh(self) -> None:
        idle = not self.running and self.project is not None
        self.pilot_button.setEnabled(idle)
        self.full_button.setEnabled(idle)
        self.sensitivity_button.setEnabled(idle)
        if self.project is None:
            return
        has_split = bool(self.project.store.split()) or any(
            p.store.split() for p in self.codesystems()
        )
        state = (has_split, self.project.config.freeze is not None)
        if state != self._split_state:
            # Vorgabe nur bei geändertem Split- oder Freeze-Stand, sonst bleibt die Wahl.
            self._split_state = state
            self.dev_only.setChecked(has_split and not state[1])
        self.dev_only.setEnabled(idle and has_split)
        self.dev_only.setToolTip(
            self.tr("Vor dem Einfrieren reicht der Übungsteil zum Schärfen; der Prüfteil (Test) "
                    "würde nach jeder Änderung am Prompt ohnehin neu kodiert.")
            if has_split
            else self.tr("Erst im Tab Splits & Freeze eine Aufteilung (Split) erzeugen.")
        )  # fmt: skip
        self._show_full_button()
        if idle:
            self._show_phase()

    def _show_full_button(self) -> None:
        """Beschriftung nach dem, was der Knopf kodiert: nur Dev oder alles."""
        has_split = bool(self._split_state and self._split_state[0])
        if has_split and self.dev_only.isChecked():
            self.full_button.setText(self.tr("Übungsteil (Dev) kodieren / fortsetzen"))
            tip = self.tr("Kodiert alle Einheiten des Übungsteils mit allen Durchläufen und "
                          "Modellen; schon Kodiertes wird übersprungen.")  # fmt: skip
        else:
            self.full_button.setText(self.tr("Vollauf starten / fortsetzen"))
            tip = self.tr("Kodiert alle Einheiten mit allen Durchläufen und Modellen; schon "
                          "Kodiertes wird übersprungen.")  # fmt: skip
            if not has_split:
                tip += " " + self.tr("Ohne Aufteilung gehören dazu auch die Einheiten, die "
                                     "später den Prüfteil (Test) bilden.")  # fmt: skip
        self.full_button.setToolTip(tip)

    def _show_phase(self) -> None:
        """Wo das Projekt steht: Aufteilung, Freeze und wie viel schon kodiert ist."""
        project = self.project
        assert project is not None
        parts = project.store.split()
        intact = freeze_intact(project)
        if intact is False:
            phase = self.tr(
                "Freeze gebrochen: Läufe sind gesperrt, bis die Änderung "
                "zurückgenommen oder der Freeze aufgehoben ist (Tab Splits & Freeze)."
            )
        elif intact:
            phase = self.tr(
                "Eingefroren: jetzt alle Einheiten kodieren und den Prüfteil (Test) "
                "einmal auswerten."
            )
        elif parts:
            phase = self.tr(
                "Übungsphase: Codebuch und Prompt am Übungsteil (Dev) schärfen, "
                "danach im Tab Splits & Freeze einfrieren."
            )
        else:
            phase = self.tr(
                "Noch keine Aufteilung (Split): Ein Lauf kodiert alle Einheiten, "
                "auch die späteren Prüfteil-Einheiten (Test). Wer Codebuch oder "
                "Prompt noch schärft, teilt vorher im Tab Splits & Freeze auf."
            )
        try:
            _, items = prepare_prompts(project)
        except ValueError:  # noch keine Daten oder Codes: die Fehlermeldung kommt beim Start
            self.phase.setText(phase)
            return
        ids = [i.item_id for i in items]
        ok = coded_items(project)[0] & set(ids)  # günstig: ohne alle Ergebnisse zu aggregieren
        line = self.tr("Kodiert (von allen Modellen beantwortet): {0} von {1} Einheiten").format(
            len(ok), len(ids)
        )
        if parts:
            dev = {i for i, part in parts.items() if part == "dev"}
            test = set(parts) - dev
            line += self.tr(" · Übungsteil {0} von {1} · Prüfteil {2} von {3}").format(
                len(ok & dev), len(dev), len(ok & test), len(test)
            )
        self.phase.setText(phase + "\n" + line)

    # --- Start ---------------------------------------------------------------------------------

    def start(self, *, pilot: bool) -> None:
        if self.project is None or self.running:
            return
        self.error.clear()
        if self.project.dirty:
            # Der Lauf liest project.json; ein ungespeicherter Stand wäre nicht nachvollziehbar.
            self.project.save()
            self.changed.emit()
        dev = self.dev_only.isEnabled() and self.dev_only.isChecked()
        limit = PILOT_ITEMS if pilot else None
        try:
            ready, skipped = self._ready_systems()
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.error.setText(error_text(exc))
            return
        systems = [(name, project) for name, project, _ in ready]
        engines = [engine for _, _, engine in ready]
        jobs: list[Job] = []
        sizes = []
        for name, project, engine in ready:
            parts = project.store.split()
            # Eingefrorene weitere Codesysteme brauchen auch ihre Test-Einheiten.
            own = dev and bool(parts) and (name is None or project.config.freeze is None)
            item_ids = {i for i, part in parts.items() if part == "dev"} if own else None
            jobs.append((project.folder, name, item_ids))
            sizes.append(len(engine.items) if item_ids is None else len(item_ids))
        item_ids = jobs[0][2]
        self._n_items = sizes[0]
        self._n_models = len(self.project.config.panel)
        self._remaining_pairs = sum(max(n - PILOT_ITEMS, 0) for n in sizes) * self._n_models
        consent = False
        if remote := engines[0].remote_providers():
            models = [m for m in self.project.config.panel
                      if m.provider in {p.key for p in remote}]  # fmt: skip
            budgets = [
                run_budget(project, item_ids=ids, item_limit=limit, models=models)
                for (_, project), (_, _, ids) in zip(systems, jobs, strict=True)
            ]
            n_items = sum(min(limit or n, n) for n in sizes)
            consent = self._ask_consent(remote, n_items, _total(budgets))
            if not consent:
                return

        self._pilot = pilot
        self._stop.clear()
        self._counts = {"ok": 0, "invalid": 0, "error": 0}
        self._started = self._job_started = time.monotonic()
        self._job = None
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.eta.clear()
        self._append(
            self.tr("Start: {0}{1}").format(
                self.tr("Pilot") if pilot else self.tr("Vollauf"),
                self.tr(" (nur Dev)") if item_ids is not None else "",
            )
            + (self.tr(", {0} Codesysteme nacheinander").format(len(jobs)) if len(jobs) > 1 else "")
        )
        for line in skipped:
            self._append(line)
        stop = self._stop
        self._task = Task(self, lambda report: _run_job(jobs, limit, consent, stop, report))
        self._task.progress.connect(self._on_progress)
        self._task.done.connect(self._on_done)
        self._task.failed.connect(self._on_failed)
        self._task.finished.connect(self._on_finished)
        # Erst starten: _set_running sperrt Tabs, der Tab-Wechsel ruft refresh(), und das sähe
        # vor dem Start keinen Lauf und gäbe die Start-Knöpfe wieder frei.
        self._task.start()
        self._set_running(True)

    def start_sensitivity(self) -> None:
        if self.project is None or self.running:
            return
        self.error.clear()
        if self.project.dirty:
            self.project.save()
            self.changed.emit()
        try:
            ready, skipped = self._ready_systems()
            systems = [(name, project) for name, project, _ in ready]
            engines = [engine for _, _, engine in ready]
            jobs: list[Job] = [
                (project.folder, name, sensitivity_items(project, self.sensitivity_limit.value()))
                for name, project in systems
            ]
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.error.setText(error_text(exc))
            return
        for line in skipped:
            self._append(line)
        item_ids = list(jobs[0][2] or [])
        consent = False
        if remote := engines[0].remote_providers():
            models = [m for m in self.project.config.panel
                      if m.provider in {p.key for p in remote}]  # fmt: skip
            budgets = [
                budget
                for (_, project), (_, _, ids) in zip(systems, jobs, strict=True)
                for budget in _sensitivity_budgets(project, set(ids or []), models)
            ]
            consent = self._ask_consent(
                remote, sum(len(ids or []) for _, _, ids in jobs), _total(budgets)
            )
            if not consent:
                return
        variants = len(variants_for(self.project.config))
        self._pilot = False
        self._n_items, self._n_models = len(item_ids), len(self.project.config.panel)
        self._stop.clear()
        self._counts = {"ok": 0, "invalid": 0, "error": 0}
        self._started = self._job_started = time.monotonic()
        self._job = None
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.eta.clear()
        self._append(
            self.tr("Start: Prompt-Sensitivität, {0} Einheiten × {1} Varianten").format(
                len(item_ids), variants
            )
        )
        stop = self._stop
        self._task = Task(self, lambda report: _sensitivity_job(jobs, consent, stop, report))
        self._task.progress.connect(self._on_progress)
        self._task.done.connect(self._on_done)
        self._task.failed.connect(self._on_failed)
        self._task.finished.connect(self._on_finished)
        self._task.start()
        self._set_running(True)

    def _ready_systems(self) -> tuple[list[tuple[str | None, Project, Engine]], list[str]]:
        """Hauptprojekt und weitere Codesysteme, in dieser Reihenfolge kodiert. Ein
        unfertiges weiteres Codesystem (etwa ohne aktive Codes) wird übersprungen."""
        assert self.project is not None
        systems = [(None, self.project), *((p.config.name, p) for p in self.prepare_codesystems())]
        ready, skipped = [], []
        for name, project in systems:
            try:
                ready.append((name, project, Engine(project)))
            except ValueError as exc:
                if name is None:
                    raise
                skipped.append(
                    self.tr("Codesystem „{0}“ übersprungen: {1}").format(name, error_text(exc))
                )
        return ready, skipped

    def note(self, text: str) -> None:
        """Hinweis ins Protokoll des Lauf-Tabs."""
        self._append(text)

    def _names_note(self) -> str:
        n = len(self.project.pseudonym_pairs) if self.project else 0
        if self.project and self.project.config.coding.pseudonyms_sha256 and n:
            return self.tr(
                "Namen: {0} Einträge der Namensliste werden vor dem Senden ersetzt."
            ).format(n)
        return self.tr("Namen: werden nicht ersetzt (Tab Prompt & Einheiten → Namen ersetzen…).")

    def _ask_consent(self, remote, n_items: int, budget: RunBudget | None = None) -> bool:
        levels = {
            "local": self.tr("lokal"),
            "academic": self.tr("akademisch / EU"),
            "external": self.tr("extern"),
        }
        lines = "\n".join(
            f"• {p.name} ({urlparse(p.base_url).netloc or p.base_url}, {levels[p.privacy_level]}"
            + (self.tr(", API-Schlüssel unverschlüsselt über http") if p.key_in_clear else "")
            + ")"
            for p in remote
        )
        answer = QMessageBox.question(
            self,
            self.tr("Daten an externe Anbieter senden?"),
            self.tr(
                "Für diesen Lauf gehen die Texte von bis zu {0} Einheiten samt Codebuch an:\n\n"
                "{1}\n\nBitte vorher prüfen, ob die Daten pseudonymisiert sind und ob der Anbieter "
                "für sie zulässig ist. Die Zustimmung wird im Projektprotokoll vermerkt."
            ).format(n_items, lines)
            + "\n\n"
            + self._names_note()
            + ("\n\n" + self._budget_text(budget, remote) if budget else ""),
        )
        return answer == QMessageBox.StandardButton.Yes

    def _budget_text(self, budget: RunBudget, remote) -> str:
        lines = [
            self.tr(
                "Geschätzt: {0} bis {1} Aufrufe und etwa {2} bis {3} Token an die Anbieter. Token "
                "sind Textbausteine (meist Wortteile), nach denen Anbieter abrechnen. Wie viele "
                "Aufrufe es werden, hängt vom adaptiven Stopp ab (er beendet die Durchläufe einer "
                "Einheit, sobald sie übereinstimmen); Nachfragen bei unbrauchbaren Antworten "
                "(Repairs) kommen hinzu."
            ).format(budget.calls_min, budget.calls, budget.tokens_in_min, budget.tokens_in)
        ]
        if budget.tokens_out_seen:
            lines.append(
                self.tr("Zurück kommen nach den bisherigen Antworten etwa {0} bis {1} Token.")
                .format(*budget.tokens_out_seen)
            )  # fmt: skip
        else:
            lines.append(self.tr("Zurück kommen höchstens {0} Token.").format(budget.tokens_out))
        for provider in remote:
            if (known := load_quota(provider.base_url)) is None:
                continue
            at, windows = known
            lines.append(
                self.tr("Kontingent (erlaubte Anfragen je Zeitraum) bei {0} laut letzter Meldung "
                        "({1}): noch {2}.").format(
                    provider.name, time.strftime("%d.%m. %H:%M", time.localtime(at)),
                    self._windows_text(windows),
                )
            )  # fmt: skip
            need = budget.per_provider.get(provider.key, (0, 0))[0]
            for window in ("day", "month"):
                left = windows.get(window, (None, None))[0]
                if left is not None and need > left and quota_current(at, window):
                    lines.append(
                        self.tr(
                            "Achtung: Schon im günstigsten Fall braucht der Lauf {0} Anfragen "
                            "an {1}, frei sind noch {2} {3}. Ist das Kontingent erschöpft, "
                            "bricht der Lauf ab und lässt sich später fortsetzen."
                        ).format(need, provider.name, left, self._window_name(window))
                    )
                    break
        return "\n".join(lines)

    def _window_name(self, window: str) -> str:
        names = {
            "month": self.tr("pro Monat"),
            "day": self.tr("pro Tag"),
            "hour": self.tr("pro Stunde"),
            "minute": self.tr("pro Minute"),
            "requests": self.tr("Anfragen pro Minute"),
            "tokens": self.tr("Token pro Minute"),
        }
        return names.get(window, window)

    def _windows_text(self, windows: dict[str, tuple[int, int | None]]) -> str:
        rank = {w: i for i, w in enumerate(WINDOWS)}
        parts = []
        for window in sorted(windows, key=lambda w: rank.get(w, len(WINDOWS))):
            left, limit = windows[window]
            amount = str(left) if limit is None else self.tr("{0} von {1}").format(left, limit)
            parts.append(f"{amount} {self._window_name(window)}")
        return ", ".join(parts)

    def _usage_text(self, summary: RunSummary) -> str:
        text = self.tr("Verbrauch laut Anbieter: {0} Token Eingabe, {1} Ausgabe").format(
            summary.tokens_in, summary.tokens_out
        )
        if summary.tokens_reasoning is not None:
            text += self.tr(" (davon {0} Reasoning)").format(summary.tokens_reasoning)
        if summary.tokens_cached is not None:
            text += self.tr("; {0} Token der Eingabe aus dem Cache").format(summary.tokens_cached)
        if summary.calls_per_item:
            mean = sum(summary.calls_per_item) / len(summary.calls_per_item)
            text += self.tr("; im Mittel {0:.1f} Aufrufe je Einheit und Modell").format(mean)
        return text

    def request_stop(self) -> None:
        if self.running:
            self._stop.set()
            self.stop_button.setEnabled(False)
            self.state.setText(self.tr("Wird nach dem laufenden Aufruf angehalten …"))

    def stop_and_wait(self) -> None:
        if self._task is not None:
            self._stop.set()
            self._task.wait()

    # --- Rückmeldungen -------------------------------------------------------------------------

    def _on_progress(self, progress: Progress) -> None:
        self._counts[progress.status] = self._counts.get(progress.status, 0) + 1
        job = progress.model_key.split(" · ")[0] if " · " in progress.model_key else None
        if job != self._job:  # nächstes Codesystem: Balken und Restzeit beginnen neu
            self._job, self._job_started = job, time.monotonic()
        self.progress.setMaximum(progress.planned)
        self.progress.setValue(progress.done)
        elapsed = time.monotonic() - self._started
        self.state.setText(
            self.tr("{0} · Einheit {1} · Durchlauf {2} · verstrichen {3}").format(
                progress.model_key, progress.item_id, progress.run + 1, format_duration(elapsed)
            )
        )
        self.counts.setText(
            self.tr("gültig {0} · ungültig {1} · Fehler {2}").format(
                self._counts.get("ok", 0),
                self._counts.get("invalid", 0),
                self._counts.get("error", 0),
            )
        )
        if progress.done:
            spent = time.monotonic() - self._job_started
            remaining = spent / progress.done * (progress.planned - progress.done)
            self.eta.setText(
                self.tr(
                    "Restzeit höchstens {0}; der adaptive Stopp macht sie meist kürzer."
                ).format(format_duration(remaining))
            )
        if progress.status != "ok":
            line = self.tr("{0}: Einheit {1}, Durchlauf {2}").format(
                status_text(progress.status), progress.item_id, progress.run + 1
            )
            if progress.detail:
                line += f" · {progress.detail[:160]}"
            self._append(line)

    def _on_done(self, summary: RunSummary) -> None:
        for note in summary.notes:
            self._append(note)
        if summary.notes:
            self.error.setText("\n".join(summary.notes))
        verb = self.tr("Angehalten") if summary.stopped else self.tr("Fertig")
        self._append(
            self.tr("{0}: {1} Aufrufe, {2} gültig, {3} ungültig, {4} Fehler, {5} repariert").format(
                verb, summary.calls, summary.ok, summary.invalid, summary.errors, summary.repaired
            )
        )
        self._report_usage(summary)
        if not summary.stopped:  # übersprungene Varianten ließen den Balken sonst stehen
            self.progress.setValue(self.progress.maximum())
        if self._pilot and not summary.stopped:
            estimate = summary.eta_s(self._remaining_pairs)
            self.eta.setText(
                self.tr(
                    "Hochrechnung für den Rest des Vollaufs: etwa {0} (mittlere Antwortzeit "
                    "{1:.1f} s)"
                ).format(format_duration(estimate), summary.mean_latency_s or 0)
                if estimate is not None
                else self.tr("Alles bereits kodiert; keine Hochrechnung nötig.")
            )
        elif not summary.stopped:
            self.eta.setText(
                self.tr("Lauf abgeschlossen in {0}.").format(
                    format_duration(time.monotonic() - self._started)
                )
            )
        if summary.errors:
            self._append(
                self.tr("{0} Aufrufe mit Fehler; ein neuer Start wiederholt sie, gültige und "
                        "ungültige Antworten bleiben.").format(summary.errors)
            )  # fmt: skip

    def _on_failed(self, exc: BaseException) -> None:
        self.error.setText(error_text(exc))
        self._append(self.tr("Abgebrochen: {0}").format(error_text(exc)))
        if isinstance(exc, RunAborted) and exc.summary is not None:
            self._report_usage(exc.summary)  # etwa Kontingent erschöpft: Stand merken

    def _report_usage(self, summary: RunSummary) -> None:
        """Verbrauch und Kontingent ins Protokoll; das Kontingent für den nächsten Start."""
        if summary.tokens_in or summary.tokens_out:
            self._append(self._usage_text(summary))
        for key, windows in summary.quota.items():
            provider = next((p for p in self.project.config.providers if p.key == key), None)
            if provider is None:
                continue
            save_quota(provider.base_url, windows)
            self._append(
                self.tr("Kontingent bei {0}: noch {1}.").format(
                    provider.name, self._windows_text(windows)
                )
            )

    def _on_finished(self) -> None:
        self._set_running(False)
        if self.project is not None:
            self._show_phase()
        self.results_changed.emit()

    def _set_running(self, running: bool) -> None:
        self.stop_button.setEnabled(running)
        self.pilot_button.setEnabled(not running)
        self.full_button.setEnabled(not running)
        self.sensitivity_button.setEnabled(not running)
        self.dev_only.setEnabled(not running and bool(self._split_state and self._split_state[0]))
        self.running_changed.emit(running)

    def _append(self, text: str) -> None:
        self.log.addItem(f"{time.strftime('%H:%M:%S')}  {text}")
        while self.log.count() > LOG_LIMIT:
            self.log.takeItem(0)
        self.log.scrollToBottom()
