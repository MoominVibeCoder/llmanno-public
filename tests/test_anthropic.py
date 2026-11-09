from types import SimpleNamespace

import pytest

pytest.importorskip("anthropic")

from llmanno.core.providers.anthropic import AnthropicProvider, Budget, cost_usd  # noqa: E402
from llmanno.core.providers.base import ChatRequest, ProviderError  # noqa: E402


class FakeStream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


class FakeClient:
    def __init__(self, text='{"ok": true}', stop="end_turn"):
        self.sent = []
        usage = SimpleNamespace(
            input_tokens=1000, output_tokens=500,
            cache_read_input_tokens=3000, cache_creation_input_tokens=0,
        )  # fmt: skip
        self.message = SimpleNamespace(
            content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=text)],
            model="claude-sonnet-5-5", stop_reason=stop, usage=usage,
        )  # fmt: skip
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **params):
        self.sent.append(params)
        return FakeStream(self.message)


def request(**kw):
    base = dict(
        model="claude-sonnet-5-5",
        messages=[{"role": "system", "content": "Codebuch"}, {"role": "user", "content": "Satz"}],
        temperature=None, max_tokens=4000, schema={"type": "object"}, reasoning_effort="low",
    )  # fmt: skip
    return ChatRequest(**(base | kw))


def test_request_shape_and_cost(tmp_path):
    client = FakeClient()
    budget = Budget(tmp_path / "k.json", limit_usd=10)
    response = AnthropicProvider(api_key="x", budget=budget, client=client).chat(request())
    sent = client.sent[0]
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert sent["messages"] == [{"role": "user", "content": "Satz"}]
    assert sent["output_config"] == {
        "format": {"type": "json_schema", "schema": {"type": "object"}},
        "effort": "low",
    }
    assert "temperature" not in sent
    assert response.text == '{"ok": true}'
    assert response.tokens_in == 4000 and response.extra["tokens_cached"] == 3000
    expected = (1000 * 2 + 3000 * 0.2 + 500 * 10) / 1e6
    assert response.extra["cost_usd"] == pytest.approx(expected)
    assert Budget(tmp_path / "k.json").spent == pytest.approx(expected)  # bleibt über Läufe


def test_budget_blocks_before_sending(tmp_path):
    client = FakeClient()
    budget = Budget(tmp_path / "k.json", limit_usd=0.01)  # 4000 Ausgabe-Token kosten 0,04 $
    with pytest.raises(ProviderError, match="Kostengrenze"):
        AnthropicProvider(api_key="x", budget=budget, client=client).chat(request())
    assert client.sent == []


def test_temperature_is_refused_not_dropped(tmp_path):
    provider = AnthropicProvider(
        api_key="x", budget=Budget(tmp_path / "k.json"), client=FakeClient()
    )
    with pytest.raises(ProviderError, match="Temperatur"):
        provider.chat(request(temperature=0.0))


def test_truncation_maps_to_length(tmp_path):
    provider = AnthropicProvider(
        api_key="x", budget=Budget(tmp_path / "k.json"), client=FakeClient(stop="max_tokens")
    )
    assert provider.chat(request()).finish_reason == "length"


def test_unknown_model_has_no_price():
    with pytest.raises(ProviderError, match="Kein Preis"):
        cost_usd("claude-unknown", 1, 1, 0, 0)


def test_overload_in_stream_is_retryable(tmp_path):
    import anthropic
    import httpx

    class Overloaded(FakeClient):
        def _stream(self, **params):
            response = httpx.Response(
                200, request=httpx.Request("POST", "https://api.anthropic.com")
            )
            raise anthropic.APIStatusError("Overloaded", response=response, body=None)

    provider = AnthropicProvider(
        api_key="x", budget=Budget(tmp_path / "k.json"), client=Overloaded()
    )
    with pytest.raises(ProviderError) as exc:
        provider.chat(request())
    assert exc.value.retryable
