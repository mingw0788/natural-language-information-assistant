import json
from importlib import import_module

import httpx
import pytest

from info_assistant.providers.base import ProviderError, ProviderNotConfigured, SearchQuery
from info_assistant.services.credentials import InMemoryCredentialStore


def make_provider(handler=None, *, configured=True):
    credentials = InMemoryCredentialStore()
    if configured:
        credentials.set_secret("baidu-search", "private-test-token")
    client = httpx.Client(transport=httpx.MockTransport(handler)) if handler else None
    provider_class = import_module("info_assistant.providers.baidu_search").BaiduSearchProvider
    return provider_class(credentials, client=client)


def test_baidu_search_emits_official_request_and_maps_web_references() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "references": [
                    {
                        "id": 1,
                        "title": "公开资料",
                        "url": "https://example.com/资料",
                        "content": "这是搜索摘要",
                        "type": "web",
                        "date": "2026-10-15 08:00:00",
                        "icon": None,
                        "image": None,
                        "video": None,
                        "web_anchor": "公开资料",
                    }
                ],
                "request_id": "test-request",
            },
        )

    result = make_provider(handler).search(SearchQuery(query="中国公开信息", count=8))

    assert requests[0].method == "POST"
    assert str(requests[0].url) == "https://qianfan.baidubce.com/v2/ai_search/web_search"
    assert requests[0].headers["X-Appbuilder-Authorization"] == "Bearer private-test-token"
    assert json.loads(requests[0].content) == {
        "messages": [{"role": "user", "content": "中国公开信息"}],
        "search_source": "baidu_search_v2",
        "resource_type_filter": [{"type": "web", "top_k": 8}],
    }
    assert result.provider == "百度搜索"
    assert result.results[0].title == "公开资料"
    assert result.results[0].url == "https://example.com/资料"
    assert result.results[0].snippet == "这是搜索摘要"
    assert result.results[0].is_evidence is False


def test_baidu_search_fails_before_network_without_key() -> None:
    with pytest.raises(ProviderNotConfigured):
        make_provider(configured=False).search(SearchQuery(query="公开资料"))


@pytest.mark.parametrize(
    ("freshness", "expected"),
    [
        ("pd", {"search_filter": {"range": {"page_time": {"gte": "now/d", "lte": "now/d"}}}}),
        ("pw", {"search_recency_filter": "week"}),
        ("pm", {"search_recency_filter": "month"}),
        ("py", {"search_recency_filter": "year"}),
        (
            "2026-10-01to2026-10-15",
            {"search_filter": {"range": {"page_time": {"gte": "2026-10-01", "lte": "2026-10-15"}}}},
        ),
    ],
)
def test_baidu_search_preserves_supported_freshness(freshness, expected) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"references": [], "request_id": "test-request"})

    make_provider(handler).search(SearchQuery(query="公开资料", freshness=freshness))

    request_body = json.loads(requests[0].content)
    assert {key: request_body[key] for key in expected} == expected


@pytest.mark.parametrize(
    "freshness",
    ["last week", "2026-02-30to2026-03-01", "2026-10-15to2026-10-01", "2026-1-1to2026-1-2"],
)
def test_baidu_search_rejects_invalid_date_ranges_before_network(freshness) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("Invalid dates must not reach the external service")

    with pytest.raises(ProviderError):
        make_provider(handler).search(SearchQuery(query="公开资料", freshness=freshness))


@pytest.mark.parametrize(("count", "expected"), [(-2, 1), (0, 1), (100, 50)])
def test_baidu_search_bounds_requested_result_count(count, expected) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"references": [], "request_id": "test-request"})

    make_provider(handler).search(SearchQuery(query="公开资料", count=count))

    assert json.loads(requests[0].content)["resource_type_filter"][0]["top_k"] == expected


def test_baidu_search_discards_unsupported_urls_and_non_web_references() -> None:
    references = [
        None,
        "invalid",
        {"url": None},
        {"url": "javascript:alert(1)"},
        {"url": "file:///private.txt"},
        {"url": "https://"},
        {"url": "https://example.com/video", "type": "video"},
        {"url": "https://example.com/web", "title": None, "content": None, "type": "web"},
    ]
    provider = make_provider(
        lambda request: httpx.Response(200, json={"references": references, "request_id": "test"})
    )

    result = provider.search(SearchQuery(query="公开资料"))

    assert len(result.results) == 1
    assert result.results[0].url == "https://example.com/web"
    assert result.results[0].title == ""
    assert result.results[0].snippet == ""


def test_baidu_search_honors_result_count_when_server_returns_more() -> None:
    provider = make_provider(
        lambda request: httpx.Response(
            200,
            json={
                "references": [
                    {"url": "https://example.com/first", "type": "web"},
                    {"url": "https://example.com/second", "type": "web"},
                ],
                "request_id": "test",
            },
        )
    )

    result = provider.search(SearchQuery(query="公开资料", count=1))

    assert [item.url for item in result.results] == ["https://example.com/first"]


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_baidu_search_http_failures_do_not_expose_secrets_or_response_bodies(status) -> None:
    provider = make_provider(
        lambda request: httpx.Response(status, text="private-test-token confidential-response")
    )

    with pytest.raises(ProviderError) as error:
        provider.search(SearchQuery(query="公开资料"))

    assert "private-test-token" not in str(error.value)
    assert "confidential-response" not in str(error.value)
    assert error.value.__cause__ is None


@pytest.mark.parametrize(
    "payload",
    [
        [],
        None,
        {},
        {"references": "invalid"},
        {"references": {}},
        {"references": [], "code": 216003, "message": "private-test-token confidential-response"},
        {"references": [], "error_code": 216003, "error_msg": "private-test-token"},
    ],
)
def test_baidu_search_rejects_malformed_payloads_and_business_errors(payload) -> None:
    provider = make_provider(lambda request: httpx.Response(200, json=payload))

    with pytest.raises(ProviderError) as error:
        provider.search(SearchQuery(query="公开资料"))

    assert "private-test-token" not in str(error.value)
    assert "confidential-response" not in str(error.value)


def test_baidu_search_handles_null_reference_list_as_empty() -> None:
    provider = make_provider(lambda request: httpx.Response(200, json={"references": None}))

    assert provider.search(SearchQuery(query="公开资料")).results == []


def test_baidu_search_network_failures_do_not_expose_request_details() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("private-test-token confidential-response", request=request)

    with pytest.raises(ProviderError) as error:
        make_provider(handler).search(SearchQuery(query="公开资料"))

    assert "private-test-token" not in str(error.value)
    assert "confidential-response" not in str(error.value)


def test_baidu_search_rejects_blank_query_before_network() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("Blank queries must not reach the external service")

    with pytest.raises(ProviderError):
        make_provider(handler).search(SearchQuery(query=" \n "))
