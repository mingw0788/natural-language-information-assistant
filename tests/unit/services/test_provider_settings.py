import pytest

from info_assistant.services.credentials import InMemoryCredentialStore
from info_assistant.services.provider_settings import (
    ProviderSettingsStore,
    validate_model_base_url,
)
from info_assistant.storage.database import Database


def test_provider_metadata_is_persisted_without_secrets(tmp_path) -> None:
    database = Database(tmp_path / "assistant.sqlite3")
    database.migrate()
    credentials = InMemoryCredentialStore()
    settings = ProviderSettingsStore(database, credentials)

    public = settings.update_model(
        "https://api.example.com/v1", "structured-model", "model-secret-value"
    )
    settings.update_search_secret("search-secret-value")

    assert public.model_configured is True
    assert public.has_model_secret is True
    assert "model-secret-value" not in public.model_dump_json()
    with database.connection() as connection:
        stored = connection.execute("SELECT value_json FROM app_settings").fetchone()[0]
    assert "model-secret-value" not in stored
    assert "search-secret-value" not in stored


@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.com/v1",
        "https://user:pass@example.com/v1",
        "https://example.com/v1?api_key=x",
        "file:///C:/secret",
    ],
)
def test_model_url_rejects_unsafe_or_credential_bearing_values(url: str) -> None:
    with pytest.raises(ValueError):
        validate_model_base_url(url)


def test_local_http_model_endpoint_is_allowed_only_for_loopback() -> None:
    assert validate_model_base_url("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434/v1"
