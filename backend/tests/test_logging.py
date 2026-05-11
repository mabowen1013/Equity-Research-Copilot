import logging

from fastapi.testclient import TestClient

from app.main import app


def test_health_request_is_logged(caplog) -> None:
    caplog.set_level(logging.INFO, logger="app.http")
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert "HTTP request method=GET path=/health status_code=200" in caplog.text
