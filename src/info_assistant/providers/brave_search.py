from __future__ import annotations

import httpx

from info_assistant.providers.base import (
    ProviderError,
    ProviderNotConfigured,
    SearchQuery,
    SearchResponse,
    SearchResult,
)
from info_assistant.services.credentials import CredentialStore


class BraveSearchProvider:
    ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

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
        secret = self.credentials.get_secret("brave-search")
        if not secret:
            raise ProviderNotConfigured("搜索服务尚未配置")
        params: dict[str, str | int] = {"q": query.query, "count": min(max(query.count, 1), 20)}
        if query.country:
            params["country"] = query.country
        if query.language:
            params["search_lang"] = query.language
        try:
            headers = {"Accept": "application/json", "X-Subscription-Token": secret}
            if self.client:
                response = self.client.get(
                    self.ENDPOINT, params=params, headers=headers, timeout=self.timeout_seconds
                )
            else:
                with httpx.Client(trust_env=False) as client:
                    response = client.get(
                        self.ENDPOINT,
                        params=params,
                        headers=headers,
                        timeout=self.timeout_seconds,
                    )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            raise ProviderError("搜索服务请求失败；密钥和请求正文不会写入错误信息") from None
        entries = payload.get("web", {}).get("results", [])
        results = [
            SearchResult(
                title=str(item.get("title", ""))[:500],
                url=str(item.get("url", ""))[:2_000],
                snippet=str(item.get("description", ""))[:2_000],
                is_evidence=False,
            )
            for item in entries[: params["count"]]
            if item.get("url")
        ]
        return SearchResponse(results=results, provider="Brave Search")

