from __future__ import annotations

import time

from fastapi.testclient import TestClient

from info_assistant.main import AppSettings, create_app
from info_assistant.providers.base import SearchResponse
from info_assistant.services.credentials import InMemoryCredentialStore
from info_assistant.services.source_fetcher import FetchOutcome, TransportResponse


def wait_for_run(client, run_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = client.get(f"/api/runs/{run_id}").json()
        if state["status"] not in {"queued", "running"}:
            return state
        time.sleep(0.02)
    raise AssertionError("background collection did not finish")


def test_default_dispatch_reads_public_search_html_and_keeps_models_off(tmp_path):
    class PublicPages:
        def __init__(self):
            self.search_urls = []

        def fetch_resource(self, url, **kwargs):
            self.search_urls.append(url)
            return TransportResponse(200, {"content-type": "text/html"}, (
                '<html><title>搜索</title><li class="b_algo"><h2>'
                '<a href="https://example.test/info">公开来源</a></h2>'
                '<div class="b_caption"><p>搜索摘要</p></div></li></html>'
            ).encode())

        def fetch(self, url, **kwargs):
            return FetchOutcome(
                "fetched", url, url, text="实际来源正文", title="公开来源",
                content_type="text/plain", http_status=200,
            )

    pages = PublicPages()
    app = create_app(AppSettings(
        data_dir=tmp_path, credentials=InMemoryCredentialStore(), fetcher=pages,
    ))
    with TestClient(app) as client:
        headers = {"Origin": "http://testserver", "X-CSRF-Token": app.state.csrf_token}
        task = client.post(
            "/api/tasks", json={"raw_input": "收集公开资料"}, headers=headers,
        ).json()
        run = client.post(f"/api/tasks/{task['id']}/runs", headers=headers).json()
        assert wait_for_run(client, run["id"])["status"] == "completed"
        result = client.get(f"/api/tasks/{task['id']}/results").json()

    assert pages.search_urls
    assert all(url.startswith("https://cn.bing.com/search?") for url in pages.search_urls)
    assert result["sources"][0]["excerpt"] == "实际来源正文"
    assert result["items"] == []
    assert app.state.runner.model_provider is None
    assert app.state.ledger.snapshot(run["id"]).used.get("model_calls", 0) == 0


def test_saved_api_choice_is_used_at_dispatch_then_can_switch_back_to_no_api(
    tmp_path, monkeypatch,
):
    calls = []

    def baidu_search(self, query):
        calls.append(self.credentials.get_secret("baidu-search"))
        return SearchResponse([], "baidu")

    monkeypatch.setattr("info_assistant.main.BaiduSearchProvider.search", baidu_search)
    app = create_app(AppSettings(
        data_dir=tmp_path, credentials=InMemoryCredentialStore(),
    ))
    with TestClient(app) as client:
        headers = {"Origin": "http://testserver", "X-CSRF-Token": app.state.csrf_token}
        saved = client.put("/api/settings/providers", json={
            "search_provider": "baidu", "search_secret": "baidu-only-secret",
        }, headers=headers)
        assert saved.status_code == 200
        task = client.post(
            "/api/tasks", json={"raw_input": "收集公开资料"}, headers=headers,
        ).json()
        run = client.post(f"/api/tasks/{task['id']}/runs", headers=headers).json()
        wait_for_run(client, run["id"])
        switched = client.put("/api/settings/providers", json={
            "search_provider": "browser", "browser_search_engine": "baidu",
            "model_preset": "none",
        }, headers=headers)

    assert calls and all(secret == "baidu-only-secret" for secret in calls)
    assert switched.status_code == 200
    assert switched.json()["search_configured"] is True
    assert switched.json()["has_search_secret"] is False
    assert app.state.provider_settings.get_search_secret() is None
