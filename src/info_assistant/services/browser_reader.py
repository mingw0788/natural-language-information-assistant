from __future__ import annotations

import time
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from info_assistant.services.source_fetcher import FetchOutcome, SourceFetcher


class BrowserReader:
    def __init__(
        self,
        fetcher: SourceFetcher,
        max_resources: int = 40,
        max_resource_bytes: int = 2 * 1024 * 1024,
        max_total_bytes: int = 10 * 1024 * 1024,
        browser_channel: str | None = None,
    ) -> None:
        self.fetcher = fetcher
        self.max_resources = max_resources
        self.max_resource_bytes = max_resource_bytes
        self.max_total_bytes = max_total_bytes
        self.browser_channel = browser_channel

    def render(self, url: str, timeout: float = 45) -> FetchOutcome:
        initial = self.fetcher.fetch(url, timeout=min(timeout, 30))
        if initial.status != "fetched":
            return initial
        counter = {"resources": 0, "bytes": 0}
        deadline = time.monotonic() + timeout
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    headless=True,
                    channel=self.browser_channel,
                )
                try:
                    context = browser.new_context(
                        service_workers="block",
                        accept_downloads=False,
                        java_script_enabled=True,
                    )
                    context.route("**/*", lambda route: self._route_resource(route, counter))
                    page = context.new_page()
                    page.on("dialog", lambda dialog: dialog.dismiss())
                    remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
                    page.goto(url, wait_until="domcontentloaded", timeout=remaining_ms)
                    page.wait_for_timeout(
                        min(500, max(0, int((deadline - time.monotonic()) * 1000)))
                    )
                    text = page.locator("body").inner_text(
                        timeout=max(1, int((deadline - time.monotonic()) * 1000))
                    )
                    title = page.title()
                    page.close()
                    context.close()
                    if not text.strip():
                        return initial
                    return FetchOutcome(
                        status="fetched",
                        requested_url=url,
                        final_url=page.url,
                        text=text.strip()[:2_000_000],
                        title=title[:500] or initial.title,
                        content_type="text/html",
                        http_status=initial.http_status,
                    )
                finally:
                    browser.close()
        except (PlaywrightError, OSError, TimeoutError):
            return FetchOutcome(
                status="fetched",
                requested_url=url,
                final_url=initial.final_url,
                text=initial.text,
                title=initial.title,
                content_type=initial.content_type,
                http_status=initial.http_status,
                error_code="browser_fallback",
                error_message="动态读取未完成，已保留静态正文",
            )

    def _route_resource(self, route, counter: dict[str, int]) -> None:
        request = route.request
        if request.method != "GET":
            route.abort()
            return
        if urlsplit(request.url).scheme.lower() not in {"http", "https"}:
            route.abort()
            return
        counter["resources"] += 1
        if counter["resources"] > self.max_resources:
            route.abort()
            return
        try:
            response = self.fetcher.fetch_resource(
                request.url,
                timeout=10,
                max_bytes=self.max_resource_bytes,
            )
        except Exception:
            route.abort()
            return
        counter["bytes"] += len(response.body)
        if counter["bytes"] > self.max_total_bytes:
            route.abort()
            return
        safe_headers = {
            key: value
            for key, value in response.headers.items()
            if key in {"content-type", "cache-control", "last-modified", "etag"}
        }
        try:
            route.fulfill(status=response.status, headers=safe_headers, body=response.body)
        except PlaywrightError:
            route.abort()
