"""Anbieter-Registry: Provider aus der Projektkonfiguration bauen, API-Keys im OS-Schlüsselbund.

Keys stehen nie in project.json, im Audit-Log oder in Exporten.
"""

from __future__ import annotations

import re

import httpx
import keyring
import keyring.errors

from llmanno.core.config import Capabilities, ProviderConfig
from llmanno.core.providers.base import (
    ChatRequest,
    ModelInfo,
    OutputMode,
    Provider,
    ProviderError,
)
from llmanno.core.providers.ollama import OllamaProvider
from llmanno.core.providers.openai_compat import OpenAICompatProvider
from llmanno.core.schema import load_json

KEYRING_SERVICE = "llmanno"
# Darunter ist der Evidenz-Repair beim Übernehmen ins Panel aus: Er kostet einen zweiten
# Aufruf und hilft bei kleinen Modellen selten (Abnahmelauf mit qwen2.5vl:3b).
SMALL_MODEL_BILLIONS = 10
_SIZE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*([KMBT])", re.I)
_SCALE = {"K": 1e-6, "M": 1e-3, "B": 1, "T": 1e3}


def parameter_billions(info: ModelInfo | None) -> float | None:
    """Parameterzahl in Milliarden aus den Modell-Details (Ollama: „3.8B“), sonst None."""
    raw = str((info.details if info else {}).get("parameter_size") or "")
    if not (match := _SIZE.search(raw)):
        return None
    return float(match.group(1)) * _SCALE[match.group(2).upper()]


def get_api_key(config: ProviderConfig) -> str | None:
    return keyring.get_password(KEYRING_SERVICE, config.base_url)


def set_api_key(config: ProviderConfig, key: str) -> None:
    keyring.set_password(KEYRING_SERVICE, config.base_url, key)


def delete_api_key(config: ProviderConfig) -> bool:
    """Entfernt den Key aus dem Schlüsselbund; False, wenn keiner hinterlegt war."""
    try:
        keyring.delete_password(KEYRING_SERVICE, config.base_url)
    except keyring.errors.PasswordDeleteError:
        return False
    return True


def make_provider(config: ProviderConfig) -> Provider:
    if config.kind == "ollama":
        return OllamaProvider(config.base_url, timeout=config.timeout_s)
    key = get_api_key(config) if config.auth != "none" else None
    if config.auth != "none" and not key:
        raise ProviderError(
            f"Kein API-Key für „{config.name}“ hinterlegt. Im Tab Modelle eintragen und „Im "
            "Schlüsselbund speichern“.",
            retryable=False,
        )
    if config.kind == "anthropic":  # optionales Extra „harness“, nur dann importieren
        from llmanno.core.providers.anthropic import AnthropicProvider

        return AnthropicProvider(api_key=key, timeout=config.timeout_s)
    caps = config.capabilities
    # Ungeprüft heißt: das Beste versuchen. Der Fähigkeits-Test korrigiert das vor dem Lauf.
    if caps.json_schema is not False:
        mode = "json_schema"
    elif caps.json_object:
        mode = "json_object"
    else:
        mode = "text"
    return OpenAICompatProvider(
        config.base_url,
        api_key=key,
        auth=config.auth,
        output_mode=mode,
        send_seed=caps.seed is not False,
        timeout=config.timeout_s,
    )


_PROBE_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


def probe(
    config: ProviderConfig, model: str, *, transport: httpx.BaseTransport | None = None
) -> tuple[Capabilities, list[str]]:
    """Prüft mit Mini-Anfragen, was der Anbieter kann. Liefert Fähigkeiten und Hinweise."""
    if config.kind == "ollama":
        # Structured Outputs und seed kann Ollama seit 0.5 für jedes Modell.
        return Capabilities(json_schema=True, json_object=True, seed=True), []

    key = get_api_key(config) if config.auth != "none" else None
    notes: list[str] = []

    def works(mode: OutputMode, seed: int | None) -> bool:
        provider = OpenAICompatProvider(
            config.base_url, api_key=key, auth=config.auth, output_mode=mode,
            send_seed=seed is not None, timeout=config.timeout_s, transport=transport,
        )  # fmt: skip
        request = ChatRequest(
            model=model,
            messages=[{"role": "user", "content": 'Antworte nur mit {"ok": true}.'}],
            temperature=None,  # GPT-5 und die o-Reihe lehnen jede eigene Temperatur ab
            # Reasoning-Modelle (etwa gpt-oss) verbrauchen Tokens fürs Denken; bei zu
            # knappem Limit bliebe die Antwort leer und die Fähigkeit gälte als fehlend.
            max_tokens=1024,
            seed=seed,
            schema=_PROBE_SCHEMA,
        )
        try:
            response = provider.chat(request)
        except ProviderError as exc:
            if exc.retryable:
                raise
            notes.append(f"{mode}{' + seed' if seed is not None else ''}: {exc}")
            return False
        finally:
            provider.close()
        if response.finish_reason == "length":
            notes.append(f"{mode}: Antwort abgeschnitten (max_tokens; Reasoning-Modell?)")
        try:
            return load_json(response.text).get("ok") is True
        except (ValueError, AttributeError):
            notes.append(f"{mode}: Antwort war kein passendes JSON: {response.text[:200]!r}")
            return False

    json_schema = works("json_schema", None)
    json_object = None if json_schema else works("json_object", None)
    best: OutputMode = "json_schema" if json_schema else "json_object" if json_object else "text"
    seed = works(best, 1)
    return Capabilities(json_schema=json_schema, json_object=json_object, seed=seed), notes


# Denkende Modelle (Reasoning) verbrauchen Token fürs Nachdenken; mit 512 bräche die Antwort ab.
THINKING_TOKENS = 4096
_THINKING = re.compile(
    r"gpt-?oss|gpt-?5|(?<![a-z0-9])o[134](?![0-9])|qwen-?3|qwq|deepseek-?r1|magistral|thinking"
    r"|reason"
)


def is_thinking_model(name: str, capabilities: tuple[str, ...] = ()) -> bool:
    """Am Namen (auch „OpenAI GPT OSS 120B“) oder an Ollamas Fähigkeit „thinking“ erkennbar."""
    normalized = re.sub(r"[\s_]+", "-", name.lower())
    return _THINKING.search(normalized) is not None or "thinking" in capabilities
