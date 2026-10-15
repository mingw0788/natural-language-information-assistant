from fastapi.testclient import TestClient

from info_assistant.main import create_app


def test_healthz_returns_ready() -> None:
    response = TestClient(create_app()).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_home_page_is_local_app() -> None:
    response = TestClient(create_app()).get("/")

    assert response.status_code == 200
    assert "自然语言信息采集助手" in response.text
