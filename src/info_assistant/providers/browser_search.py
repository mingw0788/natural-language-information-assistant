from __future__ import annotations

import base64
import binascii
import ipaddress
import re
from datetime import date, timedelta
from email.message import Message
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from info_assistant.providers.base import ProviderError, SearchQuery, SearchResponse, SearchResult
from info_assistant.services.source_fetcher import FetchTransportError, SourceFetcher
from info_assistant.services.url_policy import UnsafeUrl

MAX_SEARCH_BYTES = 2 * 1024 * 1024
_ENGINE_URLS = {"bing": "https://cn.bing.com/search", "baidu": "https://www.baidu.com/s"}


class BrowserSearchProvider:
    """Read a user-requested public search page without using a search API or API key."""

    def __init__(
        self,
        fetcher: SourceFetcher,
        engine: str = "bing",
        browser_channel: str | None = None,
        timeout_seconds: float = 30,
    ) -> None:
        if engine not in _ENGINE_URLS:
            raise ValueError("engine must be bing or baidu")
        self.fetcher = fetcher
        self.engine = engine
        self.browser_channel = browser_channel
        self.timeout_seconds = timeout_seconds

    def search(self, query: SearchQuery) -> SearchResponse:
        if not query.query.strip():
            raise ProviderError("搜索内容不能为空")
        parameters = {"q" if self.engine == "bing" else "wd": query.query}
        if query.freshness:
            if self.engine != "bing":
                raise ProviderError("普通网页搜索暂不支持该发布时间筛选，请移除时间条件或补充网址")
            # These parameters are the links offered by Bing's public date menu.
            # Search-page filtering discovers candidates; it is not publication-date evidence.
            parameters["filters"] = _bing_time_filter(query.freshness)
        search_url = f"{_ENGINE_URLS[self.engine]}?{urlencode(parameters)}"
        try:
            # This mode opens the requested search page. Source documents are subsequently
            # read with SourceFetcher.fetch(), which applies the site's robots policy.
            response = self.fetcher.fetch_resource(
                search_url, timeout=self.timeout_seconds, max_bytes=MAX_SEARCH_BYTES
            )
        except (FetchTransportError, UnsafeUrl, OSError, TimeoutError, ValueError):
            raise ProviderError("搜索页面连接失败，请稍后重试或补充网址") from None
        if response.status in {401, 403, 429}:
            raise ProviderError("搜索页面访问受限或需要验证，请打开搜索页面查看或补充网址")
        if not 200 <= response.status < 300:
            raise ProviderError("搜索页面暂时不可用，请稍后重试或补充网址")
        if len(response.body) > MAX_SEARCH_BYTES:
            raise ProviderError("搜索页面超过读取大小上限，请缩小查询范围或补充网址")
        content_type = response.headers.get("content-type", "")
        if content_type and content_type.split(";", 1)[0].lower() not in {
            "text/html",
            "application/xhtml+xml",
        }:
            raise ProviderError("搜索页面返回了无法读取的内容，请打开搜索页面或补充网址")
        message = Message()
        message["content-type"] = content_type
        try:
            html = response.body.decode(message.get_content_charset() or "utf-8", errors="replace")
        except LookupError:
            html = response.body.decode("utf-8", errors="replace")
        parser = _SearchPageParser(self.engine)
        try:
            parser.feed(html)
            parser.close()
        except (ValueError, RecursionError):
            raise ProviderError("搜索页面结构无法识别，请打开搜索页面或补充网址") from None
        if parser.is_challenge():
            raise ProviderError("搜索页面需要安全验证或访问受限，请打开搜索页面查看或补充网址")
        results = []
        seen = set()
        limit = min(max(query.count, 1), 20)
        for entry in parser.entries:
            url = _result_url(entry["actual_url"] or entry["href"], search_url)
            title = _clean(entry["title"])
            if not url or url in seen or not title or entry["advertisement"]:
                continue
            seen.add(url)
            results.append(
                SearchResult(title[:500], url, _clean(entry["snippet"])[:2_000], is_evidence=False)
            )
            if len(results) >= limit:
                break
        if not results and not parser.is_explicitly_empty():
            raise ProviderError("搜索页面结构发生变化或暂时无法读取，请打开搜索页面或补充网址")
        name = "必应网页搜索" if self.engine == "bing" else "百度网页搜索"
        return SearchResponse(results=results, provider=name)


def _clean(parts: list[str]) -> str:
    return " ".join("".join(parts).split())


def _bing_time_filter(freshness: str) -> str:
    presets = {"pd": 'ex1:"ez1"', "pw": 'ex1:"ez2"', "pm": 'ex1:"ez3"'}
    if freshness in presets:
        return presets[freshness]
    if freshness == "py":
        end = date.today()
        start = end - timedelta(days=365)
    else:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}to\d{4}-\d{2}-\d{2}", freshness):
            raise ProviderError("搜索时间范围格式无效")
        start_text, end_text = freshness.split("to")
        try:
            start, end = date.fromisoformat(start_text), date.fromisoformat(end_text)
        except ValueError:
            raise ProviderError("搜索时间范围包含无效日期") from None
        if start > end:
            raise ProviderError("搜索时间范围的开始日期不能晚于结束日期")
    epoch = date(1970, 1, 1)
    return f'ex1:"ez5_{(start - epoch).days}_{(end - epoch).days}"'


def _result_url(value: str, search_url: str) -> str | None:
    if not value or len(value) > 4_096 or any(ord(char) < 32 for char in value):
        return None
    try:
        candidate = urljoin(search_url, value)
        parts = urlsplit(candidate)
        host = (parts.hostname or "").casefold().rstrip(".")
        if host in {"bing.com", "www.bing.com", "cn.bing.com"} and parts.path == "/ck/a":
            encoded = parse_qs(parts.query).get("u", [""])[0]
            if not encoded.startswith("a1"):
                return None
            encoded = encoded[2:]
            value_bytes = base64.b64decode(
                encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True
            )
            parts = urlsplit(value_bytes.decode("utf-8"))
            host = (parts.hostname or "").casefold().rstrip(".")
        if parts.scheme not in {"http", "https"} or not host:
            return None
        if parts.username is not None or parts.password is not None:
            return None
        if host in {
            "localhost",
            "localhost.localdomain",
            "bing.com",
            "www.bing.com",
            "cn.bing.com",
        }:
            return None
        if host.endswith((".localhost", ".local", ".internal")):
            return None
        if host in {"www.baidu.com", "baidu.com"} and parts.path in {"/link", "/s", "/"}:
            return None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            if not address.is_global:
                return None
        return parts._replace(fragment="").geturl()
    except (ValueError, UnicodeError, binascii.Error):
        return None


class _SearchPageParser(HTMLParser):
    _VOID = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
    _HIDDEN = {"script", "style", "noscript", "template", "svg"}

    def __init__(self, engine: str) -> None:
        super().__init__(convert_charrefs=True)
        self.engine = engine
        self.stack: list[tuple[str, set[str]]] = []
        self.entries: list[dict] = []
        self.current: dict | None = None
        self.title: list[str] = []
        self.visible_text: list[str] = []
        self.no_result_marker = False

    def handle_starttag(self, tag: str, attrs) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if classes & {"b_no", "nors", "no-results"}:
            self.no_result_marker = True
        if tag in self._VOID:
            return
        self.stack.append((tag, classes))
        depth = len(self.stack)
        if self.current is None and (
            (self.engine == "bing" and tag == "li" and "b_algo" in classes)
            or (self.engine == "baidu" and tag == "div" and "c-container" in classes)
        ):
            self.current = {
                "depth": depth,
                "heading_depth": 0,
                "anchor_depth": 0,
                "snippet_depth": 0,
                "title": [],
                "snippet": [],
                "href": "",
                "actual_url": attributes.get("mu") or attributes.get("data-landurl") or "",
                "advertisement": False,
            }
        entry = self.current
        if entry is None:
            return
        if classes & {"b_ad", "ec-tuiguang", "ec_tuiguang", "ad-label"}:
            entry["advertisement"] = True
        if tag == ("h2" if self.engine == "bing" else "h3"):
            entry["heading_depth"] = depth
        if tag == "a" and entry["heading_depth"] and not entry["href"]:
            entry["href"] = attributes.get("href") or ""
            entry["anchor_depth"] = depth
        if (
            self.engine == "bing"
            and tag == "p"
            and any("b_caption" in item[1] for item in self.stack)
        ) or (self.engine == "baidu" and "c-abstract" in classes):
            entry["snippet_depth"] = depth

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break
        entry = self.current
        if entry is None:
            return
        depth = len(self.stack)
        if entry["depth"] > depth:
            self.entries.append(entry)
            self.current = None
            return
        for key in ("heading_depth", "anchor_depth", "snippet_depth"):
            if entry[key] > depth:
                entry[key] = 0

    def handle_data(self, data: str) -> None:
        if any(tag in self._HIDDEN for tag, classes in self.stack):
            return
        self.visible_text.append(data)
        if any(tag == "title" for tag, classes in self.stack):
            self.title.append(data)
        if self.current:
            if self.current["anchor_depth"]:
                self.current["title"].append(data)
            if self.current["snippet_depth"]:
                self.current["snippet"].append(data)

    def is_challenge(self) -> bool:
        title = _clean(self.title).casefold().rstrip(".!。 ")
        if title in {
            "百度安全验证",
            "安全验证",
            "验证码",
            "access denied",
            "verify you are human",
            "just a moment",
            "robot check",
        }:
            return True
        if self.entries:
            return False
        text = _clean(self.visible_text).casefold()
        return any(
            marker in text
            for marker in (
                "请完成下方验证",
                "请输入验证码",
                "请点击下方验证",
                "verify you are human",
                "please solve the challenge below",
                "unusual traffic from your computer network",
            )
        )

    def is_explicitly_empty(self) -> bool:
        text = _clean(self.visible_text).casefold()
        return self.no_result_marker and any(
            marker in text
            for marker in (
                "没有找到",
                "没有与",
                "未找到",
                "no results",
                "there are no results",
                "did not match any",
                "couldn't find",
            )
        )
