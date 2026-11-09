"""Ollama über die native API (/api/chat).

Nicht über /v1, weil nur die native API num_ctx, format (JSON-Schema), think, keep_alive und
die Modell-Digests zugänglich macht.
"""

from __future__ import annotations

import time

import httpx

from llmanno.core.providers.base import ChatRequest, ChatResponse, ModelInfo, json_body, send

KEEP_ALIVE = "30m"  # Modell bleibt zwischen den Aufrufen eines Laufs geladen


class OllamaProvider:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 300,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout, transport=transport
        )

    def chat(self, request: ChatRequest) -> ChatResponse:
        options: dict[str, float | int] = {"num_predict": request.max_tokens}
        if request.temperature is not None:
            options["temperature"] = request.temperature
        if request.seed is not None:
            options["seed"] = request.seed
        if request.num_ctx:
            options["num_ctx"] = request.num_ctx
        body: dict[str, object] = {
            "model": request.model,
            "messages": request.messages,
            "stream": False,
            "options": options,
            "keep_alive": KEEP_ALIVE,
        }
        if request.schema is not None:
            body["format"] = request.schema
        if request.think is not None:
            body["think"] = request.think

        started = time.perf_counter()
        data = json_body(send(self._client, "POST", "/api/chat", json=body))
        message = data.get("message") or {}
        extra = {"thinking": message["thinking"]} if message.get("thinking") else {}
        return ChatResponse(
            text=message.get("content") or "",
            model=data.get("model"),
            tokens_in=data.get("prompt_eval_count"),
            tokens_out=data.get("eval_count"),
            latency_s=time.perf_counter() - started,
            output_mode="json_schema" if request.schema is not None else "text",
            finish_reason=data.get("done_reason"),
            extra=extra,
        )

    def list_models(self) -> list[ModelInfo]:
        data = json_body(send(self._client, "GET", "/api/tags"))
        models = []
        for entry in data.get("models", []):
            details = entry.get("details") or {}
            models.append(
                ModelInfo(
                    name=entry["name"],
                    digest=entry.get("digest"),
                    context_length=details.get("context_length"),
                    capabilities=tuple(entry.get("capabilities") or ()),
                    details=details,
                )
            )
        return models

    def show(self, name: str) -> ModelInfo:
        """Details eines Modells. Ältere Ollama-Versionen liefern Kontextlänge und Fähigkeiten
        nur hier, nicht in /api/tags."""
        data = json_body(send(self._client, "POST", "/api/show", json={"model": name}))
        model_info = data.get("model_info") or {}
        context = next((v for k, v in model_info.items() if k.endswith(".context_length")), None)
        return ModelInfo(
            name=name,
            digest=None,
            context_length=context,
            capabilities=tuple(data.get("capabilities") or ()),
            details=data.get("details") or {},
        )

    def version(self) -> str | None:
        return json_body(send(self._client, "GET", "/api/version")).get("version")

    def close(self) -> None:
        self._client.close()
