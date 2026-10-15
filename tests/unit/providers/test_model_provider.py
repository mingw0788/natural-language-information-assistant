import json

import httpx
import pytest

from info_assistant.providers.base import ModelCallBudget, ProviderError
from info_assistant.providers.openai_compatible import OpenAICompatibleModelProvider
from info_assistant.services.credentials import InMemoryCredentialStore


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
