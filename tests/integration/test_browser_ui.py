from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parents[2]
APP_JS = ROOT / "src" / "info_assistant" / "web" / "static" / "app.js"


class HarnessHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/app.js":
            content = APP_JS.read_bytes()
            content_type = "text/javascript; charset=utf-8"
        else:
            content = (
                "<!doctype html><html><head><meta name='csrf-token' content='test'></head>"
                "<body><main id='task-page' data-task-id='task-1'>"
                "<div id='task-detail-capability'></div><button id='task-page-start'></button>"
                "<div id='task-page-run'></div><p id='task-page-error'></p>"
                "<select id='result-sort'><option value='name_asc'>升序</option>"
                "<option value='name_desc'>降序</option></select>"
                "<button id='compare-button'>比较选中对象</button>"
                "<p id='selected-count'></p><div id='compare-results' class='hidden'></div>"
                "<div id='task-page-results'></div><script src='/app.js'></script>"
                "</main></body></html>"
            ).encode()
            content_type = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args) -> None:
        return


def test_result_selection_survives_refresh_and_comparison_is_side_by_side() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), HarnessHandler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    items = [
        {
            "entity_id": "entity-a",
            "entity": "Alpha",
            "status": "confirmed",
            "observations": [
                {
                    "field": "价格",
                    "value": "每月 10 元",
                    "status": "confirmed",
                    "evidence": [
                        {
                            "title": "Alpha 官网",
                            "url": "https://alpha.example",
                            "quote": "每月 10 元",
                        }
                    ],
                }
            ],
            "conflicts": [],
        },
        {
            "entity_id": "entity-b",
            "entity": "Beta",
            "status": "pending_review",
            "observations": [],
            "conflicts": [],
        },
    ]
    def mock_api(route) -> None:
        path = route.request.url
        if path.endswith("/api/tasks/task-1"):
            payload = {"capability": {"missing": []}, "current_run": None}
        else:
            payload = {"run_id": "run-1", "run_status": "completed", "items": items, "view": "all"}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    try:
        with sync_playwright() as playwright:
            channel = None if Path(playwright.chromium.executable_path).is_file() else "msedge"
            browser = playwright.chromium.launch(headless=True, channel=channel)
            page = browser.new_page()
            page.route(f"{origin}/api/**", mock_api)
            page.goto(origin, wait_until="networkidle")
            page.locator(".export-selection").nth(0).check()
            page.locator(".export-selection").nth(1).check()
            with page.expect_response(f"{origin}/api/tasks/task-1/results"):
                page.locator("#result-sort").select_option("name_desc")
            assert page.locator(".export-selection:checked").count() == 2
            assert page.locator("#selected-count").inner_text() == "已选 2 个对象"
            page.locator("#compare-button").click()
            page.locator("#compare-results .comparison-table").wait_for()
            assert "Alpha" in page.locator("#compare-results").inner_text()
            assert "Beta" in page.locator("#compare-results").inner_text()
            assert "未提供" in page.locator("#compare-results").inner_text()
            assert page.locator("#compare-results a").get_attribute("href") == "https://alpha.example"
            browser.close()
    finally:
        server.shutdown()
        worker.join(timeout=2)
        server.server_close()
