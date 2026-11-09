"""Gemeinsame Schnittstelle der LLM-Anbieter."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx

OutputMode = Literal["json_schema", "json_object", "text"]


@dataclass(frozen=True)
class ChatRequest:
    model: str
    messages: list[dict[str, str]]
    temperature: float | None  # None = nicht senden, der Anbieter legt sie fest
    max_tokens: int
    seed: int | None = None
    schema: dict[str, Any] | None = None
    num_ctx: int | None = None  # nur Ollama
    think: bool | None = None  # nur Ollama; None = Parameter nicht senden
    reasoning_effort: str | None = None  # nur OpenAI-kompatibel; None = nicht senden


@dataclass(frozen=True)
class ChatResponse:
    text: str
    model: str | None  # vom Server gemeldet, kann vom angefragten Namen abweichen
    tokens_in: int | None
    tokens_out: int | None
    latency_s: float
    output_mode: OutputMode
    finish_reason: str | None  # "length" heißt: Ausgabe bei max_tokens abgeschnitten
    extra: dict[str, Any] = field(default_factory=dict)
    # Restkontingent laut Kopfzeilen: Zeitfenster → (verbleibend, Grenze); siehe quota()
    quota: dict[str, tuple[int, int | None]] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelInfo:
    name: str
    digest: str | None = None
    context_length: int | None = None
    capabilities: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)


class ProviderError(Exception):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        retry_after: float | None = None,
        status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after
        self.status = status  # HTTP-Status, falls es einer war


class Provider(Protocol):
    def chat(self, request: ChatRequest) -> ChatResponse: ...

    def list_models(self) -> list[ModelInfo]: ...

    def close(self) -> None: ...


def send(client: httpx.Client, method: str, url: str, **kwargs: Any) -> httpx.Response:
    """HTTP-Aufruf mit einheitlicher Fehlerabbildung; Wiederholen entscheidet der Aufrufer."""
    base = client.base_url  # ohne Zugangsdaten in der Adresse (user:passwort@host)
    port = f":{base.port}" if base.port else ""
    where = f"{base.scheme}://{base.host}{port}{base.path.rstrip('/')}{url}"
    try:
        response = client.request(method, url, **kwargs)
    except httpx.TimeoutException as exc:
        raise ProviderError(
            f"Zeitüberschreitung: {where} hat nicht innerhalb des Zeitlimits geantwortet.",
            retryable=True,
        ) from exc
    except httpx.TransportError as exc:
        raise ProviderError(
            f"Verbindung fehlgeschlagen: {where} ist nicht erreichbar ({exc}).", retryable=True
        ) from exc
    if response.is_success:
        return response
    retry_after = None
    header = response.headers.get("retry-after")
    if header is None and response.status_code == 429:
        header = response.headers.get("ratelimit-reset")  # GWDG: Sekunden bis zum neuen Fenster
    if header:
        try:
            retry_after = float(header)
        except ValueError:
            pass
    status = response.status_code
    raise ProviderError(
        f"HTTP {status}: {response.text[:500]}",
        retryable=status == 429 or status >= 500,
        retry_after=retry_after,
        status=status,
    )


_QUOTA = re.compile(r"x-ratelimit-(limit|remaining)-([a-z]+)")


def quota(headers: Mapping[str, str]) -> dict[str, tuple[int, int | None]]:
    """Restkontingent je Zeitfenster aus den Kopfzeilen: GWDG meldet minute, hour, day und
    month (Anfragen), OpenAI requests und tokens (je Minute)."""
    found: dict[str, dict[str, int]] = {}
    for name, value in headers.items():
        if match := _QUOTA.fullmatch(name.lower()):
            try:
                found.setdefault(match[2], {})[match[1]] = int(float(value))
            except (ValueError, OverflowError):  # auch „inf“
                continue
    return {w: (v["remaining"], v.get("limit")) for w, v in found.items() if "remaining" in v}


def json_body(response: httpx.Response) -> dict[str, Any]:
    """Antwort als JSON-Objekt; alles andere (HTML-Fehlerseite eines Proxys, abgeschnittene
    Antwort) wird ein ProviderError, damit der Aufruf als Fehler protokolliert wird."""
    try:
        data = response.json()
    except ValueError as exc:
        raise ProviderError(
            f"Antwort ist kein JSON: {response.text[:200]!r}", retryable=True
        ) from exc
    if not isinstance(data, dict):
        raise ProviderError(f"Unerwartete Antwort: {str(data)[:200]}", retryable=False)
    return data
