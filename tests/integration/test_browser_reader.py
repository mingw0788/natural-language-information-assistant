from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from playwright.sync_api import sync_playwright

from info_assistant.services.browser_reader import BrowserReader
from info_assistant.services.source_fetcher import SourceFetcher
from info_assistant.services.url_policy import UrlPolicy


class DynamicHandler(BaseHTTPRequestHandler):
    requested_paths: list[str] = []

    def do_GET(self) -> None:
        self.requested_paths.append(self.path)
        if self.path == "/robots.txt":
            self.send_response(404)
            self.end_headers()
            return
        html = (
            "<html><head><title>Dynamic fixture</title></head><body>"
            "<main id='target'>not loaded</main>"
            "<script>document.querySelector('#target').textContent='dynamic value loaded';"
            f"fetch('http://private.test:{self.server.server_port}/secret').catch(()=>{{}});"
            "</script></body></html>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def log_message(self, format: str, *args) -> None:
        return


def test_browser_renders_dynamic_text_and_blocks_private_subrequest() -> None:
    DynamicHandler.requested_paths = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), DynamicHandler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    origin = f"http://fixture.test:{server.server_port}"

    def resolver(host: str, port: int) -> list[str]:
        return ["10.0.0.4"] if host == "private.test" else ["127.0.0.1"]

    fetcher = SourceFetcher(
        UrlPolicy(resolver=resolver, test_loopback_allowlist={"127.0.0.1"}),
        minimum_host_delay=0,
    )
    with sync_playwright() as playwright:
        browser_channel = (
            None if Path(playwright.chromium.executable_path).is_file() else "msedge"
        )
    try:
        result = BrowserReader(fetcher, browser_channel=browser_channel).render(
            f"{origin}/dynamic", timeout=20
        )
    finally:
        server.shutdown()
        worker.join(timeout=2)
        server.server_close()

    assert result.status == "fetched"
    assert "dynamic value loaded" in result.text
    assert "/secret" not in DynamicHandler.requested_paths
