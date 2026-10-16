from __future__ import annotations

import re
from datetime import date
from urllib.parse import urlsplit

import httpx

from info_assistant.providers.base import (
    ProviderError,
    ProviderNotConfigured,
    SearchQuery,
    SearchResponse,
    SearchResult,
)
from info_assistant.services.credentials import CredentialStore


class BaiduSearchProvider:
    ENDPOINT = "https://qianfan.baidubce.com/v2/ai_search/web_search"

    def __init__(
        self,
        credentials: CredentialStore,
        client: httpx.Client | None = None,
        timeout_seconds: float = 30,
    ) -> None:
        self.credentials = credentials
        self.client = client
        self.timeout_seconds = timeout_seconds

    def search(self, query: SearchQuery) -> SearchResponse:
        secret = self.credentials.get_secret("baidu-search")
        if not secret:
            raise ProviderNotConfigured("百度搜索尚未配置密钥")
        if not query.query.strip():
            raise ProviderError("搜索内容不能为空")
        count = min(max(query.count, 1), 50)
        payload = {
            "messages": [{"role": "user", "content": query.query}],
            "search_source": "baidu_search_v2",
            "resource_type_filter": [{"type": "web", "top_k": count}],
            **_freshness_parameters(query.freshness),
        }
        # The official AppBuilder web_search curl example uses this header for its API key:
        # https://ai.baidu.com/ai-doc/AppBuilder/pmaxd1hvy
        headers = {"Accept": "application/json", "X-Appbuilder-Authorization": f"Bearer {secret}"}
        try:
            if self.client:
                response = self.client.post(
                    self.ENDPOINT, json=payload, headers=headers, timeout=self.timeout_seconds
                )
            else:
                with httpx.Client(trust_env=False) as client:
                    response = client.post(
                        self.ENDPOINT, json=payload, headers=headers, timeout=self.timeout_seconds
                    )
            if response.status_code in {401, 403}:
                raise ProviderError("百度搜索鉴权失败，请检查 AppBuilder API Key 和服务权限")
            if response.status_code == 429:
                raise ProviderError("百度搜索调用受到限制，请检查免费额度和调用频率")
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError):
            raise ProviderError("百度搜索请求失败；密钥和服务响应正文不会写入错误信息") from None

        if not isinstance(body, dict) or any(
            body.get(key) not in (None, 0, "0") for key in ("code", "error_code")
        ):
            raise ProviderError("百度搜索返回错误或无效数据，请检查密钥、服务权限和额度")
        if "references" not in body:
            raise ProviderError("百度搜索返回的数据缺少网页结果")
        references = body["references"]
        if references is None:
            references = []
        if not isinstance(references, list):
            raise ProviderError("百度搜索返回的网页结果格式无效")
        results = []
        for item in references:
            if not isinstance(item, dict) or item.get("type", "web") != "web":
                continue
            url = item.get("url")
            if not _is_web_url(url):
                continue
            results.append(
                SearchResult(
                    title=_text(item.get("title"), 500),
                    url=url,
                    snippet=_text(item.get("content"), 2_000),
                    is_evidence=False,
                )
            )
            if len(results) >= count:
                break
        return SearchResponse(results=results, provider="百度搜索")


def _freshness_parameters(freshness: str | None) -> dict:
    if not freshness:
        return {}
    periods = {"pw": "week", "pm": "month", "py": "year"}
    if freshness in periods:
        return {"search_recency_filter": periods[freshness]}
    if freshness == "pd":
        # The documented one-day range is the current calendar day (now/d).
        page_time = {"gte": "now/d", "lte": "now/d"}
    else:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}to\d{4}-\d{2}-\d{2}", freshness):
            raise ProviderError("搜索时间范围格式无效")
        start_text, end_text = freshness.split("to")
        try:
            start, end = date.fromisoformat(start_text), date.fromisoformat(end_text)
        except ValueError:
            raise ProviderError("搜索时间范围日期无效") from None
        if start > end:
            raise ProviderError("搜索开始日期不能晚于结束日期")
        page_time = {"gte": start_text, "lte": end_text}
    return {"search_filter": {"range": {"page_time": page_time}}}


def _text(value: object, limit: int) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _is_web_url(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 2_000:
        return False
    try:
        parts = urlsplit(value)
        return parts.scheme in {"http", "https"} and bool(parts.hostname)
    except ValueError:
        return False
