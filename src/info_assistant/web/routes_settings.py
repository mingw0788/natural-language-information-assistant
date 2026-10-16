from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from info_assistant.services.credentials import CredentialUnavailable

router = APIRouter(prefix="/api/settings")


class ProviderSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    search_secret: str | None = Field(default=None, max_length=10_000)
    search_provider: Literal["browser", "baidu", "brave"] | None = None
    browser_search_engine: Literal["bing", "baidu"] | None = None
    model_base_url: str | None = Field(default=None, max_length=2_000)
    model_name: str | None = Field(default=None, max_length=200)
    model_secret: str | None = Field(default=None, max_length=10_000)
    model_preset: Literal["none", "zhipu_free", "custom"] | None = None
    model_json_mode: Literal["json_schema", "json_object"] | None = None
    clear_model: bool = False


@router.get("/providers")
def get_provider_settings(request: Request) -> dict:
    try:
        return request.app.state.provider_settings.public_settings().model_dump(mode="json")
    except CredentialUnavailable:
        raise HTTPException(status_code=503, detail="Windows 凭据存储暂不可用") from None


@router.put("/providers")
def update_provider_settings(body: ProviderSettingsUpdate, request: Request) -> dict:
    store = request.app.state.provider_settings
    try:
        if any(value is not None for value in (
            body.search_provider, body.search_secret, body.browser_search_engine,
        )):
            store.update_search(
                provider=body.search_provider, secret=body.search_secret,
                browser_engine=body.browser_search_engine,
            )
        if body.clear_model or body.model_preset == "none":
            store.clear_model()
        elif body.model_preset == "zhipu_free":
            store.update_model_preset("zhipu_free", body.model_secret)
        elif body.model_base_url is not None or body.model_name is not None:
            if not body.model_base_url or not body.model_name:
                raise ValueError("需要同时填写模型服务地址和模型名称")
            store.update_model(
                body.model_base_url, body.model_name, body.model_secret,
                json_mode=body.model_json_mode or "json_schema",
            )
        elif body.model_secret or body.model_json_mode:
            current = store.public_settings()
            if not current.model_base_url or not current.model_name:
                raise ValueError("请先填写模型服务地址和模型名称")
            store.update_model(
                current.model_base_url, current.model_name, body.model_secret,
                json_mode=body.model_json_mode or current.model_json_mode,
                preset=current.model_preset,
            )
        return store.public_settings().model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except CredentialUnavailable:
        raise HTTPException(status_code=503, detail="Windows 凭据存储暂不可用") from None


@router.delete("/providers/model")
def clear_model_settings(request: Request) -> dict:
    try:
        return request.app.state.provider_settings.clear_model().model_dump(mode="json")
    except CredentialUnavailable:
        raise HTTPException(status_code=503, detail="Windows 凭据存储暂不可用") from None
