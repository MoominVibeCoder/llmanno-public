"""Projektkonfiguration (project.json). Alles, was das Messinstrument festlegt, steht hier."""

from __future__ import annotations

import ipaddress
import json
from datetime import UTC, datetime
from typing import Literal, Self
from urllib.parse import urlparse

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

CONFIG_VERSION = 1


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ColumnMapping(_Model):
    id: str | None = None
    text: list[str] = Field(min_length=1)
    group: str | None = None
    order: str | None = None
    speaker: str | None = None
    meta: list[str] = []
    gold: list[str] = []
    # Analyse-Variablen: für Subgruppen und den Messfehler-Test, nie im Prompt (etwa
    # Personenmerkmale, die das Modell nicht sehen soll).
    analysis: list[str] = []
    consensus: str | None = None

    def columns(self) -> list[str]:
        single = [self.id, self.group, self.order, self.speaker, self.consensus]
        return [c for c in single if c] + self.text + self.meta + self.gold + self.analysis


class DataConfig(_Model):
    source_name: str
    sheet: str
    mapping: ColumnMapping
    file_sha256: str
    units_sha256: str  # Lauf-Identität; ältere Projekte: mit Gold gerechnet
    n_units: int
    # Prüfsumme ohne Gold, falls units_sha256 anders gerechnet ist (ältere Projekte nach
    # Project.update_gold); der Rerun erkennt die Daten daran wieder.
    content_sha256: str | None = None


class Code(_Model):
    id: str = Field(pattern=r"^[A-Za-z0-9_.\-]+$")
    label: str = ""
    rank: int | None = None
    parent: str | None = None  # Code-Gruppe (Oberkategorie); nur für die Auswertung
    active: bool = True

    @field_validator("parent")
    @classmethod
    def _blank_parent(cls, value: str | None) -> str | None:
        return (value.strip() or None) if value else None


class CodebookConfig(_Model):
    source_name: str
    sha256: str
    encoding: str
    scale: Literal["nominal", "ordinal"] = "nominal"
    codes: list[Code] = []
    abstain: bool = True
    abstain_id: str = Field(default="KEIN_CODE", pattern=r"^[A-Za-z0-9_.\-]+$")
    abstain_label: str = "Kein Code des Codebuchs trifft zu oder der Text ist nicht kodierbar."

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        ids = [c.id for c in self.codes]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"Doppelte Code-Kürzel: {', '.join(duplicates)}")
        if self.abstain and self.abstain_id in ids:
            raise ValueError(
                f"Kürzel der Restkategorie kollidiert mit einem Code: {self.abstain_id}"
            )
        clashes = sorted({c.parent for c in self.codes if c.parent} & {*ids, self.abstain_id})
        if clashes:
            raise ValueError(
                f"Gruppenname gleicht einem Code-Kürzel: {', '.join(clashes)}. Gruppen "
                "brauchen eigene Namen."
            )
        if self.scale == "ordinal" and any(c.rank is None for c in self.codes if c.active):
            raise ValueError("Bei ordinalem Skalenniveau braucht jeder aktive Code einen Rang.")
        return self

    def active_codes(self) -> list[Code]:
        return [c for c in self.codes if c.active]

    def allowed_ids(self) -> list[str]:
        ids = [c.id for c in self.active_codes()]
        return ids + [self.abstain_id] if self.abstain else ids

    def groups(self) -> dict[str, str]:
        """Code → Code-Gruppe für die Auswertung auf Ebene der Oberkategorien. Codes ohne
        Gruppe und die Restkategorie stehen für sich; leer, wenn keine Gruppe gesetzt ist."""
        if not any(c.parent for c in self.active_codes()):
            return {}
        groups = {c.id: c.parent or c.id for c in self.active_codes()}
        if self.abstain:
            groups[self.abstain_id] = self.abstain_id
        return groups


# Reasoning-Aufwand, der schon niedrig ist (gpt-oss: low/medium/high; GPT-5: minimal bis high)
LOW_EFFORTS = frozenset({"low", "minimal", "none"})


class UnitSettings(_Model):
    mode: Literal["isolated", "context", "holistic"] = "isolated"
    n_before: int = Field(default=2, ge=0, le=20)
    n_after: int = Field(default=1, ge=0, le=20)
    # Spalte „kodieren ja/nein“: Zeilen mit nein (leer, 0, nein, no, false) stehen nur als
    # Kontext im Prompt, etwa Züge der Lehrkraft. None: jede Zeile wird kodiert.
    target_column: str | None = None


class Example(_Model):
    """Beispiel aus Dev für den Prompt: Einheit und ihre Gold-Codes beim Ziehen."""

    item_id: str
    codes: list[str]


class CodingConfig(_Model):
    units: UnitSettings = UnitSettings()
    n_runs: int = Field(default=5, ge=1, le=20)
    adaptive: bool = True
    adaptive_min_agree: int = Field(default=2, ge=2, le=20)
    base_seed: int = 1
    permute_codes: bool = True
    max_signal_words: int = Field(default=3, ge=1, le=10)
    max_rationale_words: int = Field(default=40, ge=10, le=200)
    # Ein Repair-Versuch, wenn Signalwörter nicht im Text stehen. Er darf nur die Belege ändern,
    # nicht die Entscheidung. Kostet bei kleinen Modellen viel und hilft dort selten.
    evidence_repair: bool = True
    # Namen vor dem Senden ersetzen: Prüfsumme der Liste im Projektordner (core.pseudonyms).
    # Die Liste selbst steht nicht hier, weil project.json ins Replikationspaket geht.
    pseudonyms_sha256: str | None = None
    # Beispiele aus Dev mit Gold im Prompt (Few-Shot, core.examples); leer = aus. Die Codes
    # stehen hier, damit spätere Gold-Änderungen den Prompt nicht still ändern. Beispiel-
    # Einheiten zählen in keiner Auswertung.
    examples: list[Example] = []


class Capabilities(_Model):
    """Vom Fähigkeits-Test ermittelt; None = noch nicht geprüft."""

    json_schema: bool | None = None
    json_object: bool | None = None
    seed: bool | None = None


class ProviderConfig(_Model):
    key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: Literal["ollama", "openai", "anthropic"]
    base_url: str = Field(min_length=1)
    auth: Literal["none", "bearer", "api-key"] = "none"
    privacy: Literal["local", "academic", "external"] = "external"
    rate_limit_per_min: int | None = Field(default=None, ge=1)
    timeout_s: float = Field(default=300, gt=0)
    # Gleichzeitige Anfragen je Modell. Gehört nicht zur Lauf-Identität (wie N): Es ändert
    # die Reihenfolge der Anfragen, nicht ihren Inhalt.
    parallel: int = Field(default=1, ge=1, le=32)
    capabilities: Capabilities = Capabilities()

    @property
    def on_this_machine(self) -> bool:
        url = self.base_url if "://" in self.base_url else f"//{self.base_url}"
        host = (urlparse(url).hostname or "").lower()
        if host == "localhost" or host.endswith(".localhost"):
            return True
        try:  # nur echte Adressen: „127.example.org“ ist ein Rechner im Netz
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    @property
    def privacy_level(self) -> Literal["local", "academic", "external"]:
        """Wie die Daten wirklich reisen: „lokal“ nur, wenn die Adresse dieser Rechner ist.

        Ein Ollama-Eintrag mit der Adresse eines anderen Rechners bleibt sonst „lokal“: kein
        Zustimmungsdialog, und der Report meldet „lokal“."""
        if self.privacy == "local" and not self.on_this_machine:
            return "external"
        return self.privacy

    @property
    def key_in_clear(self) -> bool:
        """Ein API-Schlüssel ginge unverschlüsselt (http) an einen anderen Rechner."""
        return (
            self.auth != "none" and not self.on_this_machine and self.base_url.startswith("http://")
        )


class ModelConfig(_Model):
    provider: str
    model: str = Field(min_length=1)
    temperature: float | None = Field(default=None, ge=0, le=2)  # None = automatisch
    max_tokens: int = Field(default=512, ge=64, le=32768)
    thinking: bool | None = None  # None = Modell kann kein Thinking, Parameter wird nicht gesendet
    # Manche Anbieter ignorieren die Temperatur (etwa gpt-oss bei KI:connect.NRW). Dann
    # nicht senden und im Report die Anbietervorgabe nennen statt einer falschen Zahl.
    provider_temperature: bool = False
    # reasoning_effort für Reasoning-Modelle (gpt-oss, GPT-5, Qwen). Die Stufen hängen vom
    # Anbieter ab (low/medium/high, xhigh, minimal); None = nicht senden.
    reasoning_effort: str | None = Field(default=None, min_length=1, max_length=20)

    @property
    def key(self) -> str:
        return f"{self.provider}/{self.model}"

    def effective_temperature(self, n_runs: int) -> float | None:
        # Self-Consistency braucht Streuung; ein Einzellauf bleibt greedy.
        if self.provider_temperature:
            return None
        if self.temperature is not None:
            return self.temperature
        return 0.7 if n_runs > 1 else 0.0


OLLAMA_LOCAL = ProviderConfig(
    key="ollama",
    name="Ollama (lokal)",
    kind="ollama",
    base_url="http://localhost:11434",
    privacy="local",
    capabilities=Capabilities(json_schema=True, json_object=True, seed=True),
)

GWDG_CHAT_AI = ProviderConfig(
    key="gwdg",
    name="GWDG/KISSKI Chat AI",
    kind="openai",
    base_url="https://chat-ai.academiccloud.de/v1",
    auth="bearer",
    privacy="academic",
)

KI_CONNECT_NRW = ProviderConfig(
    key="kiconnect",
    name="KI:connect.NRW",
    kind="openai",
    base_url="https://chat.kiconnect.nrw/api/v1",
    auth="bearer",
    privacy="academic",
)


class GoldConfig(_Model):
    """Wie Zellen der Gold-Spalten gelesen werden."""

    separator: str = Field(default=";", min_length=1)  # Multi-Label: mehrere Codes je Zelle
    value_map: dict[str, str | None] = {}  # Rohwert → Code; None = Wert ignorieren
    epsilon: float = Field(default=0.2, ge=0, le=0.5)  # Alt-Test: 0.2 Expert:innen, 0.1 Crowd


class SplitConfig(_Model):
    """Dev/Test-Aufteilung der Einheiten mit Gold. Dev zum Schärfen, Test bleibt bis zum
    Freeze gesperrt."""

    dev_share: float = Field(default=0.25, gt=0, lt=1)
    seed: int = 1
    stratify: bool = True  # nach dem Referenzcode schichten (nur bei Ziehung je Einheit)
    reference: str | None = None  # Gold-Spalte für die Schichtung; None = Konsens bzw. erste
    # Isoliert kodiert: Einheiten einer Gruppe als unabhängig behandeln (weder gruppenweiser
    # Split noch Cluster-Bootstrap). Nur nötig, um die Fehlerrate je Gruppe zu testen.
    independent_units: bool = False


class GateCalibration(_Model):
    """Isotone Abbildung Self-Consistency → geschätzte Trefferquote, gelernt auf Gold (mit
    Split nur Dev)."""

    xs: list[float]
    fitted: list[float]
    n: int  # Einheiten, auf denen sie gelernt wurde


class ReviewConfig(_Model):
    """Review-Queue (PLAN 3.9): unsichere Fälle per Gate plus eine Zufallsstichprobe, deren
    Ziehwahrscheinlichkeit protokolliert wird."""

    tau: float = Field(default=0.6, ge=0, le=1)  # Gate: Self-Consistency des Codes darunter
    # Gesetzt: τ gilt für die geschätzte Trefferquote. Die Abbildung wird beim Einschalten
    # gelernt und gehört zum Instrument (review.gate_calibration).
    calibration: GateCalibration | None = None
    gate_low_confidence: bool = True
    gate_dissent: bool = True
    gate_abstain: bool = True
    gate_unsupported: bool = True
    gate_invalid: bool = True
    random_share: float = Field(default=0.1, ge=0, le=1)
    seed: int = 1


class FreezeState(_Model):
    """Eingefrorenes Instrument. `fingerprint` lässt sich ohne Anbieter prüfen; die
    Modell-Hashes enthalten zusätzlich die Digests und werden beim Laufstart verglichen."""

    instrument_hash: str
    fingerprint: str
    model_hashes: dict[str, str]
    split_sha256: str | None
    frozen_at: datetime
    # Gruppenweise ausgewertet (Split und Bootstrap)? Beim Freeze festgehalten, damit die
    # Test-Auswertung dem Analyseplan folgt. None: vor dieser Angabe eingefroren.
    grouped: bool | None = None


class ProjectConfig(_Model):
    config_version: int = CONFIG_VERSION
    name: str = Field(min_length=1)
    created: datetime = Field(default_factory=lambda: datetime.now(UTC))
    prompt_language: Literal["de", "en"] = "de"
    mode: Literal["single", "multi"] = "single"
    k: int = Field(default=3, ge=1, le=3)
    data: DataConfig | None = None
    codebook: CodebookConfig | None = None
    coding: CodingConfig = CodingConfig()
    gold: GoldConfig = GoldConfig()
    split: SplitConfig = SplitConfig()
    review: ReviewConfig = ReviewConfig()
    freeze: FreezeState | None = None
    providers: list[ProviderConfig] = Field(default_factory=lambda: [OLLAMA_LOCAL])
    panel: list[ModelConfig] = []

    @model_validator(mode="after")
    def _consistent_references(self) -> Self:
        keys = [p.key for p in self.providers]
        if len(keys) != len(set(keys)):
            raise ValueError("Anbieter-Schlüssel müssen eindeutig sein.")
        for model in self.panel:
            if model.provider not in keys:
                raise ValueError(f"Unbekannter Anbieter im Panel: {model.provider}")
        panel_keys = [m.key for m in self.panel]
        if len(panel_keys) != len(set(panel_keys)):
            raise ValueError("Jedes Modell darf nur einmal im Panel stehen.")
        return self

    def provider(self, key: str) -> ProviderConfig:
        return next(p for p in self.providers if p.key == key)


def read_config(text: str, source: str = "project.json") -> ProjectConfig:
    """project.json lesen; Fehler als Satz statt als Pydantic-Ausgabe."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{source} ist beschädigt (kein gültiges JSON, Zeile {exc.lineno})."
        ) from exc
    if not isinstance(data, dict):
        raise ValueError(f"{source} ist keine llmanno-Projektdatei.")
    version = data.get("config_version")
    if isinstance(version, int) and version > CONFIG_VERSION:
        raise ValueError(
            f"Das Projekt stammt aus einer neueren llmanno-Version (Projektversion {version}, "
            f"diese Version kennt {CONFIG_VERSION}). Bitte llmanno aktualisieren."
        )
    if version != CONFIG_VERSION:
        raise ValueError(
            f"Projektversion {version} wird nicht unterstützt (erwartet {CONFIG_VERSION})."
        )
    try:
        return ProjectConfig.model_validate(data)
    except ValidationError as exc:
        errors = exc.errors()
        unknown = [".".join(map(str, e["loc"])) for e in errors if e["type"] == "extra_forbidden"]
        if unknown:
            raise ValueError(
                f"{source} enthält Einstellungen, die diese llmanno-Version nicht kennt "
                f"({', '.join(unknown[:5])}). Vermutlich wurde das Projekt mit einer neueren "
                "Version gespeichert; bitte llmanno aktualisieren."
            ) from exc
        problems = "; ".join(
            f"{'.'.join(map(str, e['loc']))}: {e['msg'].removeprefix('Value error, ')}"
            for e in errors[:3]
        )
        raise ValueError(f"{source} ist ungültig ({problems}).") from exc
