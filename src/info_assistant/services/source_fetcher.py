from __future__ import annotations

import hashlib
import http.client
import re
import socket
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass
from email.message import Message
from html.parser import HTMLParser
from threading import Lock
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from info_assistant.services.pdf_reader import PdfReader, PdfReadError
from info_assistant.services.url_policy import PinnedTarget, UnsafeUrl, UrlPolicy

MAX_HTML_BYTES = 10 * 1024 * 1024
MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_REDIRECTS = 5
USER_AGENT = "NaturalLanguageInformationAssistant/0.1 (+local user initiated collection)"


@dataclass(frozen=True)
class TransportResponse:
    status: int
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True)
class FetchOutcome:
    status: str
    requested_url: str
    final_url: str
    text: str = ""
    title: str = ""
    content_type: str = ""
    error_code: str | None = None
    error_message: str | None = None
    http_status: int | None = None
    page_count: int | None = None
    next_url: str | None = None


class PinnedSocketTransport:
    """HTTP transport that dials the exact public IP validated by UrlPolicy."""

    def request(self, target: PinnedTarget, *, max_bytes: int, timeout: float) -> TransportResponse:
        if target.scheme == "https":
            connection = _PinnedHTTPSConnection(
                target.host, target.port, target.address, timeout=timeout
            )
        else:
            connection = _PinnedHTTPConnection(
                target.host, target.port, target.address, timeout=timeout
            )
        host_header = target.host
        if target.port not in ({80} if target.scheme == "http" else {443}):
            host_header = f"{host_header}:{target.port}"
        try:
            connection.request(
                "GET",
                target.path_query,
                headers={
                    "Host": host_header,
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/pdf,text/plain;q=0.9,*/*;q=0.1",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                },
            )
            response = connection.getresponse()
            body = response.read(max_bytes + 1)
            headers = {key.lower(): value for key, value in response.getheaders()}
            return TransportResponse(response.status, headers, body)
        except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
            raise FetchTransportError("source request failed") from exc
        finally:
            connection.close()


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, address: str, *, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._pinned_address = address

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._pinned_address, self.port), timeout=self.timeout
        )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, address: str, *, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._pinned_address = address

    def connect(self) -> None:
        sock = socket.create_connection((self._pinned_address, self.port), timeout=self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class FetchTransportError(Exception):
    pass


class _VisibleTextParser(HTMLParser):
    BLOCK_ELEMENTS = {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tr",
        "ul",
    }
    HIDDEN_ELEMENTS = {"script", "style", "noscript", "svg", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.next_link: str | None = None
        self._hidden_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        attributes = {name.lower(): value for name, value in attrs}
        relation = set((attributes.get("rel") or "").lower().split())
        if tag in {"a", "link"} and "next" in relation and attributes.get("href"):
            self.next_link = attributes["href"]
        if tag in self.HIDDEN_ELEMENTS:
            self._hidden_depth += 1
        if tag == "title":
            self._in_title = True
        if self._hidden_depth == 0 and tag in self.BLOCK_ELEMENTS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        if tag in self.HIDDEN_ELEMENTS:
            self._hidden_depth = max(0, self._hidden_depth - 1)
        if self._hidden_depth == 0 and tag in self.BLOCK_ELEMENTS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._hidden_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
        self.parts.append(data)

    def output(self) -> tuple[str, str]:
        text = re.sub(r"[ \t\f\v]+", " ", "".join(self.parts))
        text = re.sub(r" *\n *", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        title = " ".join("".join(self.title_parts).split())
        return text, title


class RobotsPolicy:
    def __init__(self, user_agent: str = USER_AGENT) -> None:
        self.user_agent = user_agent
        self._cache: dict[str, tuple[RobotFileParser | None, bool, float]] = {}
        self._lock = Lock()

    def allowed(
        self, url: str, load: Callable[[str], TransportResponse]
    ) -> tuple[bool, float, str | None]:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        with self._lock:
            cached = self._cache.get(origin)
        if cached is None:
            try:
                response = load(urljoin(origin, "/robots.txt"))
                if response.status in {404, 410}:
                    parser, available, delay = None, True, 0.0
                elif response.status in {401, 403} or response.status >= 500:
                    parser, available, delay = None, False, 0.0
                elif 200 <= response.status < 300:
                    parser = RobotFileParser()
                    parser.set_url(urljoin(origin, "/robots.txt"))
                    parser.parse(response.body.decode("utf-8", errors="replace").splitlines())
                    delay_value = parser.crawl_delay(self.user_agent)
                    delay = float(delay_value or 0)
                    available = True
                else:
                    parser, available, delay = None, False, 0.0
            except (FetchTransportError, UnsafeUrl, OSError):
                parser, available, delay = None, False, 0.0
            cached = (parser, available, delay)
            with self._lock:
                self._cache[origin] = cached
        parser, available, delay = cached
        if not available:
            return False, delay, "无法读取站点访问规则，已安全停止此来源"
        if parser is not None and not parser.can_fetch(self.user_agent, url):
            return False, delay, "站点 robots 规则不允许读取此页面"
        return True, delay, None


class SourceFetcher:
    def __init__(
        self,
        url_policy: UrlPolicy | None = None,
        transport: PinnedSocketTransport | None = None,
        minimum_host_delay: float = 1.0,
        max_html_bytes: int = MAX_HTML_BYTES,
        robots_policy: RobotsPolicy | None = None,
        pdf_reader: PdfReader | None = None,
    ) -> None:
        self.url_policy = url_policy or UrlPolicy()
        self.transport = transport or PinnedSocketTransport()
        self.minimum_host_delay = max(0, minimum_host_delay)
        self.max_html_bytes = max_html_bytes
        self.robots_policy = robots_policy or RobotsPolicy()
        self.pdf_reader = pdf_reader or PdfReader()
        self._last_request: dict[str, float] = {}
        self._pace_lock = Lock()

    def fetch(self, url: str, timeout: float = 30) -> FetchOutcome:
        try:
            target = self.url_policy.validate_and_pin(url)
        except UnsafeUrl as exc:
            return self._failure(url, url, "unsafe_url", str(exc))
        allowed, crawl_delay, reason = self.robots_policy.allowed(url, self._load_robots)
        if not allowed:
            return self._failure(
                url,
                url,
                "robots_blocked",
                reason or "robots blocked",
                status="robots_blocked",
            )
        self._pace(target.host, crawl_delay)
        response, final_target, error = self._request_redirects(
            target, timeout, MAX_PDF_BYTES, check_robots=True
        )
        if error:
            return self._failure(url, target.url, error[0], error[1])
        assert response is not None and final_target is not None
        return self._to_document(url, final_target.url, response)

    def fetch_pages(self, url: str, max_pages: int = 5) -> list[FetchOutcome]:
        pages: list[FetchOutcome] = []
        seen_urls: set[str] = set()
        seen_content: set[str] = set()
        current = url
        for _ in range(min(max(max_pages, 1), 10)):
            try:
                normalized = self.url_policy.validate_and_pin(current).url
            except UnsafeUrl:
                break
            if normalized in seen_urls:
                break
            seen_urls.add(normalized)
            outcome = self.fetch(normalized)
            if outcome.status != "fetched":
                if not pages:
                    pages.append(outcome)
                break
            digest = hashlib.sha256(outcome.text.encode("utf-8")).hexdigest()
            if digest in seen_content:
                break
            seen_content.add(digest)
            pages.append(outcome)
            if not outcome.next_url:
                break
            current = outcome.next_url
        return pages

    def fetch_resource(
        self, url: str, timeout: float = 10, max_bytes: int = 2 * 1024 * 1024
    ) -> TransportResponse:
        """Fetch a browser subresource through the same DNS-pinned transport."""
        target = self.url_policy.validate_and_pin(url)
        response, _, error = self._request_redirects(target, timeout, max_bytes, check_robots=False)
        if error:
            raise FetchTransportError(error[1])
        assert response is not None
        return response

    def _load_robots(self, url: str) -> TransportResponse:
        target = self.url_policy.validate_and_pin(url)
        response, _, error = self._request_redirects(target, 10, 512 * 1024, check_robots=False)
        if error:
            raise FetchTransportError(error[1])
        assert response is not None
        return response

    def _request_redirects(
        self,
        target: PinnedTarget,
        timeout: float,
        max_bytes: int,
        check_robots: bool,
    ) -> tuple[TransportResponse | None, PinnedTarget | None, tuple[str, str] | None]:
        current = target.url
        for redirect_count in range(MAX_REDIRECTS + 1):
            try:
                pinned = self.url_policy.validate_and_pin(current)
            except UnsafeUrl:
                return None, None, ("unsafe_redirect", "重定向目标不是允许访问的公开地址")
            try:
                response = self.transport.request(pinned, max_bytes=max_bytes, timeout=timeout)
            except FetchTransportError:
                return None, pinned, ("connection_failed", "连接失败或等待超时")
            if response.status in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location or redirect_count >= MAX_REDIRECTS:
                    return None, pinned, ("redirect_limit", "重定向次数过多或目标缺失")
                current = urljoin(current, location)
                try:
                    redirect_target = self.url_policy.validate_and_pin(current)
                except UnsafeUrl:
                    return None, pinned, ("unsafe_redirect", "重定向目标不是允许访问的公开地址")
                if check_robots:
                    allowed, delay, reason = self.robots_policy.allowed(current, self._load_robots)
                    if not allowed:
                        return None, pinned, ("robots_blocked", reason or "robots blocked")
                    self._pace(pinned.host, delay)
                current = redirect_target.url
                continue
            return response, pinned, None
        return None, None, ("redirect_limit", "重定向次数过多")

    def _pace(self, host: str, crawl_delay: float) -> None:
        wait_for = max(self.minimum_host_delay, crawl_delay)
        if wait_for <= 0:
            return
        with self._pace_lock:
            now = time.monotonic()
            prior = self._last_request.get(host)
            remaining = wait_for - (now - prior) if prior is not None else 0
            if remaining > 0:
                time.sleep(remaining)
            self._last_request[host] = time.monotonic()

    def _to_document(
        self, requested_url: str, final_url: str, response: TransportResponse
    ) -> FetchOutcome:
        if response.status < 200 or response.status >= 300:
            return self._failure(
                requested_url,
                final_url,
                f"http_{response.status}",
                f"来源返回 HTTP {response.status}",
                http_status=response.status,
            )
        if len(response.body) > MAX_PDF_BYTES:
            return self._failure(requested_url, final_url, "body_too_large", "来源超过大小上限")
        content_type = response.headers.get("content-type", "").split(";", maxsplit=1)[0].lower()
        if content_type == "application/pdf":
            try:
                document = self.pdf_reader.extract(response.body)
            except PdfReadError as exc:
                return self._failure(requested_url, final_url, exc.code, exc.message)
            return FetchOutcome(
                status="fetched",
                requested_url=requested_url,
                final_url=final_url,
                text=document.text,
                title=final_url.rsplit("/", maxsplit=1)[-1],
                content_type=content_type,
                http_status=response.status,
                page_count=document.page_count,
            )
        if content_type in {"text/html", "application/xhtml+xml"}:
            if len(response.body) > self.max_html_bytes:
                return self._failure(
                    requested_url, final_url, "body_too_large", "网页正文超过 10 MiB 上限"
                )
            encoding = self._charset(response.headers.get("content-type", ""))
            html = response.body.decode(encoding, errors="replace")
            parser = _VisibleTextParser()
            parser.feed(html)
            text, title = parser.output()
            if not text:
                return self._failure(
                    requested_url, final_url, "empty_document", "网页没有可读取的正文"
                )
            return FetchOutcome(
                status="fetched",
                requested_url=requested_url,
                final_url=final_url,
                text=text,
                title=title,
                content_type=content_type,
                http_status=response.status,
                next_url=(urljoin(final_url, parser.next_link) if parser.next_link else None),
            )
        if content_type.startswith("text/"):
            encoding = self._charset(response.headers.get("content-type", ""))
            text = response.body.decode(encoding, errors="replace").strip()
            return FetchOutcome(
                status="fetched",
                requested_url=requested_url,
                final_url=final_url,
                text=text,
                title=final_url.rsplit("/", maxsplit=1)[-1],
                content_type=content_type,
                http_status=response.status,
            )
        return self._failure(
            requested_url,
            final_url,
            "unsupported_content",
            f"暂不支持读取 {content_type or '未知类型'} 内容",
            status="unsupported",
            http_status=response.status,
        )

    @staticmethod
    def _charset(content_type: str) -> str:
        message = Message()
        message["content-type"] = content_type
        return message.get_content_charset() or "utf-8"

    @staticmethod
    def _failure(
        requested_url: str,
        final_url: str,
        code: str,
        message: str,
        *,
        status: str = "failed",
        http_status: int | None = None,
    ) -> FetchOutcome:
        return FetchOutcome(
            status=status,
            requested_url=requested_url,
            final_url=final_url,
            error_code=code,
            error_message=message,
            http_status=http_status,
        )
