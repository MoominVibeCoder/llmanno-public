"""Temperatur „nicht senden“, wenn der Anbieter sie festlegt (etwa gpt-oss bei KI:connect.NRW)."""

import dataclasses
import json

import httpx
import pytest
from helpers import FakeProvider, engine
from test_evaluation import llm_answer, make_project
from test_providers import REQUEST, transport

from llmanno.core.config import ModelConfig
from llmanno.core.engine import _model_dump, fingerprint
from llmanno.core.export.prereg import preregistration
from llmanno.core.export.report import methods_paragraph
from llmanno.core.providers.base import ProviderError
from llmanno.core.providers.ollama import OllamaProvider
from llmanno.core.providers.openai_compat import OpenAICompatProvider

NO_TEMPERATURE = dataclasses.replace(REQUEST, temperature=None)


def test_providers_leave_out_an_unset_temperature():
    seen = []
    reply = {"choices": [{"message": {"content": "{}"}}]}
    OpenAICompatProvider(
        "https://x/v1", transport=transport(lambda r: httpx.Response(200, json=reply), seen)
    ).chat(NO_TEMPERATURE)
    assert "temperature" not in json.loads(seen[0].content)

    reply = {"model": "m", "message": {"content": "{}"}, "done_reason": "stop"}
    OllamaProvider(
        "http://o", transport=transport(lambda r: httpx.Response(200, json=reply), seen)
    ).chat(NO_TEMPERATURE)
    options = json.loads(seen[1].content)["options"]
    assert "temperature" not in options and options["num_predict"] == 300


def test_default_models_keep_their_fingerprint():
    # Das neue Feld darf bestehende Freezes und Lauf-Identitäten nicht verschieben.
    assert "provider_temperature" not in _model_dump(ModelConfig(provider="o", model="m"))


@pytest.fixture
def project(tmp_path):
    project = make_project(tmp_path, run=False)
    yield project
    project.close()


def test_run_and_reports_follow_the_provider(project):
    before = fingerprint(project)
    model = project.config.panel[0].model_copy(update={"provider_temperature": True})
    project.update(panel=[model])
    assert fingerprint(project) != before

    provider = FakeProvider(llm_answer)
    engine(project, provider).run()
    assert {r.temperature for r in provider.requests} == {None}
    assert "Temperatur nach Vorgabe des Anbieters" in methods_paragraph(project, None, "de")
    assert "temperature set by the provider" in methods_paragraph(project, None, "en")
    assert "| nach Vorgabe des Anbieters |" in preregistration(project)


def test_temperature_box_round_trip(qtbot):
    from llmanno.gui.models_page import TemperatureBox

    box = TemperatureBox()
    qtbot.addWidget(box)
    for model, text in [
        (ModelConfig(provider="o", model="m", provider_temperature=True), "nicht senden"),
        (ModelConfig(provider="o", model="m"), "automatisch"),
        (ModelConfig(provider="o", model="m", temperature=0.3), "0,30"),
    ]:
        box.set_model(model)
        assert box.text() in (text, text.replace(",", "."))
        assert model.model_copy(update=box.changes()) == model


GPT5_TOKENS = {"error": {
    "message": "Unsupported parameter: 'max_tokens' is not supported with this model. Use "
               "'max_completion_tokens' instead.",
    "type": "invalid_request_error", "param": "max_tokens", "code": "unsupported_parameter",
}}  # fmt: skip
GPT5_TEMPERATURE = {"error": {
    "message": "Unsupported value: 'temperature' does not support 0.7 with this model. Only the "
               "default (1) value is supported.",
    "type": "invalid_request_error", "param": "temperature", "code": "unsupported_value",
}}  # fmt: skip
OK = {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}


def test_reasoning_models_get_max_completion_tokens():
    """User 2026-10-12: GPT-5 bei KI:connect lehnte max_tokens ab (HTTP 400)."""
    seen = []

    def handler(request):
        body = json.loads(request.content)
        return httpx.Response(400 if "max_tokens" in body else 200,
                              json=GPT5_TOKENS if "max_tokens" in body else OK)  # fmt: skip

    provider = OpenAICompatProvider("https://x/v1", transport=transport(handler, seen))
    provider.chat(NO_TEMPERATURE)
    provider.chat(NO_TEMPERATURE)
    bodies = [json.loads(r.content) for r in seen]
    assert [("max_tokens" in b, b.get("max_completion_tokens")) for b in bodies] == [
        (True, None),
        (False, 300),
        (False, 300),  # danach gleich richtig
    ]


def test_rejected_temperature_names_the_fix():
    rejected = transport(lambda r: httpx.Response(400, json=GPT5_TEMPERATURE), [])
    provider = OpenAICompatProvider("https://x/v1", transport=rejected)
    with pytest.raises(ProviderError, match="„nicht senden“") as info:
        provider.chat(REQUEST)
    assert not info.value.retryable and info.value.status == 400
