"""Kodierlauf: Modell → Einheit → Lauf, mit adaptivem N, Repair, Fortsetzen und Audit.

Die Reihenfolge Modell → Einheit hält bei wenig VRAM immer nur ein Modell geladen.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from collections import Counter
from collections.abc import Callable, Collection
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from typing import Any

from llmanno.core.aggregate import ItemResult, aggregate, decision
from llmanno.core.config import (
    CodebookConfig,
    CodingConfig,
    ModelConfig,
    ProviderConfig,
    ReviewConfig,
    UnitSettings,
)
from llmanno.core.evidence import locate
from llmanno.core.i18n import Message
from llmanno.core.project import Project
from llmanno.core.prompt import (
    TEMPLATE_VERSION,
    PromptSpec,
    estimate_tokens,
    render,
    render_system,
    render_user,
    repair_message,
)
from llmanno.core.providers import make_provider
from llmanno.core.providers.base import (
    ChatRequest,
    ChatResponse,
    ModelInfo,
    Provider,
    ProviderError,
)
from llmanno.core.schema import AnswerError, Parsed, parse_answer, response_schema
from llmanno.core.store import SENSITIVITY_STARTED, CallRecord, sha256_text
from llmanno.core.units import Item, build_items, is_target

RETRY_DELAYS_S = (2, 5, 15, 30)
MAX_RETRY_AFTER_S = 300  # verlangt der Anbieter länger Pause, ist meist das Kontingent leer
REPAIR_OVERHEAD_TOKENS = 200  # Repair-Nachricht; die vorige Antwort zählt über max_tokens
MAX_CONSECUTIVE_ERRORS = 5
# Netzausfall: nach 5 Verbindungsfehlern in Folge warten statt abbrechen, bis zu 30 min.
OUTAGE_WAITS_S = (60, 120, 300, 600)
MAX_OUTAGE_S = 1800
# Hält das Modell das Ausgabeformat systematisch nicht ein, kostet jede Einheit
# n_runs × 2 Aufrufe ohne Ergebnis: nach so vielen Einheiten in Folge ohne gültige Antwort
# abbrechen. Gezählt je Einheit, damit eine einzelne heikle Einheit den Lauf nicht beendet.
MAX_INVALID_UNITS = 5
PILOT_ITEMS = 20


class RunAborted(Exception):
    """Systematisches Problem, bei dem Weiterlaufen nur Fehler produzieren würde."""

    summary: RunSummary | None = None  # was bis zum Abbruch lief (Verbrauch, Kontingent)


class _Stopped(Exception):
    """Anhalten während einer Wartezeit; der angefangene Aufruf wird nicht protokolliert."""


class FreezeBroken(RunAborted):
    """Das eingefrorene Instrument wurde verändert; erst nach Aufheben des Freeze weiter."""


@dataclass
class PreparedModel:
    config: ModelConfig
    provider_config: ProviderConfig
    provider: Provider
    info: ModelInfo | None
    num_ctx: int | None
    config_hash: str
    temperature: float | None
    server_version: str | None = None  # Ollama-Version


# Messbedingungen aus Ollamas Modelldetails, die in Protokoll und Report gehören.
MODEL_DETAILS = ("family", "parameter_size", "quantization_level", "format")


def _model_details(info: ModelInfo | None) -> dict[str, str]:
    if info is None:
        return {}
    return {k: str(info.details[k]) for k in MODEL_DETAILS if info.details.get(k)}


@dataclass(frozen=True)
class Progress:
    model_key: str
    item_id: str
    run: int
    status: str
    done: int  # in diesem Aufruf von run() ausgeführte Modellaufrufe
    planned: int  # offene Aufrufe beim Start, obere Schranke (ohne adaptiven Stopp)
    detail: str | None = None  # Fehlergrund bei ungültig oder Fehler


@dataclass(frozen=True)
class _Outcome:
    """Ein Aufruf, noch nicht gespeichert: Der Store gehört dem Thread, der den Lauf führt."""

    record: CallRecord
    texts: tuple[str, str]  # System- und User-Nachricht
    retryable: bool  # Fehler war vorübergehend (Netz, 5xx, 429)


@dataclass
class RunSummary:
    calls: int = 0
    ok: int = 0
    invalid: int = 0
    errors: int = 0
    repaired: int = 0
    stopped: bool = False
    parallel: int = 1  # gleichzeitige Anfragen (kleinster Wert im Panel)
    latencies: list[float] = field(default_factory=list)
    calls_per_item: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # Hinweise, etwa übersprungene Codesysteme
    # Verbrauch laut Anbieter, Repairs eingeschlossen; None: vom Anbieter nicht gemeldet
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cached: int | None = None
    tokens_reasoning: int | None = None
    # Restkontingent je Anbieter (Schlüssel) aus der letzten Antwort, siehe providers.quota
    quota: dict[str, dict[str, tuple[int, int | None]]] = field(default_factory=dict)

    @property
    def mean_latency_s(self) -> float | None:
        return sum(self.latencies) / len(self.latencies) if self.latencies else None

    def eta_s(self, remaining_pairs: int) -> float | None:
        """Hochrechnung für verbleibende (Einheit, Modell)-Paare aus gemessener Latenz und den
        tatsächlich nötigen Läufen pro Paar (adaptives N)."""
        latency = self.mean_latency_s
        if latency is None or not self.calls_per_item:
            return None
        per_item = sum(self.calls_per_item) / len(self.calls_per_item)
        return remaining_pairs * per_item * latency / self.parallel


ProviderFactory = Callable[[ProviderConfig], Provider]


# Später ergänzte Felder nur aufnehmen, wenn sie gesetzt sind: sonst verschöben sich
# bestehende Lauf-Identitäten und Freezes.
_LATER_FIELDS = {"provider_temperature": False, "reasoning_effort": None}


# Sensitivitätsläufe (core.sensitivity): Variante "isoliert" kodiert ohne Kontext.
ISOLATED = "isoliert"
# Variante mit dem Original-Prompt, aber Reasoning-Aufwand „low“ (core.sensitivity): Liefern
# weniger Denk-Token dieselben Codes? Nur für die Modelle, die der Aufrufer nennt.
REASONING_LOW = "reasoning_niedrig"


def _model_dump(model: ModelConfig, exclude: Collection[str] = ()) -> dict:
    dump = model.model_dump(mode="json", exclude=set(exclude))
    for name, default in _LATER_FIELDS.items():
        if dump.get(name, default) == default:
            dump.pop(name, None)
    return dump


def _coding_dump(coding: CodingConfig, **kwargs: Any) -> dict:
    """Wie _LATER_FIELDS: später ergänzte Felder nur, wenn sie gesetzt sind."""
    dump = coding.model_dump(mode="json", **kwargs)
    if dump.get("units", {}).get("target_column") is None:
        dump.get("units", {}).pop("target_column", None)
    if dump.get("pseudonyms_sha256", 0) is None:
        del dump["pseudonyms_sha256"]
    if dump.get("examples") == []:
        del dump["examples"]
    return dump


def _review_dump(review: ReviewConfig) -> dict:
    """Wie _LATER_FIELDS: die Abbildung der rekalibrierten Schwelle nur, wenn gesetzt."""
    dump = review.model_dump(mode="json")
    if dump["calibration"] is None:
        del dump["calibration"]
    return dump


def _targets_sha(units: list, column: str) -> str:
    """Welche Zeilen kodiert werden: Die Werte der Zielspalte stehen nicht in der
    Einheiten-Prüfsumme (die Quellzeile fehlt dort)."""
    return sha256_text("\n".join(sorted(u.unit_id for u in units if is_target(u, column))))


def _codebook_dump(codebook: CodebookConfig, *, groups: bool) -> dict:
    dump = codebook.model_dump(mode="json", exclude={"source_name", "encoding", "sha256"})
    if not groups:  # Code-Gruppen stehen nicht im Prompt und ändern keine Lauf-Identität
        for code in dump["codes"]:
            code["parent"] = None
    return dump


def sent_reasoning_effort(model: ModelConfig, provider: ProviderConfig) -> str | None:
    """OpenAI-kompatible Anbieter und Anthropic (als `effort`) kennen ihn; Ollama nicht."""
    return model.reasoning_effort if provider.kind in ("openai", "anthropic") else None


def fingerprint(project: Project) -> str:
    """Alles, was die Messung festlegt, ohne Anbieter zu fragen (also ohne Digests).

    Ändert sich der Fingerabdruck nach einem Freeze, ist das Instrument nicht mehr das
    präregistrierte."""
    return _sha(_fingerprint_payload(project))


def instrument_fingerprint(project: Project) -> str:
    """Wie fingerprint, ohne die Daten: dasselbe Instrument auf einer neuen Erhebungswelle."""
    payload = _fingerprint_payload(project)
    del payload["units_sha256"]
    payload["coding"].pop("pseudonyms_sha256", None)  # Namen gehören zu den Daten
    payload["coding"].pop("examples", None)  # Beispiele auch (transfer_note nennt sie)
    return _sha(payload)


def _sha(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _fingerprint_payload(project: Project) -> dict:
    cfg = project.config
    panel = []
    for model in cfg.panel:
        provider = cfg.provider(model.provider)
        panel.append(
            {
                **_model_dump(model),
                "provider": provider.model_dump(
                    mode="json", include={"kind", "base_url", "capabilities"}
                ),
            }
        )
    payload = {
        "template_version": TEMPLATE_VERSION,
        "language": cfg.prompt_language,
        "mode": cfg.mode,
        "k": cfg.k,
        # Mit Gruppen: Sie gehören zum Analyseplan; Umgruppieren nach dem Freeze bricht ihn.
        "codebook": _codebook_dump(cfg.codebook, groups=True) if cfg.codebook else None,
        "codebook_text_sha256": sha256_text(project.codebook_text or ""),
        "units_sha256": cfg.data.units_sha256 if cfg.data else None,
        "has_meta": bool(cfg.data and cfg.data.mapping.meta),
        "coding": _coding_dump(cfg.coding),
        "review": _review_dump(cfg.review),  # Gate-Regeln gehören zum Instrument
        "panel": panel,
    }
    if column := cfg.coding.units.target_column:  # nur gesetzt: alte Hashes bleiben
        payload["targets_sha256"] = _targets_sha(project.store.units(), column)
    return payload


def prepare_prompts(project: Project, *, sending: bool = False) -> tuple[PromptSpec, list[Item]]:
    """Prompt-Spezifikation und Einheiten aus dem Projekt; braucht kein Modell im Panel.

    `sending`: Der Prompt geht an ein Modell (Lauf, Budget, Vorschau). Fehlt dann die
    Namensliste, bricht das ab; sonst (Auswertung, Exporte) fehlen nur die Nutzer-Nachrichten."""
    cfg = project.config
    units = project.store.units()
    if cfg.data is None or not units:
        raise ValueError(
            "Es sind noch keine Daten importiert: im Tab Daten eine Tabelle importieren."
        )
    if cfg.codebook is None or not cfg.codebook.active_codes():
        raise ValueError(
            "Die Code-Liste hat noch keine aktiven Codes: im Tab Codebuch ein Codebuch laden "
            "und Codes eintragen oder vorschlagen lassen."
        )
    text = project.codebook_text
    if text is None:
        raise ValueError(
            "Die Codebuch-Datei fehlt im Projektordner: im Tab Codebuch das Codebuch neu laden."
        )
    missing = False
    try:
        names = project.pseudonymizer()
    except ValueError:
        if sending:
            raise
        names, missing = None, True
    spec = PromptSpec(
        language=cfg.prompt_language,
        mode=cfg.mode,
        k=cfg.k,
        unit_mode=cfg.coding.units.mode,
        has_meta=bool(cfg.data.mapping.meta),
        codebook_text=text,
        codebook=cfg.codebook,
        max_signal_words=cfg.coding.max_signal_words,
        max_rationale_words=cfg.coding.max_rationale_words,
        permute_codes=cfg.coding.permute_codes,
        pseudonyms=names,
        names_missing=missing,
    )
    items = build_items(units, cfg.coding.units)
    if cfg.coding.examples:
        by_id = {i.item_id: i for i in items}
        allowed = set(cfg.codebook.allowed_ids())
        wrong = [
            e.item_id
            for e in cfg.coding.examples
            if e.item_id not in by_id
            or not e.codes
            or set(e.codes) - allowed
            or (cfg.mode == "single" and len(e.codes) != 1)
        ]
        if wrong:
            raise ValueError(
                "Die Beispiele passen nicht mehr zu Daten, Codebuch oder Modus ("
                + ", ".join(wrong[:5])
                + "). Im Tab Prompt & Einheiten entfernen oder neu auswählen."
            )
        examples = tuple((by_id[e.item_id], tuple(e.codes)) for e in cfg.coding.examples)
        spec = replace(spec, examples=examples)
    return spec, items


class Engine:
    def __init__(
        self,
        project: Project,
        *,
        provider_factory: ProviderFactory | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        variant: str | None = None,
        models: Collection[str] | None = None,
    ) -> None:
        """`variant` (siehe core.sensitivity) macht einen Sensitivitätslauf: ein Lauf je
        Einheit, ohne adaptiven Abbruch, nicht in der Lauf-Tabelle (die Ergebnisse bleiben
        unberührt) und ohne Freeze-Prüfung, weil er das Instrument nicht ändert. `models`:
        nur diese Panel-Modelle (Schlüssel)."""
        self.project = project
        self._factory = provider_factory or make_provider
        # Die Oberfläche übergibt threading.Event.wait: Anhalten beendet eine Wartezeit sofort.
        self._sleep = sleep
        self._should_stop: Callable[[], bool] = lambda: False
        self._last_retryable = False  # letzter Fehler war vorübergehend (Netz, 5xx, 429)
        self._clock = clock
        self._last_call: dict[str, float] = {}
        self._rate_lock = threading.Lock()
        self._throttles: dict[str, _Throttle] = {}
        self._notes: list[tuple[str, dict]] = []  # Protokoll aus Worker-Threads
        self._notes_lock = threading.Lock()
        self._ahead: _Prefetch | None = None
        self._quota: dict[str, dict[str, tuple[int, int | None]]] = {}
        # Prüfreihenfolge wie der Arbeitsablauf: Daten, Codebuch, dann Panel.
        self.spec, self.items = prepare_prompts(project, sending=True)
        self.variant = variant
        self._models = None if models is None else set(models)
        self.units = project.config.coding.units
        if variant == ISOLATED:
            self.units = UnitSettings(mode="isolated", target_column=self.units.target_column)
            examples = tuple((replace(i, before=(), after=()), c) for i, c in self.spec.examples)
            self.spec = replace(self.spec, unit_mode="isolated", examples=examples)
            self.items = build_items(project.store.units(), self.units)
        elif variant and variant != REASONING_LOW:
            self.spec = replace(self.spec, variant=variant)
        self.n_runs = 1 if variant is not None else project.config.coding.n_runs
        self.adaptive = variant is None and project.config.coding.adaptive
        if not project.config.panel:
            raise ValueError("Im Panel ist noch kein Modell: im Tab Modelle eines übernehmen.")
        assert project.config.codebook is not None
        self.schema = response_schema(project.config.codebook.allowed_ids())

    # --- Vorbereitung --------------------------------------------------------------------------

    def remote_providers(self) -> list[ProviderConfig]:
        """Anbieter im Panel, an die Daten das Gerät verlassen; brauchen eine Zustimmung."""
        cfg = self.project.config
        keys = dict.fromkeys(m.provider for m in cfg.panel)
        return [cfg.provider(k) for k in keys if cfg.provider(k).privacy_level != "local"]

    def prepare(self, prepared: list[PreparedModel]) -> None:
        """Füllt `prepared`; was bis zu einem Fehler entsteht, schließt der Aufrufer."""
        cfg = self.project.config
        for model in cfg.panel:
            if self._models is not None and model.key not in self._models:
                continue
            if self.variant == REASONING_LOW:
                model = model.model_copy(update={"reasoning_effort": "low"})
            provider_cfg = cfg.provider(model.provider)
            try:
                provider = self._factory(provider_cfg)
            except ProviderError as exc:
                raise RunAborted(str(exc)) from exc
            try:
                info = self._model_info(provider, provider_cfg, model)
                num_ctx = self._num_ctx(model, info) if provider_cfg.kind == "ollama" else None
                server_version = self._server_version(provider)
            except Exception:
                provider.close()
                raise
            # Sensitivität greedy: sonst mischt α Zufallsstreuung in den Wortlaut-Effekt.
            temperature = (
                (None if model.provider_temperature else 0.0)
                if self.variant is not None
                else model.effective_temperature(self.n_runs)
            )
            prepared.append(
                PreparedModel(
                    model, provider_cfg, provider, info, num_ctx,
                    self._config_hash(model, provider_cfg, info, num_ctx, temperature),
                    temperature, server_version,
                )
            )  # fmt: skip

    @staticmethod
    def _server_version(provider: Provider) -> str | None:
        version = getattr(provider, "version", None)  # nur Ollama
        try:
            return version() if version else None
        except ProviderError:
            return None

    def _model_info(
        self, provider: Provider, provider_cfg: ProviderConfig, model: ModelConfig
    ) -> ModelInfo | None:
        try:
            infos = provider.list_models()
        except ProviderError as exc:
            if provider_cfg.kind == "ollama":
                message = str(exc)  # send() nennt Adresse und Ursache meist schon
                if "nicht erreichbar" not in message:
                    message = f"Ollama ist nicht erreichbar: {message}"
                raise RunAborted(message) from exc
            return None  # manche Anbieter haben kein /models; der Lauf zeigt, ob das Modell geht
        # Ollama nimmt „llama3“ für „llama3:latest“ an; die Liste nennt immer den Tag.
        names = {model.model, f"{model.model}:latest"}
        info = next((i for i in infos if i.name in names), None)
        if info is None and provider_cfg.kind == "ollama":
            raise RunAborted(f"Modell „{model.model}“ ist in Ollama nicht installiert.")
        show = getattr(provider, "show", None)
        if info is not None and show and (info.context_length is None or not info.capabilities):
            try:
                details = show(info.name)
            except ProviderError:
                return info
            info = replace(
                info,
                context_length=info.context_length or details.context_length,
                capabilities=info.capabilities or details.capabilities,
            )
        return info

    def _num_ctx(self, model: ModelConfig, info: ModelInfo | None) -> int:
        """Ein fester Wert pro Lauf: ändert sich num_ctx, lädt Ollama das Modell neu."""
        system = estimate_tokens(render_system(self.spec, 0))
        longest = max(estimate_tokens(render_user(self.spec, i)) for i in self.items)
        # Platz für einen Repair: vorige Antwort, Repair-Nachricht und eine zweite Antwort.
        total = system + longest + 2 * model.max_tokens + REPAIR_OVERHEAD_TOKENS
        needed = math.ceil(total * 1.1 / 1024) * 1024
        num_ctx = max(needed, 4096)
        if info and info.context_length and num_ctx > info.context_length:
            raise RunAborted(
                f"Der längste Prompt braucht etwa {num_ctx} Token, „{model.model}“ kann höchstens "
                f"{info.context_length}. Kürzeres Codebuch, kleineres Kontextfenster oder anderes "
                "Modell wählen."
            )
        return num_ctx

    def _config_hash(
        self,
        model: ModelConfig,
        provider_cfg: ProviderConfig,
        info: ModelInfo | None,
        num_ctx: int | None,
        temperature: float,
    ) -> str:
        """Identität eines Laufs. N und die adaptive Regel gehören nicht dazu: mehr Läufe
        ergänzen einen bestehenden Lauf, statt ihn zu ersetzen."""
        cfg = self.project.config
        assert cfg.data is not None and cfg.codebook is not None
        payload = {
            "template_version": TEMPLATE_VERSION,
            "language": cfg.prompt_language,
            "mode": cfg.mode,
            "k": cfg.k,
            "codebook": _codebook_dump(cfg.codebook, groups=False),
            # Der Text, der wirklich im Prompt steht, nicht die Prüfsumme vom Laden.
            "codebook_text_sha256": sha256_text(self.spec.codebook_text),
            "units_sha256": cfg.data.units_sha256,
            "has_meta": bool(cfg.data.mapping.meta),
            "coding": _coding_dump(
                cfg.coding.model_copy(update={"units": self.units}),
                exclude={"n_runs", "adaptive", "adaptive_min_agree"},
            ),
            "provider": {
                "kind": provider_cfg.kind,
                "base_url": provider_cfg.base_url,
                "capabilities": provider_cfg.capabilities.model_dump(mode="json"),
            },
            # provider_temperature steckt in temperature (None).
            "model": _model_dump(model, exclude={"temperature", "provider_temperature"}),
            "temperature": temperature,
            "digest": info.digest if info else None,
            "num_ctx": num_ctx,
        }
        if self.units.target_column:
            payload["targets_sha256"] = _targets_sha(
                self.project.store.units(), self.units.target_column
            )
        if self.variant is not None:  # Hauptläufe ohne: bestehende Identitäten bleiben gleich
            payload["prompt_variant"] = self.variant or "original"
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    # --- Lauf ----------------------------------------------------------------------------------

    def run(
        self,
        *,
        item_limit: int | None = None,
        item_ids: Collection[str] | None = None,
        remote_consent: bool = False,
        should_stop: Callable[[], bool] = lambda: False,
        on_progress: Callable[[Progress], None] | None = None,
    ) -> RunSummary:
        remote = self.remote_providers()
        if remote and not remote_consent:
            raise PermissionError(
                "Daten gingen an externe Anbieter: " + ", ".join(p.name for p in remote)
            )
        store = self.project.store
        items = self.items if item_ids is None else [i for i in self.items if i.item_id in item_ids]
        items = items[:item_limit] if item_limit else items
        summary = RunSummary()
        self._quota = summary.quota
        prepared: list[PreparedModel] = []
        self._should_stop = should_stop
        try:
            self.prepare(prepared)
            summary.parallel = min((m.provider_config.parallel for m in prepared), default=1)
            self._throttles = {
                m.provider_config.key: _Throttle(m.provider_config.parallel)
                for m in prepared
                if m.provider_config.parallel > 1
            }
            if self.variant is None:
                self._check_freeze(prepared)
            if remote:
                store.log(
                    "remote_consent",
                    providers=[
                        {"name": p.name, "base_url": p.base_url, "privacy": p.privacy_level}
                        for p in remote
                    ],
                    n_items=len(items),
                )
            # Nur die offene Arbeit: Beim Fortsetzen blieb der Balken sonst bei dem Anteil
            # stehen, den der frühere Lauf schon erledigt hatte.
            wanted = {i.item_id for i in items}
            finished = sum(
                1
                for model in prepared
                for c in store.calls(
                    model_key=model.config.key, config_hash=model.config_hash, with_raw=False
                )
                if c.item_id in wanted and c.run < self.n_runs and c.status != "error"
            )
            planned = len(items) * self.n_runs * len(prepared) - finished
            for model in prepared:
                details = {
                    "model": model.config.key,
                    "config_hash": model.config_hash,
                    "digest": model.info.digest if model.info else None,
                    "num_ctx": model.num_ctx,
                    "temperature": model.temperature,
                    "n_items": len(items),
                    "n_runs": self.n_runs,
                    "adaptive": self.adaptive,
                    "server_version": model.server_version,
                    "model_details": _model_details(model.info),
                }
                if model.provider_config.parallel > 1:
                    details["parallel"] = model.provider_config.parallel
                if self.variant is None:
                    store.start_run(model.config.key, model.config_hash, self._run_settings())
                    # Alles, was Report und GUIDE-LLM über den Lauf sagen; die Konfiguration
                    # kann sich danach ändern.
                    store.log(
                        "run_started",
                        **details,
                        fingerprint=fingerprint(self.project),
                        model_config=model.config.model_dump(mode="json"),
                        provider_config=model.provider_config.model_dump(mode="json"),
                        reasoning_effort=sent_reasoning_effort(model.config, model.provider_config),
                        coding=self.project.config.coding.model_dump(mode="json"),
                    )
                else:
                    store.log(
                        SENSITIVITY_STARTED,
                        variant=self.variant,
                        fingerprint=fingerprint(self.project),
                        **details,
                    )
                existing = {
                    (c.item_id, c.run): c
                    for c in store.calls(
                        model_key=model.config.key, config_hash=model.config_hash, with_raw=False
                    )
                }
                consecutive_errors = invalid_units = 0
                outage = pauses = 0  # Wartezeit auf das Netz (s) und Zahl der Pausen
                ahead = _Prefetch(self, model, items, existing, self._keep(summary, existing))
                self._ahead = ahead
                for index, item in enumerate(items):
                    ahead.fill(index)
                    executed = 0
                    statuses: set[str] = set()
                    for run in range(self.n_runs):
                        if self._adaptive_done(item.item_id, run, existing):
                            break
                        previous = existing.get((item.item_id, run))
                        if previous is not None and previous.status != "error":
                            continue
                        try:
                            if should_stop():
                                raise _Stopped
                            record = self._store(ahead.take(item, run))
                        except _Stopped:
                            summary.stopped = True
                            ahead.finish(save=True)
                            store.log("run_stopped", model=model.config.key, calls=summary.calls)
                            return summary
                        store.record_call(record)
                        existing[(item.item_id, run)] = record
                        executed += 1
                        self._count(summary, record)
                        consecutive_errors = (
                            consecutive_errors + 1 if record.status == "error" else 0
                        )
                        statuses.add(record.status)
                        if record.status == "ok":
                            outage = pauses = 0
                        if on_progress:
                            on_progress(
                                Progress(
                                    model.config.key, item.item_id, run, record.status,
                                    summary.calls, planned, record.error,
                                )
                            )  # fmt: skip
                        if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                            if not self._last_retryable or outage >= MAX_OUTAGE_S:
                                raise RunAborted(
                                    f"{MAX_CONSECUTIVE_ERRORS} Fehler in Folge bei "
                                    f"{model.config.key}, zuletzt: {record.error}"
                                    + (f" (nach {outage // 60} min Warten)" if outage else "")
                                )
                            # Vermutlich ein Netzausfall: warten und weitermachen. Die
                            # fehlgeschlagenen Aufrufe wiederholt der nächste Start.
                            wait = OUTAGE_WAITS_S[min(pauses, len(OUTAGE_WAITS_S) - 1)]
                            pauses += 1
                            store.log("run_waiting", model=model.config.key, seconds=wait,
                                      reason=record.error)  # fmt: skip
                            ahead.hold()  # Vorausgeschicktes erst nach der Pause
                            try:
                                self._pause(wait)
                            except _Stopped:  # Anhalten während der Pause: kein Abbruch
                                summary.stopped = True
                                ahead.finish(save=True)
                                store.log(
                                    "run_stopped", model=model.config.key, calls=summary.calls
                                )
                                return summary
                            outage += wait
                            consecutive_errors = 0
                    if executed:
                        summary.calls_per_item.append(executed)
                    if "ok" in statuses:
                        invalid_units = 0
                    elif "invalid" in statuses:
                        invalid_units += 1
                        if invalid_units >= MAX_INVALID_UNITS:
                            raise RunAborted(
                                f"{MAX_INVALID_UNITS} Einheiten in Folge ohne gültige Antwort bei "
                                f"{model.config.key}: Das Modell hält das Ausgabeformat nicht "
                                f"ein (zuletzt: {record.error}). Ausgabemodus des Anbieters, "
                                "max. Tokens oder ein anderes Modell prüfen."
                            )
                ahead.finish(save=False)
                store.log(
                    "run_finished" if self.variant is None else "sensitivity_finished",
                    model=model.config.key,
                    config_hash=model.config_hash,
                    calls=summary.calls,
                    ok=summary.ok,
                    invalid=summary.invalid,
                    errors=summary.errors,
                )
        except Exception as exc:  # auch unerwartete Fehler gehören ins Protokoll
            if isinstance(exc, RunAborted):
                exc.summary = summary
            if self._ahead is not None:  # fertige Antworten nicht wegwerfen (Kontingent!)
                self._ahead.finish(save=True)
            store.log("run_aborted", reason=str(exc) or type(exc).__name__, type=type(exc).__name__)
            raise
        finally:
            if self._ahead is not None:
                self._ahead.finish(save=False)  # laufende Anfragen abwarten, offene verwerfen
                self._ahead = None
            for model in prepared:
                model.provider.close()
        return summary

    def _check_freeze(self, prepared: list[PreparedModel]) -> None:
        state = self.project.config.freeze
        if state is None:
            return
        hashes = {m.config.key: m.config_hash for m in prepared}
        if fingerprint(self.project) == state.fingerprint and hashes == state.model_hashes:
            return
        changed = sorted(k for k in hashes.keys() | state.model_hashes.keys()
                         if hashes.get(k) != state.model_hashes.get(k))  # fmt: skip
        self.project.store.log(
            "freeze_broken", instrument_hash=state.instrument_hash, changed_models=changed
        )
        raise FreezeBroken(
            "Das Instrument entspricht nicht mehr dem Freeze"
            + (f" (geändert: {', '.join(changed)})" if changed else " (Konfiguration geändert)")
            + ". Änderungen zurücknehmen oder den Freeze mit Begründung aufheben."
        )

    def _run_settings(self) -> dict:
        """Was die Aggregation braucht, um die Aufrufe dieses Laufs richtig zu deuten."""
        cfg = self.project.config
        assert cfg.codebook is not None
        return {
            "mode": cfg.mode,
            "k": cfg.k,
            "abstain_id": cfg.codebook.abstain_id if cfg.codebook.abstain else None,
            "n_codes": len(cfg.codebook.allowed_ids()),
        }

    def _adaptive_done(self, item_id: str, run: int, existing: dict) -> bool:
        """Stopp, sobald genug gültige Läufe einstimmig sind.

        Netzwerkfehler sagen nichts über die Kodierung und werden übergangen; eine ungültige
        Antwort zählt als Unsicherheit und verhindert den Stopp.
        """
        cfg = self.project.config.coding
        if not self.adaptive or run < cfg.adaptive_min_agree:
            return False
        prior = [existing.get((item_id, r)) for r in range(run)]
        return _stops(prior, cfg.adaptive_min_agree, self.project.config.mode)

    def _count(self, summary: RunSummary, record: CallRecord) -> None:
        summary.calls += 1
        if record.status == "ok":
            summary.ok += 1
        elif record.status == "invalid":
            summary.invalid += 1
        else:
            summary.errors += 1
        if "repariert" in record.flags:
            summary.repaired += 1
        for part in (record.meta, record.meta.get("repair") or {}):
            summary.tokens_in += part.get("tokens_in") or 0
            summary.tokens_out += part.get("tokens_out") or 0
            for key in ("tokens_cached", "tokens_reasoning"):
                if (value := part.get(key)) is not None:
                    setattr(summary, key, (getattr(summary, key) or 0) + value)
        # Gesamtdauer inklusive Repair, sonst unterschätzt die Hochrechnung die Laufzeit.
        if (duration := record.meta.get("duration_s")) is not None:
            summary.latencies.append(duration)

    # --- Ein Aufruf ----------------------------------------------------------------------------

    def _store(self, outcome: _Outcome) -> CallRecord:
        """Texte des Aufrufs speichern (im Thread des Laufs); der Aufruf selbst folgt."""
        with self._notes_lock:
            notes, self._notes = self._notes, []
        for event, details in notes:
            self.project.store.log(event, **details)
        for text in outcome.texts:
            self.project.store.put_text(text)
        self._last_retryable = outcome.retryable
        return outcome.record

    def _keep(self, summary: RunSummary, existing: dict) -> Callable[[_Outcome], None]:
        """Beim Anhalten: schon fertige Vorab-Anfragen speichern statt sie wegzuwerfen."""

        def keep(outcome: _Outcome) -> None:
            record = self._store(outcome)
            key = (record.item_id, record.run)
            if key in existing and existing[key].status != "error":
                return
            self.project.store.record_call(record)
            existing[key] = record
            self._count(summary, record)

        return keep

    def _call(self, model: PreparedModel, item: Item, run: int) -> _Outcome:
        """Ein Aufruf samt Repair, ohne Store: läuft bei parallelen Anfragen im Worker."""
        cfg = self.project.config
        seed = cfg.coding.base_seed + run
        prompt = render(self.spec, item, seed)
        system_sha, user_sha = sha256_text(prompt.system), sha256_text(prompt.user)
        texts = (prompt.system, prompt.user)
        retryable = False
        request = ChatRequest(
            model=model.config.model,
            messages=prompt.messages(),
            temperature=model.temperature,
            max_tokens=model.config.max_tokens,
            seed=seed if model.provider_config.capabilities.seed is not False else None,
            schema=self.schema,
            num_ctx=model.num_ctx,
            think=model.config.thinking if model.provider_config.kind == "ollama" else None,
            reasoning_effort=sent_reasoning_effort(model.config, model.provider_config),
        )
        params = {
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "seed": request.seed,
            "num_ctx": request.num_ctx,
            "think": request.think,
            "reasoning_effort": request.reasoning_effort,
        }

        raw: list[str] = []
        meta: dict = {}
        flags: list[str] = []
        started = time.perf_counter()

        def record(status: str, **fields) -> _Outcome:
            duration = round(time.perf_counter() - started, 3)
            fields["meta"] = {**fields["meta"], "duration_s": duration}
            return _Outcome(_record(status, **fields), texts, retryable)

        def _record(status: str, **fields) -> CallRecord:
            return CallRecord(
                item_id=item.item_id, model_key=model.config.key, run=run,
                config_hash=model.config_hash, seed=seed, system_sha=system_sha,
                user_sha=user_sha, status=status, request=params, **fields,
            )  # fmt: skip

        try:
            response = self._chat(model, request)
            raw.append(response.text)
            meta = self._meta(response)
            self._check_truncation(model, response)
            try:
                parsed = self._parse(response.text)
            except AnswerError as error:
                flags.append("repair_gescheitert")  # „repariert“ erst, wenn er trägt
                repaired = self._chat(
                    model, self._repair(request, response.text, error.kind, error.detail)
                )
                raw.append(repaired.text)
                meta["repair"] = self._meta(repaired)
                self._check_truncation(model, repaired)
                parsed = self._parse(repaired.text)
                flags[flags.index("repair_gescheitert")] = "repariert"
            if response.finish_reason == "length":
                flags.append("ausgabe_abgeschnitten")
        except AnswerError as error:
            return record("invalid", answer=None, raw=raw, error=str(error), flags=flags, meta=meta)
        except ProviderError as error:
            retryable = error.retryable
            return record("error", answer=None, raw=raw, error=str(error), flags=flags, meta=meta)

        answer = self._with_evidence(parsed, item)
        missing = self._missing(answer)
        if missing and "repariert" not in flags and cfg.coding.evidence_repair:
            # Ein Repair-Versuch für erfundene Signalwörter. Übernommen wird er nur, wenn er
            # dieselbe Entscheidung trifft und besser belegt ist: Der Repair soll Belege
            # korrigieren, nicht die Kodierung zugunsten leicht zitierbarer Codes verschieben.
            detail = ", ".join(f"„{m}“" for m in missing)
            try:
                retry = self._chat(model, self._repair(request, raw[-1], "evidence", detail))
                raw.append(retry.text)
                meta["repair"] = self._meta(retry)
                self._check_truncation(model, retry)
                candidate = self._with_evidence(self._parse(retry.text), item)
                same = decision(candidate, cfg.mode) == decision(answer, cfg.mode)
                if same and len(self._missing(candidate)) < len(missing):
                    answer = candidate
                    flags.append("repariert")
                else:  # andere Entscheidung oder nicht besser belegt
                    flags.append("repair_verworfen")
            except (AnswerError, ProviderError):
                flags.append("repair_gescheitert")
        if self._missing(answer):
            flags.append("nicht_belegt")
        for flag in answer.pop("flags"):
            if flag not in flags:
                flags.append(flag)
        return record("ok", answer=answer, raw=raw, error=None, flags=flags, meta=meta)

    def _chat(self, model: PreparedModel, request: ChatRequest) -> ChatResponse:
        provider = model.provider_config
        throttle = self._throttles.get(provider.key)
        for attempt in range(len(RETRY_DELAYS_S) + 1):
            self._respect_rate_limit(provider)
            concurrent = 0  # gleichzeitige Anfragen beim Start dieses Versuchs
            try:
                if throttle is None:
                    response = model.provider.chat(request)
                else:
                    with throttle as concurrent:
                        response = model.provider.chat(request)
                if response.quota:
                    with self._notes_lock:
                        self._quota[provider.key] = response.quota
                return response
            except ProviderError as error:
                # „Zu viele Anfragen“ bei paralleler Last: weniger gleichzeitig schicken.
                limit = throttle.shrink(concurrent) if throttle and error.status == 429 else None
                if limit is not None:
                    with self._notes_lock:
                        self._notes.append(
                            ("parallel_reduced", {"provider": provider.key, "parallel": limit})
                        )
                if not error.retryable or attempt == len(RETRY_DELAYS_S):
                    raise
                delay = error.retry_after or RETRY_DELAYS_S[attempt]
                if delay > MAX_RETRY_AFTER_S:
                    raise RunAborted(
                        f"{model.provider_config.name} verlangt {delay / 60:.0f} min Pause "
                        "(Retry-After), vermutlich ist das Kontingent erschöpft. Den Lauf "
                        "später fortsetzen; fertige Aufrufe bleiben erhalten."
                    ) from error
                self._pause(delay)
        raise AssertionError("unreachable")

    def _respect_rate_limit(self, provider: ProviderConfig) -> None:
        if not provider.rate_limit_per_min:
            return
        interval = 60 / provider.rate_limit_per_min
        with self._rate_lock:  # parallele Anfragen reihen sich hier ein
            last = self._last_call.get(provider.key)
            now = self._clock()
            if last is not None and now - last < interval:
                self._pause(interval - (now - last))
            self._last_call[provider.key] = self._clock()

    def _pause(self, seconds: float) -> None:
        self._sleep(seconds)
        if self._should_stop():
            raise _Stopped

    def _check_truncation(self, model: PreparedModel, response: ChatResponse) -> None:
        # num_ctx ist pessimistisch geschätzt. Reicht es trotzdem nicht, hat Ollama den Prompt
        # still gekürzt: Codebuch oder Zieltext fehlten dann teilweise. Das ist systematisch.
        if model.num_ctx and response.tokens_in:
            if response.tokens_in + model.config.max_tokens > model.num_ctx:
                raise RunAborted(
                    f"Prompt vermutlich gekürzt ({response.tokens_in} Token bei num_ctx "
                    f"{model.num_ctx}). Der Lauf wurde abgebrochen."
                )

    def _parse(self, text: str) -> Parsed:
        cfg = self.project.config
        assert cfg.codebook is not None
        return parse_answer(
            text,
            allowed=cfg.codebook.allowed_ids(),
            abstain_id=cfg.codebook.abstain_id if cfg.codebook.abstain else None,
            mode=cfg.mode,
            k=cfg.k,
            max_signal_words=cfg.coding.max_signal_words,
        )

    def _repair(self, request: ChatRequest, previous: str, kind: str, detail: str) -> ChatRequest:
        message = repair_message(self.spec.language, kind, detail, self.spec.unit_mode)
        messages = [
            *request.messages,
            {"role": "assistant", "content": previous},
            {"role": "user", "content": message},
        ]
        return replace(request, messages=messages)

    def _with_evidence(self, parsed: Parsed, item: Item) -> dict:
        find = self.spec.pseudonyms.locate if self.spec.pseudonyms else locate
        return {
            "rationale": parsed.rationale,
            "candidates": [
                {
                    "code": c.code,
                    "confidence": c.confidence,
                    "evidence": [find(e, item.target).to_dict() for e in c.evidence],
                }
                for c in parsed.candidates
            ],
            "flags": list(parsed.flags),
        }

    @staticmethod
    def _missing(answer: dict) -> list[str]:
        return [
            ev["text"]
            for cand in answer["candidates"]
            for ev in cand["evidence"]
            if ev["status"] == "missing"
        ]

    @staticmethod
    def _meta(response: ChatResponse) -> dict:
        return {
            "model_returned": response.model,
            "tokens_in": response.tokens_in,
            "tokens_out": response.tokens_out,
            "latency_s": round(response.latency_s, 3),
            "finish_reason": response.finish_reason,
            "output_mode": response.output_mode,
            **response.extra,
        }

    # --- Ergebnisse ----------------------------------------------------------------------------

    def results(self) -> dict[str, ItemResult]:
        return results(self.project, [i.item_id for i in self.items])


@dataclass(frozen=True)
class RunBudget:
    """Trockenlauf: was ein Lauf kostet, ohne einen Anbieter zu fragen."""

    calls: int  # obere Schranke (ohne adaptiven Stopp und Repairs)
    tokens_in: int  # pessimistisch geschätzt (3 Zeichen je Token)
    tokens_out: int  # obere Schranke: max. Tokens je Aufruf
    longest: tuple[str, int]  # Einheit mit dem längsten Nutzer-Prompt, dessen Token
    median: int  # mittlere Länge des Nutzer-Prompts (ohne System-Prompt mit Codebuch)
    system: int = 0  # Token des System-Prompts, für jeden Aufruf gleich
    calls_min: int = 0  # untere Schranke: jede Einheit stoppt adaptiv so früh wie möglich
    tokens_in_min: int = 0
    # Ausgabe nach den bisherigen gültigen Antworten der Modelle (von, bis); None: ein Modell
    # hat noch keine
    tokens_out_seen: tuple[int, int] | None = None
    # Aufrufe je Anbieter (Schlüssel): (mindestens, höchstens), für das Kontingent
    per_provider: dict[str, tuple[int, int]] = field(default_factory=dict)

    @property
    def outlier(self) -> bool:
        """Eine Einheit ist so lang, dass sie num_ctx für alle Aufrufe hochtreibt."""
        return self.longest[1] > 3 * self.median and self.longest[1] - self.median > 500


def run_budget(
    project: Project,
    *,
    item_ids: Collection[str] | None = None,
    item_limit: int | None = None,
    runs: int | None = None,
    models: list[ModelConfig] | None = None,
    adaptive: bool | None = None,
    resume: bool = True,
) -> RunBudget:
    """`adaptive`: None folgt dem Projekt; Sensitivitätsläufe stoppen nie früh. `resume`:
    fertige Aufrufe des letzten Laufs mit gleichem Instrument und Modell abziehen, wie beim
    Fortsetzen."""
    spec, items = prepare_prompts(project, sending=True)
    if item_ids is not None:
        items = [i for i in items if i.item_id in item_ids]
    items = items[:item_limit] if item_limit else items
    cfg = project.config
    models = cfg.panel if models is None else models
    runs = cfg.coding.n_runs if runs is None else runs
    adaptive = cfg.coding.adaptive if adaptive is None else adaptive
    min_agree = cfg.coding.adaptive_min_agree if adaptive else None
    system = estimate_tokens(render_system(spec, 0))
    prompts = [(i.item_id, estimate_tokens(render_user(spec, i))) for i in items]
    lengths = sorted(n for _, n in prompts) or [0]
    current = fingerprint(project) if resume else ""
    calls = calls_min = tokens_in = tokens_in_min = tokens_out = 0
    seen: tuple[float, float] | None = (0.0, 0.0) if models else None
    per_provider: dict[str, tuple[int, int]] = {}
    for m in models:
        done = _finished(project, m, current) if resume else {}
        low = high = 0
        for item_id, n in prompts:
            lo, hi = _open_calls(done.get(item_id, {}), runs, min_agree, cfg.mode)
            low, high = low + lo, high + hi
            tokens_in_min, tokens_in = (
                tokens_in_min + (system + n) * lo,
                tokens_in + (system + n) * hi,
            )
        calls_min, calls, tokens_out = (
            calls_min + low,
            calls + high,
            tokens_out + high * m.max_tokens,
        )
        was = per_provider.get(m.provider, (0, 0))
        per_provider[m.provider] = (was[0] + low, was[1] + high)
        effort = sent_reasoning_effort(m, cfg.provider(m.provider))
        mean = project.store.mean_tokens_out(m.key, effort)
        seen = (
            None if seen is None or mean is None else (seen[0] + low * mean, seen[1] + high * mean)
        )
    return RunBudget(
        calls=calls,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        longest=max(prompts, key=lambda p: p[1], default=("", 0)),
        median=lengths[len(lengths) // 2],
        system=system,
        calls_min=calls_min,
        tokens_in_min=tokens_in_min,
        tokens_out_seen=None if seen is None else (round(seen[0]), round(seen[1])),
        per_provider=per_provider,
    )


def current_runs(project: Project, current: str | None = None) -> dict[str, str]:
    """Panel-Modell → Lauf-Identität seines letzten Laufs mit dem jetzigen Instrument und
    denselben Modelleinstellungen; ohne solchen Lauf fehlt das Modell. Geschätzt: Die
    Lauf-Identität hängt auch am Server (Digest), den diese Prüfung nicht fragt."""
    current = fingerprint(project) if current is None else current
    configs = {m.key: m.model_dump(mode="json") for m in project.config.panel}
    found: dict[str, str] = {}
    for entry in project.store.audit():
        details = entry.details
        key = details.get("model")
        if (
            entry.event == "run_started"
            and key in configs
            and details.get("fingerprint") == current
            and details.get("model_config") == configs[key]
        ):
            found[key] = details["config_hash"]
    return found


def _finished(
    project: Project, model: ModelConfig, current: str
) -> dict[str, dict[int, CallRecord]]:
    """Fertige Aufrufe (nicht error) je Einheit und Lauf aus dem letzten Lauf mit diesem
    Instrument und diesem Modell; ein Fortsetzen wiederholt sie nicht."""
    config_hash = current_runs(project, current).get(model.key)
    if config_hash is None:
        return {}
    done: dict[str, dict[int, CallRecord]] = {}
    for c in project.store.calls(model_key=model.key, config_hash=config_hash, with_raw=False):
        if c.status != "error":
            done.setdefault(c.item_id, {})[c.run] = c
    return done


_AGREES = object()  # angenommene Antwort: gültig und einig mit den übrigen


def _stops(prior: list, min_agree: int, mode: str) -> bool:
    """Adaptiver Stopp vor dem nächsten Lauf: genug gültige Läufe, alle einstimmig.

    Netzwerkfehler sagen nichts über die Kodierung und werden übergangen; eine fehlende oder
    ungültige Antwort zählt als Unsicherheit und verhindert den Stopp.
    """
    if any(p is None or (p is not _AGREES and p.status == "invalid") for p in prior):
        return False
    real = [p for p in prior if p is not _AGREES and p.status == "ok"]
    agrees = sum(p is _AGREES for p in prior)
    decisions = {tuple(decision(p.answer, mode)) for p in real if p.answer}
    return len(real) + agrees >= min_agree and (
        len(decisions) == 1 or (agrees > 0 and not decisions)
    )


def _open_calls(
    done: dict[int, CallRecord], runs: int, min_agree: int | None, mode: str
) -> tuple[int, int]:
    """Offene Aufrufe einer Einheit (mindestens, höchstens), wie die Schleife des Laufs sie
    abarbeitet; `min_agree` None: ohne adaptiven Stopp. Höchstens: Jeder neue Aufruf verhindert
    den Stopp. Mindestens: Jeder neue Aufruf stimmt mit den bisherigen überein."""
    missing = [r for r in range(runs) if r not in done]
    if not missing or min_agree is None:
        return len(missing), len(missing)
    # Vor dem ersten fehlenden Lauf ist alles beantwortet: Hat die Einheit dort schon gestoppt?
    known = [done[q] for q in range(missing[0])]
    if any(_stops(known[:r], min_agree, mode) for r in range(min_agree, len(known) + 1)):
        return 0, 0
    low, filled = 0, dict(done)
    for r in range(runs):
        if r >= min_agree and _stops([filled[q] for q in range(r)], min_agree, mode):
            break
        if r not in done:
            low, filled[r] = low + 1, _AGREES
    return low, len(missing)


@dataclass(frozen=True)
class ResultBasis:
    """Welche Läufe in die Ergebnisse eingehen und wie sie zu deuten sind."""

    settings: dict  # mode, k, abstain_id, n_codes des zugrunde liegenden Laufs
    runs: dict[str, str]  # Modell → config_hash
    excluded: list[str]  # Panel-Modelle, deren letzter Lauf andere Einstellungen hatte
    current: bool  # entsprechen die Einstellungen der aktuellen Konfiguration?


def result_basis(project: Project) -> ResultBasis:
    """Grundlage der Ergebnisse: der zuletzt gestartete Lauf je Panel-Modell.

    Aggregiert wird mit den Einstellungen dieser Läufe, nicht mit den aktuellen. Haben die
    Modelle unterschiedliche Einstellungen, zählen die zur aktuellen Konfiguration passenden;
    die übrigen lassen sich nicht gemeinsam deuten und bleiben außen vor.
    """
    cfg = project.config
    assert cfg.codebook is not None
    current = {
        "mode": cfg.mode,
        "k": cfg.k,
        "abstain_id": cfg.codebook.abstain_id if cfg.codebook.abstain else None,
        "n_codes": len(cfg.codebook.allowed_ids()),
    }
    latest = {m.key: run for m in cfg.panel if (run := project.store.latest_run(m.key))}
    settings = {key: run[1] or current for key, run in latest.items()}  # alte Stores: aktuell
    candidates = list(settings.values())
    reference = current if current in candidates or not candidates else candidates[0]
    runs = {key: latest[key][0] for key in latest if settings[key] == reference}
    excluded = [key for key in latest if key not in runs]
    return ResultBasis(reference, runs, excluded, reference == current)


class _Throttle:
    """Höchstzahl gleichzeitiger Anfragen an einen Anbieter. Antwortet er „zu viele Anfragen“
    (429), sinkt sie unter die Zahl, bei der er ablehnte (bis 1), und bleibt so für diesen Lauf."""

    def __init__(self, limit: int) -> None:
        self.limit, self.active = limit, 0
        self._condition = threading.Condition()

    def __enter__(self) -> int:
        with self._condition:
            while self.active >= self.limit:
                self._condition.wait()
            self.active += 1
            return self.active

    def __exit__(self, *exc: object) -> None:
        with self._condition:
            self.active -= 1
            self._condition.notify_all()

    def shrink(self, rejected: int) -> int | None:
        """Grenze unter die Zahl gleichzeitiger Anfragen senken, bei der der Anbieter ablehnte;
        liefert die neue Grenze oder None, wenn sie schon so niedrig war."""
        with self._condition:
            new = max(1, min(self.limit, rejected - 1))
            if new == self.limit:
                return None
            self.limit = new
            return new


class _Prefetch:
    """Parallele Anfragen (ProviderConfig.parallel > 1): Läufe, die für eine Einheit sicher
    gebraucht werden (bis zur Zahl einstimmiger Läufe für den adaptiven Abbruch, ohne ihn alle),
    gehen vorab an einen Thread-Pool. Die Schleife des Laufs holt sie in derselben Reihenfolge ab
    wie sonst und entscheidet über Abbruch, Pausen und weitere Läufe wie bisher. Ohne
    Parallelität läuft jeder Aufruf erst, wenn die Schleife ihn braucht."""

    def __init__(
        self,
        engine: Engine,
        model: PreparedModel,
        items: list[Item],
        existing: dict,
        keep: Callable[[_Outcome], None],
    ) -> None:
        self.engine, self.model, self.items, self.existing = engine, model, items, existing
        self.keep = keep  # fertige Vorab-Anfragen speichern (Anhalten, Abbruch)
        self.workers = model.provider_config.parallel
        coding = engine.project.config.coding
        self.certain = (
            min(coding.adaptive_min_agree, engine.n_runs) if engine.adaptive else engine.n_runs
        )
        self.pending: dict[tuple[str, int], Future] = {}
        self.pool = ThreadPoolExecutor(self.workers) if self.workers > 1 else None
        self._next = 0  # erste Einheit, die noch nicht vorausgeschickt ist

    def fill(self, index: int) -> None:
        if self.pool is None:
            return
        position = max(self._next, index)
        while position < len(self.items) and len(self.pending) < 2 * self.workers:
            item = self.items[position]
            for run in range(self.certain):
                key = (item.item_id, run)
                previous = self.existing.get(key)
                if key in self.pending or (previous is not None and previous.status != "error"):
                    continue
                self.pending[key] = self.pool.submit(self._call, item, run)
            position += 1
        self._next = position

    def _call(self, item: Item, run: int) -> _Outcome:
        # Nach „Anhalten“ geht nichts Neues mehr an den Anbieter.
        if self.engine._should_stop():
            raise _Stopped
        return self.engine._call(self.model, item, run)

    def take(self, item: Item, run: int) -> _Outcome:
        future = self.pending.pop((item.item_id, run), None)
        if future is not None and not future.cancelled():
            return future.result()
        return self.engine._call(self.model, item, run)

    def hold(self) -> None:
        """Netzpause: Nicht gestartete Anfragen zurücknehmen, laufende abwarten und ihre
        Netzfehler verwerfen; beides geht nach der Pause neu hinaus. Sonst zählten Fehler aus
        der Ausfallzeit nach der Pause als neue Fehlerserie."""
        for key, future in list(self.pending.items()):
            if future.cancel():
                del self.pending[key]
                continue
            try:
                outcome = future.result()
            except Exception:  # Anhalten oder Abbruch: holt take() ab
                continue
            if outcome.record.status == "error" and outcome.retryable:
                del self.pending[key]
        self._next = 0

    def finish(self, save: bool) -> None:
        """Offene Anfragen verwerfen, laufende abwarten; fertige speichern, wenn `save`."""
        if self.pool is None:
            return
        for future in self.pending.values():
            future.cancel()
        self.pool.shutdown(wait=True, cancel_futures=True)
        if save:
            for future in self.pending.values():
                if future.done() and not future.cancelled() and future.exception() is None:
                    self.keep(future.result())
        self.pending = {}
        self.pool = None


def basis_warnings(project: Project) -> list[str]:
    """Wo die Ergebnisse nicht zur aktuellen Konfiguration passen."""
    basis = result_basis(project)
    warnings: list[str] = [
        Message(
            f"{key}: Der letzte Lauf hatte andere Einstellungen (Modus, k, Restkategorie oder "
            "Zahl der Codes) als die übrigen Modelle. Das Modell fehlt in den Ergebnissen, bis "
            "es mit der aktuellen Konfiguration neu läuft.",
            f"{key}: its last run used different settings (mode, k, residual category or "
            "number of codes) than the other models. The model is missing from the results "
            "until it runs again with the current configuration.",
        )
        for key in basis.excluded
    ]
    if basis.runs and not basis.current:
        warnings.append(
            Message(
                "Die Ergebnisse stammen aus Läufen mit anderen Einstellungen (Modus, k, "
                "Restkategorie oder Zahl der Codes) als die aktuelle Konfiguration. "
                "Ausgewertet wird nach den Einstellungen der Läufe; für die aktuelle "
                "Konfiguration neu kodieren.",
                "The results come from runs with different settings (mode, k, residual "
                "category or number of codes) than the current configuration. They are "
                "evaluated with the settings of the runs; recode for the current "
                "configuration.",
            )
        )
    return warnings


def served_models(project: Project) -> dict[str, Counter[str]]:
    """Vom Server gemeldete Modellnamen je Panel-Modell, über die Ergebnisgrundlage.

    Hinter einem API-Namen kann der Anbieter das Modell austauschen; der Digest fehlt dort.
    """
    return {
        key: Counter(
            c.meta["model_returned"]
            for c in project.store.calls(model_key=key, config_hash=config_hash, with_raw=False)
            if c.meta.get("model_returned")
        )
        for key, config_hash in result_basis(project).runs.items()
    }


def served_model_warnings(project: Project, only: str | None = None) -> list[str]:
    warnings = []
    for key, names in served_models(project).items():
        if len(names) > 1 and only in (None, key):
            listed = ", ".join(f"{name} ({n}×)" for name, n in names.most_common())
            warnings.append(
                Message(
                    f"{key}: Der Anbieter meldete verschiedene Modellnamen: {listed}. Das "
                    "können verschiedene Modelle, Versionen oder Server sein. Das Blatt "
                    "„Läufe“ im XLSX-Export nennt den Namen je Aufruf: dort prüfen, ob die "
                    "Codes davon abhängen, und es im Bericht angeben.",
                    f"{key}: the provider reported different model names: {listed}. These "
                    "may be different models, versions or servers. The sheet “Läufe” (runs) "
                    "in the XLSX export lists the name per call: check there whether the "
                    "codes depend on it, and state it in the report.",
                )
            )
    return warnings


def results(
    project: Project, item_ids: list[str], only: list[str] | None = None
) -> dict[str, ItemResult]:
    """Ergebnis je Einheit; braucht keinen Anbieter, alles steht im Store.

    `only` beschränkt auf einzelne Panel-Modelle (Evaluation je Modell)."""
    basis = result_basis(project)
    # Ein Panel-Modell ohne Lauf (etwa nach Abbruch beim ersten Modell) fehlt in jeder
    # Einheit: Es zählt mit, damit sie als unvollständig gilt. Ausgeschlossene nicht.
    models = [m.key for m in project.config.panel if m.key not in basis.excluded]
    if only is not None:
        models = [m for m in models if m in only]
    by_item: dict[str, list[CallRecord]] = {}
    for model in (m for m in models if m in basis.runs):
        runs = basis.runs[model]
        for call in project.store.calls(model_key=model, config_hash=runs, with_raw=False):
            by_item.setdefault(call.item_id, []).append(call)
    settings = basis.settings
    return {
        item_id: aggregate(
            item_id,
            by_item.get(item_id, []),
            models=models,
            mode=settings["mode"],
            k=settings["k"],
            n_codes=settings["n_codes"],
            abstain_id=settings["abstain_id"],
        )
        for item_id in item_ids
    }
