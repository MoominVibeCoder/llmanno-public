"""OpenAI-kompatible Chat-Completions-API (GWDG/KISSKI, OpenAI, vLLM, LM Studio, …)."""

from __future__ import annotations

import time
from typing import Any, Literal

import httpx

from llmanno.core.providers.base import (
    ChatRequest,
    ChatResponse,
    ModelInfo,
    OutputMode,
    ProviderError,
    json_body,
    quota,
    send,
)


class OpenAICompatProvider:
    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        auth: Literal["none", "bearer", "api-key"] = "bearer",
        output_mode: OutputMode = "json_schema",
        send_seed: bool = True,
        timeout: float = 300,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {}
        if api_key and auth == "bearer":
            headers["Authorization"] = f"Bearer {api_key}"
        elif api_key and auth == "api-key":
            headers["api-key"] = api_key
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout, transport=transport
        )
        self.output_mode = output_mode
        self.send_seed = send_seed
        # OpenAIs Reasoning-Modelle (GPT-5, o-Reihe) lehnen max_tokens ab: max_completion_tokens
        self._tokens = "max_tokens"

    def _body(self, request: ChatRequest) -> tuple[dict[str, Any], OutputMode]:
        body: dict[str, Any] = {
            "model": request.model,
            "messages": request.messages,
            self._tokens: request.max_tokens,
        }
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.reasoning_effort is not None:
            body["reasoning_effort"] = request.reasoning_effort
        if self.send_seed and request.seed is not None:
            body["seed"] = request.seed
        mode: OutputMode = "text"
        if request.schema is not None and self.output_mode == "json_schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "coding", "schema": request.schema, "strict": True},
            }
            mode = "json_schema"
        elif request.schema is not None and self.output_mode == "json_object":
            body["response_format"] = {"type": "json_object"}
            mode = "json_object"
        return body, mode

    def _adapt(self, exc: ProviderError, request: ChatRequest) -> bool:
        """Abgelehnte Parameter: Der Token-Name wird einmal umgestellt (gleiche Bedeutung, die
        Messung ändert sich nicht). Eine abgelehnte Temperatur bekommt einen Hinweis; still
        weglassen hieße, dass Bericht und Protokoll eine Temperatur nennen, die nie galt."""
        text = str(exc)
        if exc.status != 400:
            return False
        if self._tokens == "max_tokens" and "max_completion_tokens" in text:
            self._tokens = "max_completion_tokens"
            return True
        if request.temperature is not None and "temperature" in text and "support" in text:
            raise ProviderError(
                "Das Modell erlaubt nur seine eigene Temperatur. Im Tab Modelle in der Spalte "
                f"Temperatur „nicht senden“ wählen (kleinster Wert). ({text})",
                retryable=False,
                status=400,
            ) from exc
        return False

    def chat(self, request: ChatRequest) -> ChatResponse:
        while True:
            body, mode = self._body(request)
            started = time.perf_counter()
            try:
                http = send(self._client, "POST", "/chat/completions", json=body)
                break
            except ProviderError as exc:
                if not self._adapt(exc, request):
                    raise
        data = json_body(http)
        choice = (data.get("choices") or [{}])[0]
        usage = data.get("usage") or {}
        extra = (
            {"system_fingerprint": data["system_fingerprint"]}
            if data.get("system_fingerprint")
            else {}
        )
        # Prefix-Cache (OpenAI, vLLM; DeepSeek eigener Name) und verborgenes Reasoning
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
        if cached is None:
            cached = usage.get("prompt_cache_hit_tokens")
        reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
        for key, value in (("tokens_cached", cached), ("tokens_reasoning", reasoning)):
            if isinstance(value, int):
                extra[key] = value
        return ChatResponse(
            text=(choice.get("message") or {}).get("content") or "",
            model=data.get("model"),
            tokens_in=usage.get("prompt_tokens"),
            tokens_out=usage.get("completion_tokens"),
            latency_s=time.perf_counter() - started,
            output_mode=mode,
            finish_reason=choice.get("finish_reason"),
            extra=extra,
            quota=quota(http.headers),
        )

    def list_models(self) -> list[ModelInfo]:
        data = json_body(send(self._client, "GET", "/models"))
        return sorted((ModelInfo(name=m["id"]) for m in data.get("data", [])), key=lambda m: m.name)

    def close(self) -> None:
        self._client.close()
