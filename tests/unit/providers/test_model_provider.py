import json

import httpx
import pytest

from info_assistant.providers.base import ModelCallBudget, ProviderError
from info_assistant.providers.openai_compatible import OpenAICompatibleModelProvider
from info_assistant.services.credentials import InMemoryCredentialStore
from info_assistant.services.provider_settings import ProviderSettingsStore
from info_assistant.storage.database import Database


def test_model_provider_requests_schema_json_and_parses_usage() -> None:
    credentials = InMemoryCredentialStore()
    credentials.set_secret("model-api", "secret-model-key")
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append((request, json.loads(request.content)))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"answer":"ok"}'}}],
                "usage": {"prompt_tokens": 31, "completion_tokens": 6},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleModelProvider(
        base_url="https://api.example.com/v1",
        model="test-model",
        credentials=credentials,
        client=client,
    )
    result = provider.generate_json(
        "answer",
        {"type": "object", "properties": {"answer": {"type": "string"}}},
        "untrusted page text",
        ModelCallBudget(max_output_tokens=100),
    )

    request, body = captured[0]
    assert request.headers["Authorization"] == "Bearer secret-model-key"
    assert body["response_format"]["type"] == "json_schema"
    assert result.data == {"answer": "ok"}
    assert result.input_tokens == 31
    assert result.output_tokens == 6


def test_model_provider_rejects_non_json_and_sanitizes_errors() -> None:
    secret = "secret-model-key"
    credentials = InMemoryCredentialStore()
    credentials.set_secret("model-api", secret)
    provider = OpenAICompatibleModelProvider(
        base_url="https://api.example.com/v1",
        model="test-model",
        credentials=credentials,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={"choices": [{"message": {"content": "no"}}]},
                )
            )
        ),
    )

    with pytest.raises(ProviderError) as error:
        provider.generate_json("answer", {"type": "object"}, "text", ModelCallBudget(20))

    assert secret not in str(error.value)


def test_domestic_json_object_mode_supplies_schema_and_disables_thinking() -> None:
    credentials = InMemoryCredentialStore()
    credentials.set_secret("model-api", "zhipu-test-key")
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append((request, json.loads(request.content)))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"records":[]}'}}]},
        )

    schema = {"type": "object", "properties": {"records": {"type": "array"}}}
    provider = OpenAICompatibleModelProvider(
        "https://open.bigmodel.cn/api/paas/v4",
        "glm-4.7-flash",
        credentials,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        json_mode="json_object",
        disable_thinking=True,
    )

    result = provider.generate_json("records", schema, "untrusted page", ModelCallBudget(4000))

    request, body = captured[0]
    assert str(request.url) == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    assert body["response_format"] == {"type": "json_object"}
    assert body["thinking"] == {"type": "disabled"}
    assert body["stream"] is False
    assert body["max_tokens"] == 4000
    assert json.dumps(schema, ensure_ascii=False) in body["messages"][0]["content"]
    assert body["messages"][1]["content"] == "untrusted page"
    assert result.data == {"records": []}


def test_invalid_json_mode_is_rejected_before_model_call() -> None:
    with pytest.raises(ValueError):
        OpenAICompatibleModelProvider(
            "https://example.com/v1", "model", InMemoryCredentialStore(), json_mode="invalid"
        )


def test_in_flight_model_provider_does_not_pick_up_a_different_services_key(tmp_path) -> None:
    credentials = InMemoryCredentialStore()
    database = Database(tmp_path / "assistant.sqlite3")
    database.migrate()
    settings = ProviderSettingsStore(database, credentials)
    settings.update_model("https://service-a.example/v1", "model-a", "service-a-key")
    configuration, key_snapshot = settings.model_runtime_snapshot()
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"answer":"ok"}'}}]}
        )

    provider = OpenAICompatibleModelProvider(
        configuration.model_base_url,
        configuration.model_name,
        credentials,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        credential_snapshot=key_snapshot,
        freeze_credentials=True,
    )
    settings.update_model("https://service-b.example/v1", "model-b", "service-b-key")

    provider.generate_json(
        "answer", {"type": "object"}, "text", ModelCallBudget(max_output_tokens=20)
    )

    assert str(captured[0].url) == "https://service-a.example/v1/chat/completions"
    assert captured[0].headers["Authorization"] == "Bearer service-a-key"
