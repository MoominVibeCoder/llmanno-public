"""Anthropic-API (Claude) für den Harness-Arm, mit Kostenbuch und harter Grenze.

Das SDK `anthropic` ist ein optionales Extra (`uv sync --extra harness`) und nicht in der Exe.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import anthropic

from llmanno.core.providers.base import ChatRequest, ChatResponse, ModelInfo, ProviderError

BASE_URL = "https://api.anthropic.com"
# USD je Mio. Token: Eingabe, Ausgabe, Lesen aus dem Cache. Schreiben in den Cache kostet
# das 1,25-Fache der Eingabe (5 Minuten). Stand 2026-10-28.
PRICES = {
    "claude-sonnet-5-5": (2.0, 10.0, 0.20),
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-fable-5-1": (10.0, 50.0, 0.25),
}
CACHE_WRITE = 1.25
LEDGER = Path.home() / ".llmanno" / "anthropic_kosten.json"
DEFAULT_LIMIT_USD = 90.0  # Rest vom 100-$-Guthaben als Puffer für Schätzfehler


def cost_usd(
    model: str, tokens_in: int, tokens_out: int, cache_read: int, cache_write: int
) -> float:
    if model not in PRICES:
        raise ProviderError(f"Kein Preis für {model} hinterlegt (PRICES).", retryable=False)
    price_in, price_out, price_read = PRICES[model]
    return (
        tokens_in * price_in
        + cache_write * price_in * CACHE_WRITE
        + cache_read * price_read
        + tokens_out * price_out
    ) / 1e6


class Budget:
    """Kostenbuch über alle Läufe hinweg als JSON-Datei: ausgegeben und Grenze in USD."""

    def __init__(self, path: Path = LEDGER, limit_usd: float = DEFAULT_LIMIT_USD) -> None:
        self.path = path
        self._lock = threading.Lock()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self.spent, self.limit = float(data["spent_usd"]), float(data["limit_usd"])
        else:
            self.spent, self.limit = 0.0, limit_usd

    def check(self, worst_case: float) -> None:
        if self.spent + worst_case > self.limit:
            raise ProviderError(
                f"Kostengrenze: ausgegeben {self.spent:.2f} $, Grenze {self.limit:.2f} $, dieser "
                f"Aufruf könnte bis {worst_case:.2f} $ kosten. Grenze in {self.path} anpassen.",
                retryable=False,
            )

    def charge(self, amount: float) -> None:
        with self._lock:  # die Engine ruft parallel auf (ProviderConfig.parallel)
            self.spent += amount
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({"spent_usd": round(self.spent, 6), "limit_usd": self.limit}),
                encoding="utf-8",
            )
            tmp.replace(self.path)


class AnthropicProvider:
    def __init__(
        self,
        *,
        api_key: str | None,
        budget: Budget | None = None,
        timeout: float = 300,
        client: Any = None,
    ) -> None:
        # Wiederholen entscheidet die Engine (Engine._chat), nicht das SDK.
        self._client = client or anthropic.Anthropic(
            api_key=api_key, timeout=timeout, max_retries=0
        )
        self.budget = budget or Budget()

    def _params(self, request: ChatRequest) -> dict[str, Any]:
        if request.temperature is not None:
            # Wie bei OpenAI-Modellen mit fester Temperatur: still weglassen hieße, dass Bericht
            # und Protokoll eine Temperatur nennen, die nie galt.
            raise ProviderError(
                "Claude Sonnet 5.5 und Opus 5.5 lehnen eine eigene Temperatur ab. Im Tab Modelle "
                "in der Spalte Temperatur „nicht senden“ wählen.",
                retryable=False,
            )
        system = "\n\n".join(m["content"] for m in request.messages if m["role"] == "system")
        params: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "messages": [m for m in request.messages if m["role"] != "system"],
        }
        if system:  # Codebuch und Anweisungen: gleich in jedem Aufruf, also aus dem Cache
            params["system"] = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]
        output: dict[str, Any] = {}
        if request.schema is not None:
            output["format"] = {"type": "json_schema", "schema": request.schema}
        if request.reasoning_effort is not None:
            output["effort"] = request.reasoning_effort
        if output:
            params["output_config"] = output
        return params

    def chat(self, request: ChatRequest) -> ChatResponse:
        params = self._params(request)
        # Obergrenze ohne Cache: Eingabe 3 Zeichen je Token (vorsichtig), Ausgabe max_tokens.
        chars = len(json.dumps(params.get("system", ""))) + len(json.dumps(params["messages"]))
        self.budget.check(cost_usd(request.model, chars // 3, request.max_tokens, 0, 0))
        started = time.perf_counter()
        try:
            with self._client.messages.stream(**params) as stream:
                message = stream.get_final_message()
        except anthropic.RateLimitError as exc:
            raise ProviderError(
                f"HTTP 429: {exc.message}", retryable=True, retry_after=_retry_after(exc),
                status=429,
            ) from exc  # fmt: skip
        except anthropic.APIStatusError as exc:
            raise ProviderError(
                f"HTTP {exc.status_code}: {exc.message}",
                # Überlastung kommt im Stream auch mit Status 200 („overloaded_error“)
                retryable=exc.status_code >= 500 or "overloaded" in str(exc.message).lower(),
                retry_after=_retry_after(exc),
                status=exc.status_code,
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError(
                f"Verbindung zu {BASE_URL} fehlgeschlagen: {exc}", retryable=True
            ) from exc
        usage = message.usage
        cache_read = usage.cache_read_input_tokens or 0
        cache_write = usage.cache_creation_input_tokens or 0
        cost = cost_usd(
            request.model, usage.input_tokens, usage.output_tokens, cache_read, cache_write
        )
        self.budget.charge(cost)
        stop = {"max_tokens": "length"}.get(message.stop_reason, message.stop_reason)
        return ChatResponse(
            text="".join(b.text for b in message.content if b.type == "text"),
            model=message.model,
            # Wie bei OpenAI: Eingabe gesamt, der Cache-Anteil steht in extra
            tokens_in=usage.input_tokens + cache_read + cache_write,
            tokens_out=usage.output_tokens,
            latency_s=time.perf_counter() - started,
            output_mode="json_schema" if request.schema is not None else "text",
            finish_reason=stop,
            extra={
                "tokens_cached": cache_read,
                "tokens_cache_write": cache_write,
                "cost_usd": cost,
            },
        )

    def list_models(self) -> list[ModelInfo]:
        return sorted(
            (ModelInfo(name=m.id) for m in self._client.models.list()), key=lambda m: m.name
        )

    def close(self) -> None:
        self._client.close()


def _retry_after(exc: anthropic.APIStatusError) -> float | None:
    try:
        return float(exc.response.headers.get("retry-after", ""))
    except ValueError:
        return None
