from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from info_assistant.services.source_fetcher import SourceFetcher
from info_assistant.services.url_policy import UrlPolicy


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/robots.txt":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"User-agent: *\nDisallow: /blocked\n")
            return
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", f"http://private.test:{self.server.server_port}/private")
            self.end_headers()
            return
        if self.path in {"/list", "/repeat"}:
            body = (
                b'<html><head><link rel="next" href="/repeat"></head><body>same list</body></html>'
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/blocked/page":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"must not be fetched")
            return
        body = (
            b"<html><head><title>Pricing</title></head><body>"
            b"<main>Starter costs $12 per month.</main></body></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        return


def test_fetcher_reads_fixture_and_honors_robots_and_private_redirects() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    origin = f"http://fixture.test:{server.server_port}"

    def resolver(host: str, port: int) -> list[str]:
        return ["10.0.0.4"] if host == "private.test" else ["127.0.0.1"]

    policy = UrlPolicy(
        resolver=resolver,
        test_loopback_allowlist={"127.0.0.1"},
    )
    fetcher = SourceFetcher(policy, minimum_host_delay=0)
    try:
        page = fetcher.fetch(f"{origin}/page")
        blocked = fetcher.fetch(f"{origin}/blocked/page")
        redirected = fetcher.fetch(f"{origin}/redirect")
        repeated_pages = fetcher.fetch_pages(f"{origin}/list", max_pages=5)
    finally:
        server.shutdown()
        worker.join(timeout=2)
        server.server_close()

    assert page.status == "fetched"
    assert page.title == "Pricing"
    assert "$12 per month" in page.text
    assert blocked.status == "robots_blocked"
    assert redirected.status == "failed"
    assert redirected.error_code == "unsafe_redirect"
    assert len(repeated_pages) == 1
