from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from info_assistant.services.credentials import CredentialUnavailable

router = APIRouter(prefix="/api/settings")


class ProviderSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    search_secret: str | None = Field(default=None, max_length=10_000)
    model_base_url: str | None = Field(default=None, max_length=2_000)
    model_name: str | None = Field(default=None, max_length=200)
    model_secret: str | None = Field(default=None, max_length=10_000)
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
        if body.search_secret is not None:
            store.update_search_secret(body.search_secret)
        if body.clear_model:
            store.clear_model()
        elif body.model_base_url is not None or body.model_name is not None:
            if not body.model_base_url or not body.model_name:
                raise ValueError("需要同时填写模型服务地址和模型名称")
            store.update_model(body.model_base_url, body.model_name, body.model_secret)
        elif body.model_secret:
            current = store.public_settings()
            if not current.model_base_url or not current.model_name:
                raise ValueError("请先填写模型服务地址和模型名称")
            store.update_model(current.model_base_url, current.model_name, body.model_secret)
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
