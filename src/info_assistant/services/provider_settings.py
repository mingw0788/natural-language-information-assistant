from __future__ import annotations

import json
from datetime import UTC, datetime
from threading import RLock
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict

from info_assistant.services.credentials import CredentialStore
from info_assistant.storage.database import Database

SEARCH_CREDENTIALS = {"baidu": "baidu-search", "brave": "brave-search"}
SEARCH_LABELS = {"baidu": "百度搜索（千帆）", "brave": "Brave Search"}
SEARCH_SIGNUP_URLS = {
    "baidu": "https://console.bce.baidu.com/ai_apaas/",
    "brave": "https://brave.com/search/api/",
}
BROWSER_ENGINES = {"bing": "必应", "baidu": "百度"}
BROWSER_HOME_URLS = {"bing": "https://cn.bing.com/", "baidu": "https://www.baidu.com/"}
ZHIPU_FREE_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
ZHIPU_FREE_MODEL = "glm-4.7-flash"


class ProviderConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_provider: str = "browser"
    search_provider_label: str = "免API网页搜索（必应）"
    search_signup_url: str = "https://cn.bing.com/"
    browser_search_engine: str = "bing"
    configured_search_providers: list[str] = ["browser"]
    search_configured: bool = False
    model_provider: str = "openai_compatible"
    model_base_url: str = ""
    model_name: str = ""
    model_configured: bool = False
    model_supports_structured_output: bool = True
    model_preset: str = "none"
    model_json_mode: str = "json_schema"
    has_search_secret: bool = False
    has_model_secret: bool = False


class ProviderSettingsStore:
    SETTING_KEY = "providers"

    def __init__(self, database: Database, credentials: CredentialStore) -> None:
        self.database = database
        self.credentials = credentials
        self._model_settings_lock = RLock()

    def public_settings(self) -> ProviderConfiguration:
        stored = self._read()
        configured = ["browser"] + [
            provider for provider, name in SEARCH_CREDENTIALS.items()
            if self.credentials.get_secret(name)
        ]
        selected = stored.get("search_provider")
        if selected not in {"browser", *SEARCH_CREDENTIALS}:
            selected = "brave" if "brave" in configured else "browser"
        browser_engine = stored.get("browser_search_engine", "bing")
        if browser_engine not in BROWSER_ENGINES:
            browser_engine = "bing"
        has_search_secret = selected != "browser" and selected in configured
        has_model_secret = bool(self.credentials.get_secret("model-api"))
        model_ready = bool(
            stored.get("model_base_url") and stored.get("model_name") and has_model_secret
        )
        return ProviderConfiguration(
            search_provider=selected,
            search_provider_label=(
                f"免API网页搜索（{BROWSER_ENGINES[browser_engine]}）"
                if selected == "browser" else SEARCH_LABELS[selected]
            ),
            search_signup_url=(
                BROWSER_HOME_URLS[browser_engine]
                if selected == "browser" else SEARCH_SIGNUP_URLS[selected]
            ),
            browser_search_engine=browser_engine,
            configured_search_providers=configured,
            model_base_url=stored.get("model_base_url", ""),
            model_name=stored.get("model_name", ""),
            search_configured=selected == "browser" or has_search_secret,
            model_configured=model_ready,
            model_preset=stored.get(
                "model_preset", "custom" if stored.get("model_base_url") else "none"
            ),
            model_json_mode=stored.get("model_json_mode", "json_schema"),
            model_supports_structured_output=stored.get("model_json_mode") != "json_object",
            has_search_secret=has_search_secret,
            has_model_secret=has_model_secret,
        )

    def update_search_secret(self, secret: str | None) -> ProviderConfiguration:
        return self.update_search(secret=secret)

    def update_search(
        self, provider: str | None = None, secret: str | None = None,
        browser_engine: str | None = None,
    ) -> ProviderConfiguration:
        with self._model_settings_lock:
            selected = provider or self.public_settings().search_provider
            if selected not in {"browser", *SEARCH_CREDENTIALS}:
                raise ValueError("不支持的搜索服务")
            if browser_engine is not None and browser_engine not in BROWSER_ENGINES:
                raise ValueError("不支持的网页搜索引擎")
            normalized_secret = secret.strip() if secret else ""
            if selected == "browser" and normalized_secret:
                raise ValueError("免API网页搜索不需要密钥，请切换到对应的API服务再保存密钥")
            if selected != "browser" and secret is not None:
                credential_name = SEARCH_CREDENTIALS[selected]
                if normalized_secret:
                    self.credentials.set_secret(credential_name, normalized_secret)
                else:
                    self.credentials.delete_secret(credential_name)
            stored = self._read()
            stored["search_provider"] = selected
            if browser_engine:
                stored["browser_search_engine"] = browser_engine
            self._write(stored)
            return self.public_settings()

    def update_model(
        self, base_url: str, model_name: str, secret: str | None,
        json_mode: str = "json_schema", preset: str = "custom",
    ) -> ProviderConfiguration:
        normalized_url = validate_model_base_url(base_url)
        normalized_model = model_name.strip()
        if not normalized_model:
            raise ValueError("模型名称不能为空")
        if json_mode not in {"json_schema", "json_object"}:
            raise ValueError("模型 JSON 输出模式无效")
        if preset not in {"custom", "zhipu_free"}:
            raise ValueError("不支持的模型预设")
        if preset == "zhipu_free" and (
            normalized_url != ZHIPU_FREE_BASE_URL or normalized_model != ZHIPU_FREE_MODEL
            or json_mode != "json_object"
        ):
            raise ValueError("免费模型预设参数不匹配")
        normalized_secret = secret.strip() if secret else ""
        with self._model_settings_lock:
            stored = self._read()
            if (
                stored.get("model_base_url") != normalized_url
                and self.get_model_secret() and not normalized_secret
            ):
                raise ValueError("切换模型服务地址时请填写新服务的密钥，避免复用其他服务的密钥")
            if normalized_secret:
                self.credentials.set_secret("model-api", normalized_secret)
            stored["model_base_url"] = normalized_url
            stored["model_name"] = normalized_model
            stored["model_preset"] = preset
            stored["model_json_mode"] = json_mode
            self._write(stored)
            return self.public_settings()

    def update_model_preset(
        self, preset: str, secret: str | None = None
    ) -> ProviderConfiguration:
        if preset == "none":
            return self.clear_model()
        if preset != "zhipu_free":
            raise ValueError("不支持的模型预设")
        return self.update_model(
            ZHIPU_FREE_BASE_URL, ZHIPU_FREE_MODEL, secret,
            json_mode="json_object", preset="zhipu_free",
        )

    def clear_model(self) -> ProviderConfiguration:
        with self._model_settings_lock:
            self.credentials.delete_secret("model-api")
            stored = self._read()
            stored.pop("model_base_url", None)
            stored.pop("model_name", None)
            stored.pop("model_preset", None)
            stored.pop("model_json_mode", None)
            self._write(stored)
            return self.public_settings()

    def model_runtime_snapshot(self) -> tuple[ProviderConfiguration, str | None]:
        """Read model endpoint metadata and its key as one run-scoped configuration."""
        with self._model_settings_lock:
            return self.public_settings(), self.get_model_secret()

    def get_model_secret(self) -> str | None:
        return self.credentials.get_secret("model-api")

    def get_search_secret(self) -> str | None:
        selected = self.public_settings().search_provider
        name = SEARCH_CREDENTIALS.get(selected)
        return self.credentials.get_secret(name) if name else None

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
