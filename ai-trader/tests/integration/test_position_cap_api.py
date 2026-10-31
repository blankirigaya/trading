"""Position-count cap control: status exposure, immediate apply, and persistence."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_trader.api.routes import router
from ai_trader.application.container import Container
from ai_trader.config import Settings


def _client(database_path: Path) -> TestClient:
    app = FastAPI()
    app.state.container = Container(Settings(database_url=f"sqlite:///{database_path}"))
    app.include_router(router)
    return TestClient(app)


def test_status_exposes_the_position_count_cap(tmp_path: Path) -> None:
    client = _client(tmp_path / "status.db")
    body = client.get("/system/status").json()
    assert body["position_count_cap_disabled"] is False
    assert body["max_open_positions"] == 5


def test_toggle_updates_status_immediately(tmp_path: Path) -> None:
    client = _client(tmp_path / "toggle.db")
    response = client.post("/control/position-cap", params={"disabled": "true"})
    assert response.status_code == 200
    assert response.json()["position_count_cap_disabled"] is True
    assert client.get("/system/status").json()["position_count_cap_disabled"] is True

    restored = client.post("/control/position-cap", params={"disabled": "false"})
    assert restored.json()["position_count_cap_disabled"] is False
    assert client.get("/system/status").json()["position_count_cap_disabled"] is False


def test_cap_state_survives_a_process_restart(tmp_path: Path) -> None:
    database = tmp_path / "persist.db"
    first = _client(database)
    first.post("/control/position-cap", params={"disabled": "true"})

    # A fresh container (new process) must load the persisted choice.
    second = _client(database)
    assert second.get("/system/status").json()["position_count_cap_disabled"] is True
