from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).parents[2]
WEB = ROOT / "src" / "info_assistant" / "web"


@pytest.fixture
def provider_page():
    settings = {
        "search_provider": "browser",
        "search_provider_label": "免API网页搜索（必应）",
        "search_signup_url": "https://www.bing.com/",
        "browser_search_engine": "bing",
        "search_configured": True,
        "configured_search_providers": ["browser", "baidu"],
        "model_preset": "none",
        "model_configured": False,
        "model_base_url": "",
        "model_name": "",
        "model_json_mode": "json_object",
    }
    templates = Environment(
        loader=FileSystemLoader(WEB / "templates"), autoescape=select_autoescape()
    )
    html = templates.get_template("settings.html").render(
        csrf_token="test-token", providers=settings
    )
    task_html = templates.get_template("task.html").render(
        csrf_token="test-token",
        task={"id": "task-1", "latest_version": {"number": 1, "spec": {"raw_input": "测试"}}},
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/static/app.js":
                content = (WEB / "static" / "app.js").read_bytes()
                content_type = "text/javascript"
            elif self.path == "/static/app.css":
                content = (WEB / "static" / "app.css").read_bytes()
                content_type = "text/css"
            elif self.path.startswith("/tasks/"):
                content = task_html.encode()
                content_type = "text/html; charset=utf-8"
            else:
                content = html.encode()
                content_type = "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with sync_playwright() as playwright:
            channel = None if Path(playwright.chromium.executable_path).is_file() else "msedge"
            browser = playwright.chromium.launch(headless=True, channel=channel)
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.goto(f"http://127.0.0.1:{server.server_port}/settings", wait_until="networkidle")
            yield page, settings
            browser.close()
    finally:
        server.shutdown()
        worker.join(timeout=2)
        server.server_close()


def test_switching_search_does_not_reuse_another_services_key(provider_page):
    page, saved = provider_page
    page.locator("#search-provider").select_option("baidu")
    expect(page.locator("#search-state")).to_contain_text("百度搜索已配置")
    page.locator("#search-secret").fill("unsaved-baidu-key")
    page.locator("#clear-search").check()
    page.locator("#search-provider").select_option("brave")

    expect(page.locator("#search-secret")).to_have_value("")
    expect(page.locator("#clear-search")).not_to_be_checked()
    expect(page.locator("#search-state")).to_contain_text("Brave Search尚未配置")
    page.locator("#search-secret").fill("new-brave-key")
    requests = []

    def save(route):
        requests.append(route.request.post_data_json)
        response = {
            **saved,
            "search_provider": "brave",
            "search_provider_label": "Brave Search",
            "search_signup_url": "https://brave.com/search/api/",
            "configured_search_providers": ["browser", "baidu", "brave"],
        }
        route.fulfill(status=200, content_type="application/json", body=json.dumps(response))

    page.route("**/api/settings/providers", save)
    page.get_by_role("button", name="保存设置").click()
    expect(page.locator("#settings-message")).to_contain_text("设置已保存")
    assert requests[0]["search_provider"] == "brave"
    assert requests[0]["search_secret"] == "new-brave-key"
    expect(page.locator("#search-secret")).to_have_value("")
    expect(page.locator("#search-state")).to_contain_text("Brave Search已配置")
    page.locator("#search-provider").select_option("baidu")
    expect(page.locator("#search-state")).to_contain_text("百度搜索已配置")


def test_domestic_model_preset_sends_choice_and_clears_unsaved_custom_key(provider_page):
    page, saved = provider_page
    page.locator("#model-preset").select_option("custom")
    expect(page.locator("#custom-model-fields")).to_be_visible()
    page.locator("#model-base-url").fill("https://model.example.com/v1")
    page.locator("#model-name").fill("custom-chat")
    page.locator("#model-secret").fill("unsaved-custom-key")
    page.locator("#clear-model").check()
    page.locator("#model-preset").select_option("zhipu_free")

    expect(page.locator("#custom-model-fields")).not_to_be_visible()
    expect(page.locator("#model-secret")).to_have_value("")
    expect(page.locator("#clear-model")).not_to_be_checked()
    expect(page.locator("#model-state")).to_contain_text("尚未配置")
    page.locator("#model-secret").fill("new-zhipu-key")
    requests = []

    def save(route):
        requests.append(route.request.post_data_json)
        response = {
            **saved,
            "model_preset": "zhipu_free",
            "model_configured": True,
            "model_base_url": "https://open.bigmodel.cn/api/paas/v4",
            "model_name": "glm-4.7-flash",
            "model_json_mode": "json_object",
        }
        route.fulfill(status=200, content_type="application/json", body=json.dumps(response))

    page.route("**/api/settings/providers", save)
    page.get_by_role("button", name="保存设置").click()
    expect(page.locator("#settings-message")).to_contain_text("设置已保存")
    assert requests[0]["model_preset"] == "zhipu_free"
    assert requests[0]["model_secret"] == "new-zhipu-key"
    assert "model_base_url" not in requests[0]
    assert "model_name" not in requests[0]
    expect(page.locator("#model-state")).to_contain_text("已配置")
    expect(page.locator("#model-secret")).to_have_value("")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_default_search_and_local_reading_save_without_any_keys(provider_page):
    page, saved = provider_page
    expect(page.locator("#search-provider")).to_have_value("browser")
    expect(page.locator("#search-secret")).not_to_be_visible()
    expect(page.locator("#model-preset")).to_have_value("none")
    expect(page.locator("#model-secret")).not_to_be_visible()
    page.locator("#browser-search-engine").select_option("baidu")
    requests = []

    def save(route):
        requests.append(route.request.post_data_json)
        response = {**saved, "browser_search_engine": "baidu"}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(response))

    page.route("**/api/settings/providers", save)
    page.get_by_role("button", name="保存设置").click()
    expect(page.locator("#settings-message")).to_contain_text("设置已保存")
    assert requests == [{
        "search_provider": "browser",
        "browser_search_engine": "baidu",
        "model_preset": "none",
        "clear_model": True,
    }]
    expect(page.locator("#search-state")).to_contain_text("无需密钥")
    expect(page.locator("#provider-data-flow")).to_contain_text("百度")


def test_source_excerpts_show_without_entities_and_cannot_inject_html(provider_page):
    page, _ = provider_page
    excerpt = '网页原文 <img src=x onerror="window.injected=true">'

    def results(route):
        if route.request.url.endswith("/results"):
            payload = {
                "items": [],
                "sources": [{
                    "title": "原始来源 <script>window.injected=true</script>",
                    "url": "https://example.com/official",
                    "excerpt": excerpt,
                    "fetched_at": "2026-10-15T01:00:00Z",
                    "snapshot_id": "snapshot-1",
                }],
            }
        else:
            payload = {"capability": {"missing": []}, "current_run": None}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    page.route("**/api/**", results)
    origin = page.url.removesuffix("/settings")
    page.goto(f"{origin}/tasks/task-1", wait_until="networkidle")
    expect(page.locator(".source-card")).to_contain_text(excerpt)
    assert page.locator(".source-card a").get_attribute("href") == "https://example.com/official"
    assert page.locator(".source-card img, .source-card script").count() == 0
    assert page.evaluate("window.injected === undefined")
    assert page.locator(".export-selection").count() == 0
