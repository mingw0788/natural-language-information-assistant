import base64
from datetime import date
from importlib import import_module
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from info_assistant.providers.base import ProviderError, SearchQuery
from info_assistant.services.source_fetcher import FetchTransportError, TransportResponse

BING_HTML = """<!doctype html><html><head><title>空气净化器 - 搜索</title>
<script>var challenge = 'ordinary-page-script';</script></head><body>
<nav><a href="https://example.com/navigation">导航</a></nav><ol id="b_results">
<li class="b_ad"><h2><a href="https://example.com/ad">赞助商</a></h2></li>
<li class="b_algo"><div class="b_title"><h2><a href="https://example.com/a">官方<b>资料</b></a></h2></div>
<div class="b_caption"><p>空气净化器 <strong>产品说明</strong>。</p></div></li>
<li class="b_algo"><h2><a href="https://example.com/b">第二条</a></h2>
<div class="b_caption"><p>另一项资料</p></div></li></ol></body></html>"""


class FixtureFetcher:
    def __init__(self, html=BING_HTML, *, status=200):
        self.html = html
        self.status = status
        self.requests = []

    def fetch_resource(self, url, timeout=10, max_bytes=2 * 1024 * 1024):
        self.requests.append((url, timeout, max_bytes))
        return TransportResponse(
            self.status,
            {"content-type": "text/html; charset=utf-8"},
            self.html.encode("utf-8"),
        )


def make_provider(fetcher=None, **kwargs):
    provider_class = import_module("info_assistant.providers.browser_search").BrowserSearchProvider
    return provider_class(fetcher or FixtureFetcher(), **kwargs)


def test_browser_search_queries_public_bing_page_and_extracts_organic_results() -> None:
    fetcher = FixtureFetcher()

    response = make_provider(fetcher).search(SearchQuery(query="空气净化器 & 价格", count=10))

    assert fetcher.requests == [
        (
            "https://cn.bing.com/search?q=%E7%A9%BA%E6%B0%94%E5%87%80%E5%8C%96%E5%99%A8+%26+%E4%BB%B7%E6%A0%BC",
            30,
            2 * 1024 * 1024,
        )
    ]
    assert response.provider == "必应网页搜索"
    assert [item.url for item in response.results] == [
        "https://example.com/a",
        "https://example.com/b",
    ]
    assert response.results[0].title == "官方资料"
    assert response.results[0].snippet == "空气净化器 产品说明。"
    assert all(item.is_evidence is False for item in response.results)


def test_browser_search_decodes_bing_redirect_without_visiting_result() -> None:
    target = base64.urlsafe_b64encode(b"https://example.com/official?lang=zh").decode().rstrip("=")
    html = f'<li class="b_algo"><h2><a href="/ck/a?u=a1{target}&amp;ntb=1">来源</a></h2></li>'
    fetcher = FixtureFetcher(html)

    response = make_provider(fetcher).search(SearchQuery(query="来源"))

    assert response.results[0].url == "https://example.com/official?lang=zh"
    assert len(fetcher.requests) == 1


def test_browser_search_filters_duplicates_and_unsafe_or_unresolved_links() -> None:
    links = [
        "javascript:alert(1)",
        "file:///private",
        "http://localhost/a",
        "http://10.1.2.3/a",
        "https://user:secret@example.com/a",
        "/search?q=other",
        "/ck/a?u=opaque",
        "https://example.com/a#one",
        "https://example.com/a#two",
        "https://example.com/b",
    ]
    html = "".join(f'<li class="b_algo"><h2><a href="{link}">标题</a></h2></li>' for link in links)

    response = make_provider(FixtureFetcher(html)).search(SearchQuery(query="公开信息"))

    assert [item.url for item in response.results] == [
        "https://example.com/a",
        "https://example.com/b",
    ]


def test_browser_search_baidu_uses_container_actual_url_instead_of_tracking_link() -> None:
    html = """<html><title>示例_百度搜索</title><body>
    <div class="result c-container" mu="https://example.com/actual">
    <h3><a href="https://www.baidu.com/link?url=opaque">官方网页</a></h3>
    <div class="c-abstract">产品的公开参数</div></div>
    <div class="c-container" data-landurl="https://example.com/second">
    <h3><a href="https://www.baidu.com/link?url=opaque2">另一网页</a></h3></div>
    </body></html>"""
    fetcher = FixtureFetcher(html)

    response = make_provider(fetcher, engine="baidu").search(SearchQuery(query="产品"))

    assert fetcher.requests[0][0] == "https://www.baidu.com/s?wd=%E4%BA%A7%E5%93%81"
    assert response.provider == "百度网页搜索"
    assert response.results[0].title == "官方网页"
    assert response.results[0].url == "https://example.com/actual"
    assert response.results[0].snippet == "产品的公开参数"
    assert response.results[1].url == "https://example.com/second"


@pytest.mark.parametrize("count, expected", [(0, 1), (-1, 1), (1, 1), (999, 20)])
def test_browser_search_limits_result_count(count, expected) -> None:
    html = "".join(
        f'<li class="b_algo"><h2><a href="https://example.com/{number}">结果 {number}</a></h2></li>'
        for number in range(30)
    )

    response = make_provider(FixtureFetcher(html)).search(SearchQuery(query="结果", count=count))

    assert len(response.results) == expected


@pytest.mark.parametrize(
    "html",
    [
        "<html><title>百度安全验证</title><body>请完成下方验证后继续访问</body></html>",
        "<html><title>Bing</title><body>One last step. "
        "Please solve the challenge below to continue.</body></html>",
        "<html><title>Access Denied</title><body>Request blocked</body></html>",
    ],
)
def test_browser_search_reports_challenge_pages_without_retrying(html) -> None:
    fetcher = FixtureFetcher(html)

    with pytest.raises(ProviderError, match="验证|访问受限"):
        make_provider(fetcher).search(SearchQuery(query="公开信息"))

    assert len(fetcher.requests) == 1


@pytest.mark.parametrize(
    "html, engine",
    [
        ('<ol id="b_results"><li class="b_no"><h1>没有与该词相关的结果。</h1></li></ol>', "bing"),
        ('<div class="nors"><p>抱歉，没有找到与该词相关的网页。</p></div>', "baidu"),
    ],
)
def test_browser_search_returns_empty_only_for_explicit_no_result_pages(html, engine) -> None:
    assert (
        make_provider(FixtureFetcher(html), engine=engine).search(SearchQuery(query="该词")).results
        == []
    )


def test_browser_search_reports_unrecognized_html_as_failure() -> None:
    fetcher = FixtureFetcher("<html><title>Bing</title><body>页面正在加载</body></html>")

    with pytest.raises(ProviderError, match="补充网址|页面结构"):
        make_provider(fetcher).search(SearchQuery(query="公开信息"))


@pytest.mark.parametrize(
    "freshness",
    [
        "invalid",
        "2026-02-30to2026-03-01",
        "2026-10-15to2026-10-01",
        "2026-1-1to2026-1-2",
    ],
)
def test_browser_search_rejects_invalid_time_filters_before_network(freshness) -> None:
    fetcher = FixtureFetcher()

    with pytest.raises(ProviderError, match="时间|发布时间"):
        make_provider(fetcher).search(SearchQuery(query="公开信息", freshness=freshness))

    assert fetcher.requests == []


def test_browser_search_maps_exact_dates_to_verified_public_bing_epoch_filter() -> None:
    fetcher = FixtureFetcher()

    response = make_provider(fetcher).search(
        SearchQuery(query="公开信息", freshness="2026-10-01to2026-10-15")
    )

    assert parse_qs(urlsplit(fetcher.requests[0][0]).query)["filters"] == ['ex1:"ez5_20727_20741"']
    assert response.results[0].is_evidence is False


def test_browser_search_maps_past_year_using_a_fixed_current_date(monkeypatch) -> None:
    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 15)

    monkeypatch.setattr(
        import_module("info_assistant.providers.browser_search"), "date", FixedDate, raising=False
    )
    fetcher = FixtureFetcher()

    response = make_provider(fetcher).search(SearchQuery(query="公开信息", freshness="py"))

    assert parse_qs(urlsplit(fetcher.requests[0][0]).query)["filters"] == ['ex1:"ez5_20376_20741"']
    assert response.results[0].is_evidence is False


@pytest.mark.parametrize(
    "freshness, expected", [("pd", 'ex1:"ez1"'), ("pw", 'ex1:"ez2"'), ("pm", 'ex1:"ez3"')]
)
def test_browser_search_uses_date_filters_observed_on_public_bing_page(freshness, expected) -> None:
    fetcher = FixtureFetcher()

    make_provider(fetcher).search(SearchQuery(query="公开信息", freshness=freshness))

    assert parse_qs(urlsplit(fetcher.requests[0][0]).query)["filters"] == [expected]


def test_browser_search_baidu_rejects_unsupported_time_filter() -> None:
    fetcher = FixtureFetcher()

    with pytest.raises(ProviderError, match="时间|发布时间"):
        make_provider(fetcher, engine="baidu").search(SearchQuery(query="公开信息", freshness="pw"))

    assert fetcher.requests == []


def test_browser_search_parses_real_public_bing_fixture() -> None:
    fixture = Path(__file__).parents[2] / "fixtures" / "bing_search_public.html"
    fetcher = FixtureFetcher(fixture.read_text(encoding="utf-8"))

    response = make_provider(fetcher).search(SearchQuery(query="公开信息", count=20))

    assert len(response.results) == 10
    assert all(item.title and item.url.startswith("https://") for item in response.results)
    assert any(item.snippet for item in response.results)


@pytest.mark.parametrize("status", [403, 429, 500])
def test_browser_search_http_failures_are_safe_and_explain_action(status) -> None:
    fetcher = FixtureFetcher("PRIVATE HTML BODY", status=status)

    with pytest.raises(ProviderError) as error:
        make_provider(fetcher).search(SearchQuery(query="PRIVATE QUERY"))

    assert "PRIVATE" not in str(error.value)
    assert len(fetcher.requests) == 1


def test_browser_search_network_errors_do_not_expose_query_or_page_body() -> None:
    class FailingFetcher:
        def fetch_resource(self, *args, **kwargs):
            raise FetchTransportError("PRIVATE QUERY or COOKIE")

    with pytest.raises(ProviderError) as error:
        make_provider(FailingFetcher()).search(SearchQuery(query="PRIVATE QUERY"))

    assert "PRIVATE" not in str(error.value)


def test_browser_search_rejects_blank_query_without_network() -> None:
    fetcher = FixtureFetcher()

    with pytest.raises(ProviderError):
        make_provider(fetcher).search(SearchQuery(query=" \n "))

    assert fetcher.requests == []


def test_browser_search_rejects_non_html_and_oversized_documents() -> None:
    class UnexpectedFetcher:
        def fetch_resource(self, *args, **kwargs):
            return TransportResponse(200, {"content-type": "application/json"}, b'{"x": "y"}')

    with pytest.raises(ProviderError):
        make_provider(UnexpectedFetcher()).search(SearchQuery(query="公开信息"))

    with pytest.raises(ProviderError):
        make_provider(FixtureFetcher("x" * (2 * 1024 * 1024 + 1))).search(
            SearchQuery(query="公开信息")
        )


def test_browser_search_rejects_unknown_engine() -> None:
    with pytest.raises(ValueError):
        make_provider(engine="unknown")
