from fastapi.testclient import TestClient

from info_assistant.main import AppSettings, create_app
from info_assistant.services.credentials import InMemoryCredentialStore


def make_test_app(tmp_path):
    return create_app(AppSettings(data_dir=tmp_path, credentials=InMemoryCredentialStore()))


def test_healthz_returns_ready(tmp_path) -> None:
    response = TestClient(make_test_app(tmp_path)).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_home_page_is_local_app(tmp_path) -> None:
    response = TestClient(make_test_app(tmp_path)).get("/")

    assert response.status_code == 200
    assert "自然语言信息采集助手" in response.text
    assert 'id="task-form"' in response.text
    assert "免API网页搜索" in response.text
    assert "无需密钥" in response.text
    assert "用自然语言描述你要找什么" in response.text
    assert 'href="/settings"' in response.text
    assert "csrf-token" in response.text


def test_settings_and_history_pages_render_for_narrow_viewports(tmp_path) -> None:
    client = TestClient(make_test_app(tmp_path))

    settings = client.get("/settings")
    history = client.get("/history")

    assert settings.status_code == history.status_code == 200
    assert "Windows 凭据管理器" in settings.text
    assert 'id="search-provider"' in settings.text
    assert 'id="model-preset"' in settings.text
    assert "GLM-4.7-Flash" in settings.text
    assert 'id="history-list"' in history.text


def test_local_browser_channel_is_passed_to_browser_reader(tmp_path) -> None:
    app = create_app(
        AppSettings(
            data_dir=tmp_path,
            credentials=InMemoryCredentialStore(),
            browser_channel="msedge",
        )
    )

    assert app.state.browser_reader.browser_channel == "msedge"
