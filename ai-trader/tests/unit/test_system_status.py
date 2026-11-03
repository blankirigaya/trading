import json

from fastapi.testclient import TestClient

from ai_trader.application.container import Container
from ai_trader.config import Settings
from ai_trader.main import create_app


def _client_with_live_shadow() -> TestClient:
    settings = Settings(
        app_mode="LIVE_SHADOW",
        ai_provider="remote_openai_compatible",
        ai_base_url="http://trader:hunter2@192.168.1.25:8080/v1",
        ai_model="server-model-id",
        ai_api_key="top-secret-key",
        jev_enabled=False,
    )
    app = create_app()
    app.state.container = Container(settings)
    return TestClient(app)


def test_status_reports_remote_inference_and_paper_execution() -> None:
    body = _client_with_live_shadow().get("/system/status").json()
    assert body["ai_provider"] == "remote_openai_compatible"
    assert body["ai_inference_location"] == "remote"
    assert body["ai_model"] == "server-model-id"
    assert body["execution_broker"] == "paper"
    assert body["live_order_execution"] is False
    assert body["mode"] == "LIVE_SHADOW"


def test_status_exposes_health_fields() -> None:
    body = _client_with_live_shadow().get("/system/status").json()
    for field in (
        "ai_server_reachable",
        "last_ai_success_at",
        "last_ai_failure_at",
    ):
        assert field in body


def test_status_sanitizes_ai_server_and_never_leaks_secrets() -> None:
    body = _client_with_live_shadow().get("/system/status").json()
    assert body["ai_server"] == "192.168.1.25:8080"
    serialized = json.dumps(body)
    for secret in ("hunter2", "top-secret-key", "api_key"):
        assert secret not in serialized


def test_status_does_not_perform_inference_request() -> None:
    client = _client_with_live_shadow()
    for _ in range(5):
        client.get("/system/status")
    # Cached health stays unknown because no inference or probe has run.
    assert client.get("/system/status").json()["ai_server_reachable"] is None


def test_status_reflects_recorded_health_without_new_requests() -> None:
    from ai_trader.ai.remote_model import DEGRADED_AFTER_CONSECUTIVE_FAILURES

    client = _client_with_live_shadow()
    health = client.app.state.container.ai_health
    # A single failure is dampened: the badge stays UNKNOWN, the error is recorded.
    health.record_failure("inference request returned HTTP 503")
    body = client.get("/system/status").json()
    assert body["ai_server_reachable"] is None
    assert "503" in body["last_ai_error"]
    assert body["last_ai_failure_at"] is not None
    # A sustained run with no success in between reports DEGRADED.
    for _ in range(DEGRADED_AFTER_CONSECUTIVE_FAILURES - 1):
        health.record_failure("inference request returned HTTP 503")
    assert client.get("/system/status").json()["ai_server_reachable"] is False


def test_health_endpoint_is_cheap() -> None:
    assert _client_with_live_shadow().get("/health").json() == {"status": "ok"}
