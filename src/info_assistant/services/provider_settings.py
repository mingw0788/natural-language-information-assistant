from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict

from info_assistant.services.credentials import CredentialStore
from info_assistant.storage.database import Database


class ProviderConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_provider: str = "brave"
    search_configured: bool = False
    model_provider: str = "openai_compatible"
    model_base_url: str = ""
    model_name: str = ""
    model_configured: bool = False
    model_supports_structured_output: bool = True
    has_search_secret: bool = False
    has_model_secret: bool = False


class ProviderSettingsStore:
    SETTING_KEY = "providers"

    def __init__(self, database: Database, credentials: CredentialStore) -> None:
        self.database = database
        self.credentials = credentials

    def public_settings(self) -> ProviderConfiguration:
        stored = self._read()
        has_search_secret = bool(self.credentials.get_secret("brave-search"))
        has_model_secret = bool(self.credentials.get_secret("model-api"))
        model_ready = bool(
            stored.get("model_base_url") and stored.get("model_name") and has_model_secret
        )
        return ProviderConfiguration(
            model_base_url=stored.get("model_base_url", ""),
            model_name=stored.get("model_name", ""),
            search_configured=has_search_secret,
            model_configured=model_ready,
            has_search_secret=has_search_secret,
            has_model_secret=has_model_secret,
        )

    def update_search_secret(self, secret: str | None) -> ProviderConfiguration:
        if secret:
            self.credentials.set_secret("brave-search", secret.strip())
        else:
            self.credentials.delete_secret("brave-search")
        return self.public_settings()

    def update_model(
        self, base_url: str, model_name: str, secret: str | None
    ) -> ProviderConfiguration:
        normalized_url = validate_model_base_url(base_url)
        normalized_model = model_name.strip()
        if not normalized_model:
            raise ValueError("模型名称不能为空")
        if secret:
            self.credentials.set_secret("model-api", secret.strip())
        stored = self._read()
        stored["model_base_url"] = normalized_url
        stored["model_name"] = normalized_model
        self._write(stored)
        return self.public_settings()

    def clear_model(self) -> ProviderConfiguration:
        self.credentials.delete_secret("model-api")
        stored = self._read()
        stored.pop("model_base_url", None)
        stored.pop("model_name", None)
        self._write(stored)
        return self.public_settings()

    def get_model_secret(self) -> str | None:
        return self.credentials.get_secret("model-api")

    def get_search_secret(self) -> str | None:
        return self.credentials.get_secret("brave-search")

    def _read(self) -> dict[str, str]:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT value_json FROM app_settings WHERE key=?", (self.SETTING_KEY,)
            ).fetchone()
        return json.loads(row["value_json"]) if row else {}

    def _write(self, data: dict[str, str]) -> None:
        timestamp = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO app_settings(key, value_json, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, "
                "updated_at=excluded.updated_at",
                (self.SETTING_KEY, json.dumps(data, ensure_ascii=False), timestamp),
            )


def validate_model_base_url(value: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme not in {"https", "http"} or not parts.hostname:
        raise ValueError("模型服务地址必须是 HTTP(S) URL")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("模型服务地址不能包含凭据、查询参数或片段")
    hostname = parts.hostname.rstrip(".").lower()
    if parts.scheme == "http" and hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("非本机模型服务必须使用 HTTPS")
    return value.strip().rstrip("/")
