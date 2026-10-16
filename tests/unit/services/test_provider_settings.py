from threading import Event, Thread, current_thread

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
    settings.update_search(provider="baidu", secret="search-secret-value")

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


def make_provider_settings(tmp_path, credentials=None):
    database = Database(tmp_path / "assistant.sqlite3")
    database.migrate()
    credentials = credentials or InMemoryCredentialStore()
    return ProviderSettingsStore(database, credentials), credentials


def test_new_user_can_search_without_any_api_credentials(tmp_path) -> None:
    settings, _credentials = make_provider_settings(tmp_path)

    public = settings.public_settings()

    assert public.search_provider == "browser"
    assert public.browser_search_engine == "bing"
    assert public.search_configured is True
    assert public.has_search_secret is False
    assert public.model_preset == "none"
    assert public.model_configured is False


def test_legacy_brave_key_remains_selected_until_user_switches(tmp_path) -> None:
    credentials = InMemoryCredentialStore()
    credentials.set_secret("brave-search", "legacy-key")
    settings, _ = make_provider_settings(tmp_path, credentials)

    assert settings.public_settings().search_provider == "brave"
    assert settings.public_settings().search_configured is True


def test_search_selection_keeps_provider_keys_separate(tmp_path) -> None:
    settings, credentials = make_provider_settings(tmp_path)

    baidu = settings.update_search(provider="baidu", secret="baidu-key")
    brave = settings.update_search(provider="brave")
    assert baidu.search_configured is True
    assert brave.search_configured is False
    assert settings.get_search_secret() is None

    settings.update_search(secret="brave-key")
    restored = settings.update_search(provider="baidu")
    assert set(restored.configured_search_providers) == {"browser", "baidu", "brave"}
    assert settings.get_search_secret() == "baidu-key"
    settings.update_search(secret="")
    assert credentials.get_secret("baidu-search") is None
    assert credentials.get_secret("brave-search") == "brave-key"
    assert "baidu-key" not in restored.model_dump_json()


def test_invalid_search_provider_does_not_mutate_settings(tmp_path) -> None:
    settings, _credentials = make_provider_settings(tmp_path)

    with pytest.raises(ValueError):
        settings.update_search(provider="unknown", secret="wrong-service-key")

    assert settings.public_settings().search_provider == "browser"
    assert settings.public_settings().has_search_secret is False


def test_browser_mode_never_reuses_saved_api_key(tmp_path) -> None:
    settings, credentials = make_provider_settings(tmp_path)
    settings.update_search(provider="baidu", secret="baidu-key")

    public = settings.update_search(provider="browser", browser_engine="baidu")

    assert public.search_provider == "browser"
    assert public.browser_search_engine == "baidu"
    assert public.search_configured is True
    assert public.has_search_secret is False
    assert settings.get_search_secret() is None
    assert credentials.get_secret("baidu-search") == "baidu-key"


def test_free_model_preset_uses_exact_domestic_endpoint_and_json_mode(tmp_path) -> None:
    settings, _credentials = make_provider_settings(tmp_path)

    public = settings.update_model_preset("zhipu_free", "domestic-model-key")

    assert public.model_base_url == "https://open.bigmodel.cn/api/paas/v4"
    assert public.model_name == "glm-4.7-flash"
    assert public.model_json_mode == "json_object"
    assert public.model_preset == "zhipu_free"
    assert public.model_configured is True
    assert "domestic-model-key" not in public.model_dump_json()


def test_model_preset_without_key_is_saved_but_not_ready(tmp_path) -> None:
    settings, _credentials = make_provider_settings(tmp_path)

    public = settings.update_model_preset("zhipu_free")

    assert public.model_name == "glm-4.7-flash"
    assert public.model_configured is False


def test_changing_model_endpoint_requires_explicit_new_key(tmp_path) -> None:
    settings, credentials = make_provider_settings(tmp_path)
    settings.update_model("https://old.example/v1", "old-model", "old-service-key")

    with pytest.raises(ValueError):
        settings.update_model("https://new.example/v1", "new-model", None)

    assert settings.public_settings().model_base_url == "https://old.example/v1"
    assert credentials.get_secret("model-api") == "old-service-key"


def test_switching_to_free_model_does_not_reuse_another_service_key(tmp_path) -> None:
    settings, _credentials = make_provider_settings(tmp_path)
    settings.update_model("https://old.example/v1", "old-model", "old-service-key")

    with pytest.raises(ValueError):
        settings.update_model_preset("zhipu_free")

    updated = settings.update_model_preset("zhipu_free", "zhipu-key")
    assert updated.model_configured is True
    assert settings.get_model_secret() == "zhipu-key"


def test_model_runtime_snapshot_keeps_endpoint_and_credential_together(tmp_path) -> None:
    settings, _credentials = make_provider_settings(tmp_path)
    settings.update_model("https://service-a.example/v1", "model-a", "service-a-key")

    configuration, secret = settings.model_runtime_snapshot()
    settings.update_model("https://service-b.example/v1", "model-b", "service-b-key")

    assert configuration.model_base_url == "https://service-a.example/v1"
    assert configuration.model_name == "model-a"
    assert secret == "service-a-key"
    assert settings.public_settings().model_base_url == "https://service-b.example/v1"


def test_search_settings_cannot_overwrite_concurrent_model_configuration(tmp_path) -> None:
    settings, credentials = make_provider_settings(tmp_path)
    settings.update_model("https://service-a.example/v1", "model-a", "service-a-key")
    at_search_write = Event()
    allow_search_write = Event()
    original_write = settings._write

    def delayed_write(data):
        if current_thread().name == "search-settings":
            at_search_write.set()
            assert allow_search_write.wait(2)
        original_write(data)

    settings._write = delayed_write
    search_thread = Thread(
        name="search-settings", target=lambda: settings.update_search(provider="brave")
    )
    search_thread.start()
    assert at_search_write.wait(2)

    lock_was_free = settings._model_settings_lock.acquire(blocking=False)
    if lock_was_free:
        settings._model_settings_lock.release()
        settings.update_model("https://service-b.example/v1", "model-b", "service-b-key")
    allow_search_write.set()
    search_thread.join(2)
    assert not search_thread.is_alive()

    if not lock_was_free:
        settings.update_model("https://service-b.example/v1", "model-b", "service-b-key")
    current = settings.public_settings()
    assert current.model_base_url == "https://service-b.example/v1"
    assert credentials.get_secret("model-api") == "service-b-key"
    assert lock_was_free is False
