from __future__ import annotations

import time

from fastapi.testclient import TestClient

from info_assistant.main import AppSettings, create_app
from info_assistant.services.credentials import InMemoryCredentialStore
from info_assistant.services.source_fetcher import FetchOutcome


class FakeFetcher:
    def fetch(self, url: str, timeout: float = 30) -> FetchOutcome:
        return FetchOutcome(
            status="fetched",
            requested_url=url,
            final_url=url,
            title="Product pricing",
            text="Acme\n价格：每月 12 元",
            content_type="text/html",
            http_status=200,
        )


def client_for(tmp_path):
    app = create_app(
        AppSettings(
            data_dir=tmp_path,
            credentials=InMemoryCredentialStore(),
            auto_dispatch=False,
        )
    )
    client = TestClient(app)
    return client, {"Origin": "http://testserver", "X-CSRF-Token": app.state.csrf_token}


def test_create_direct_source_task_and_list_history(tmp_path) -> None:
    client, headers = client_for(tmp_path)

    created = client.post(
        "/api/tasks",
        json={
            "raw_input": "查这个产品的价格：https://example.test/pricing",
            "supplemental_material": "",
        },
        headers=headers,
    )
    assert created.status_code == 201
    task = created.json()
    assert task["spec"]["sources"][0]["value"] == "https://example.test/pricing"
    assert task["capability"]["ready"] is True

    run = client.post(f"/api/tasks/{task['id']}/runs", headers=headers)
    assert run.status_code == 202
    assert run.json()["status"] == "queued"

    history = client.get("/api/history")
    assert history.status_code == 200
    assert history.json()["total"] == 1
    assert history.json()["items"][0]["id"] == task["id"]


def test_missing_search_configuration_is_reported_before_run(tmp_path) -> None:
    client, headers = client_for(tmp_path)
    created = client.post(
        "/api/tasks",
        json={"raw_input": "列出几款本地 AI 编程工具", "supplemental_material": ""},
        headers=headers,
    )

    assert created.status_code == 201
    assert created.json()["capability"]["ready"] is False
    response = client.post(f"/api/tasks/{created.json()['id']}/runs", headers=headers)
    assert response.status_code == 409
    assert response.json()["detail"]["missing"]


def test_recent_time_window_is_frozen_at_each_run_start(tmp_path) -> None:
    client, headers = client_for(tmp_path)
    task = client.post(
        "/api/tasks",
        json={
            "raw_input": "查最近的价格变化：https://example.test/pricing",
            "supplemental_material": "",
        },
        headers=headers,
    ).json()
    first = client.post(f"/api/tasks/{task['id']}/runs", headers=headers).json()
    first_task_version = client.get(f"/api/tasks/{task['id']}").json()["latest_version"]["id"]
    first_spec = client.app.state.repository.spec_for_run(first["id"])
    second = client.post(f"/api/tasks/{task['id']}/runs", headers=headers).json()
    second_spec = client.app.state.repository.spec_for_run(second["id"])

    assert first["version_id"] == first_task_version
    assert second["version_id"] != first["version_id"]
    assert first_spec.time_window is not None
    assert second_spec.time_window is not None
    assert second_spec.time_window.end >= first_spec.time_window.end
    assert any("运行开始时刻前 7 天" in item for item in second_spec.assumptions)


def test_run_control_routes_pause_resume_and_cancel_without_resetting_usage(tmp_path) -> None:
    client, headers = client_for(tmp_path)
    task = client.post(
        "/api/tasks",
        json={
            "raw_input": "读取来源：https://example.test/page",
            "supplemental_material": "",
        },
        headers=headers,
    ).json()
    run = client.post(f"/api/tasks/{task['id']}/runs", headers=headers).json()
    client.app.state.control.start(run["id"])

    paused = client.post(f"/api/runs/{run['id']}/pause", headers=headers)
    task_detail = client.get(f"/api/tasks/{task['id']}").json()
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"
    assert task_detail["current_run"]["status"] == "paused"

    resumed = client.post(f"/api/runs/{run['id']}/resume", headers=headers)
    assert resumed.status_code == 202
    assert resumed.json()["status"] == "queued"
    client.app.state.control.start(run["id"])
    cancelled = client.post(f"/api/runs/{run['id']}/cancel", headers=headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_provider_settings_never_return_secret_and_require_csrf(tmp_path) -> None:
    client, headers = client_for(tmp_path)
    secret = "do-not-return-this-secret"
    updated = client.put(
        "/api/settings/providers",
        json={"search_secret": secret},
        headers=headers,
    )
    assert updated.status_code == 200
    assert secret not in updated.text
    assert updated.json()["search_configured"] is True

    denied = client.put("/api/settings/providers", json={"search_secret": "other-secret"})
    assert denied.status_code == 403


def test_foreign_host_and_origin_are_rejected(tmp_path) -> None:
    client, headers = client_for(tmp_path)
    foreign_host = client.get("/healthz", headers={"Host": "attacker.example"})
    assert foreign_host.status_code == 400

    mismatched_origin = client.post(
        "/api/tasks",
        json={"raw_input": "test", "supplemental_material": ""},
        headers={**headers, "Origin": "https://attacker.example"},
    )
    assert mismatched_origin.status_code == 403
    mismatched_port = client.post(
        "/api/tasks",
        json={"raw_input": "test", "supplemental_material": ""},
        headers={**headers, "Origin": "http://testserver:9000"},
    )
    assert mismatched_port.status_code == 403


def test_local_collection_results_correction_and_undo(tmp_path) -> None:
    app = create_app(
        AppSettings(
            data_dir=tmp_path,
            credentials=InMemoryCredentialStore(),
            fetcher=FakeFetcher(),
        )
    )
    client = TestClient(app)
    headers = {"Origin": "http://testserver", "X-CSRF-Token": app.state.csrf_token}
    created = client.post(
        "/api/tasks",
        json={
            "raw_input": "查价格：https://example.test/pricing",
            "supplemental_material": "",
        },
        headers=headers,
    )
    run = client.post(f"/api/tasks/{created.json()['id']}/runs", headers=headers).json()

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = client.get(f"/api/runs/{run['id']}").json()
        if state["status"] not in {"queued", "running"}:
            break
        time.sleep(0.02)

    results = client.get(f"/api/tasks/{created.json()['id']}/results?view=confirmed").json()
    assert state["status"] == "completed"
    assert results["items"][0]["observations"][0]["value"] == "每月 12 元"
    entity_id = results["items"][0]["entity_id"]

    corrected = client.patch(
        f"/api/entities/{entity_id}",
        json={"field": "价格", "value": "每月 15 元"},
        headers=headers,
    )
    assert corrected.status_code == 200
    corrected_results = client.get(f"/api/tasks/{created.json()['id']}/results").json()
    assert corrected_results["items"][0]["observations"][0]["value"] == "每月 15 元"
    assert corrected_results["items"][0]["observations"][0]["status"] == "user_confirmed"

    undone = client.delete(
        f"/api/entities/{entity_id}/corrections/%E4%BB%B7%E6%A0%BC",
        headers=headers,
    )
    assert undone.status_code == 200
    restored = client.get(f"/api/tasks/{created.json()['id']}/results").json()
    assert restored["items"][0]["observations"][0]["value"] == "每月 12 元"

    exported = client.post(
        f"/api/tasks/{created.json()['id']}/export",
        json={"format": "markdown", "view": "all"},
        headers=headers,
    )
    assert exported.status_code == 200
    assert "来源摘录" in exported.text
    assert "https://example.test/pricing" in exported.text

    retained = client.delete(
        f"/api/tasks/{created.json()['id']}/data?keep_derived_results=true",
        headers=headers,
    )
    assert retained.status_code == 200
    after_delete = client.get(f"/api/tasks/{created.json()['id']}/results").json()
    assert after_delete["items"][0]["observations"][0]["status"] == "evidence_missing"
    assert after_delete["items"][0]["observations"][0]["evidence"][0]["snapshot_available"] is False

    deleted = client.delete(
        f"/api/tasks/{created.json()['id']}/data?keep_derived_results=false",
        headers=headers,
    )
    assert deleted.status_code == 200
    assert client.get(f"/api/tasks/{created.json()['id']}").status_code == 404
