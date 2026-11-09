import json

import httpx
import pytest

from llmanno.core import providers
from llmanno.core.config import GWDG_CHAT_AI, OLLAMA_LOCAL, Capabilities
from llmanno.core.providers import make_provider, probe
from llmanno.core.providers.base import ChatRequest, ProviderError
from llmanno.core.providers.ollama import OllamaProvider
from llmanno.core.providers.openai_compat import OpenAICompatProvider

SCHEMA = {"type": "object"}
REQUEST = ChatRequest(
    model="m", messages=[{"role": "user", "content": "hi"}], temperature=0.7, max_tokens=300,
    seed=3, schema=SCHEMA, num_ctx=8192, think=False,
)  # fmt: skip


def transport(handler, seen):
    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return httpx.MockTransport(wrapped)


def test_ollama_chat_sends_native_options_and_maps_response():
    seen = []
    reply = {
        "model": "m", "message": {"content": '{"a": 1}', "thinking": "hmm"},
        "prompt_eval_count": 120, "eval_count": 30, "done_reason": "stop",
    }  # fmt: skip
    provider = OllamaProvider(
        "http://ollama:11434/", transport=transport(lambda r: httpx.Response(200, json=reply), seen)
    )
    response = provider.chat(REQUEST)
    body = json.loads(seen[0].content)
    assert seen[0].url == "http://ollama:11434/api/chat"
    assert body["options"] == {"temperature": 0.7, "num_predict": 300, "seed": 3, "num_ctx": 8192}
    assert (body["format"], body["think"], body["stream"]) == (SCHEMA, False, False)
    assert (response.text, response.tokens_in, response.tokens_out) == ('{"a": 1}', 120, 30)
    assert response.output_mode == "json_schema" and response.extra == {"thinking": "hmm"}


def test_ollama_omits_unset_parameters_and_lists_models():
    seen = []
    tags = {
        "models": [
            {
                "name": "qwen:3b", "digest": "abc", "capabilities": ["completion"],
                "details": {"context_length": 32768, "quantization_level": "Q4_K_M"},
            }
        ]
    }  # fmt: skip

    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=tags)
        return httpx.Response(200, json={"message": {"content": "x"}})

    provider = OllamaProvider("http://o", transport=transport(handler, seen))
    provider.chat(ChatRequest(model="m", messages=[], temperature=0, max_tokens=64))
    body = json.loads(seen[0].content)
    assert "format" not in body and "think" not in body
    assert set(body["options"]) == {"temperature", "num_predict"}
    [info] = provider.list_models()
    assert (info.name, info.digest, info.context_length) == ("qwen:3b", "abc", 32768)


def test_openai_chat_with_json_schema_seed_and_bearer():
    seen = []
    reply = {
        "model": "llama-3.3-70b", "system_fingerprint": "fp1",
        "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 50, "completion_tokens": 5},
    }  # fmt: skip
    provider = OpenAICompatProvider(
        "https://api.example/v1",
        api_key="geheim",
        transport=transport(lambda r: httpx.Response(200, json=reply), seen),
    )
    response = provider.chat(REQUEST)
    body = json.loads(seen[0].content)
    assert seen[0].url == "https://api.example/v1/chat/completions"
    assert seen[0].headers["authorization"] == "Bearer geheim"
    assert body["response_format"]["json_schema"]["schema"] == SCHEMA
    assert body["seed"] == 3 and "num_ctx" not in body and "think" not in body
    assert (response.model, response.tokens_in, response.extra) == (
        "llama-3.3-70b", 50, {"system_fingerprint": "fp1"}
    )  # fmt: skip


@pytest.mark.parametrize("mode", ["json_object", "text"])
def test_openai_fallback_modes(mode):
    seen = []
    reply = {"choices": [{"message": {"content": "{}"}}]}
    provider = OpenAICompatProvider(
        "https://x/v1", auth="api-key", api_key="k", output_mode=mode, send_seed=False,
        transport=transport(lambda r: httpx.Response(200, json=reply), seen),
    )  # fmt: skip
    response = provider.chat(REQUEST)
    body = json.loads(seen[0].content)
    assert "seed" not in body and seen[0].headers["api-key"] == "k"
    assert body.get("response_format") == (
        {"type": "json_object"} if mode == "json_object" else None
    )
    assert response.output_mode == mode


@pytest.mark.parametrize(
    "status, retryable, retry_after", [(429, True, 7.0), (503, True, None), (400, False, None)]
)
def test_http_errors_are_classified(status, retryable, retry_after):
    headers = {"retry-after": "7"} if retry_after else {}
    provider = OpenAICompatProvider(
        "https://x/v1",
        transport=httpx.MockTransport(lambda r: httpx.Response(status, headers=headers, text="nö")),
    )
    with pytest.raises(ProviderError) as info:
        provider.chat(REQUEST)
    assert (info.value.retryable, info.value.retry_after) == (retryable, retry_after)
    assert f"HTTP {status}" in str(info.value)


def test_connection_errors_are_retryable():
    def fail(request):
        raise httpx.ConnectError("weg")

    provider = OllamaProvider("http://o", transport=httpx.MockTransport(fail))
    with pytest.raises(ProviderError) as info:
        provider.chat(REQUEST)
    assert info.value.retryable


def test_make_provider_requires_key_and_picks_output_mode(monkeypatch):
    monkeypatch.setattr(providers, "get_api_key", lambda config: None)
    assert isinstance(make_provider(OLLAMA_LOCAL), OllamaProvider)
    with pytest.raises(ProviderError, match="API-Key"):
        make_provider(GWDG_CHAT_AI)

    monkeypatch.setattr(providers, "get_api_key", lambda config: "k")
    unknown = make_provider(GWDG_CHAT_AI)
    assert (unknown.output_mode, unknown.send_seed) == ("json_schema", True)
    limited = make_provider(
        GWDG_CHAT_AI.model_copy(
            update={"capabilities": Capabilities(json_schema=False, json_object=True, seed=False)}
        )
    )
    assert (limited.output_mode, limited.send_seed) == ("json_object", False)


def test_probe_falls_back_and_collects_notes(monkeypatch):
    monkeypatch.setattr(providers, "get_api_key", lambda config: "k")

    def handler(request):
        body = json.loads(request.content)
        if body.get("response_format", {}).get("type") == "json_schema":
            return httpx.Response(400, text="response_format json_schema not supported")
        if "seed" in body:
            return httpx.Response(400, text="unknown parameter seed")
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]})

    caps, notes = probe(GWDG_CHAT_AI, "m", transport=httpx.MockTransport(handler))
    assert caps == Capabilities(json_schema=False, json_object=True, seed=False)
    assert len(notes) == 2 and "json_schema" in notes[0]


def test_probe_ollama_needs_no_requests():
    caps, notes = probe(OLLAMA_LOCAL, "m")
    assert caps.json_schema and caps.seed and notes == []
