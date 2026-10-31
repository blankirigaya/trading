"""Capital-cap control: status exposure, immediate apply, and persistence."""

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


def test_status_exposes_the_capital_cap_controls(tmp_path: Path) -> None:
    client = _client(tmp_path / "status.db")
    body = client.get("/system/status").json()
    assert body["capital_cap_disabled"] is False
    assert body["max_ai_capital_inr"] == "50000"
    assert body["max_single_position_inr"] == "25000"
    assert body["max_gross_exposure_inr"] == "50000"


def test_toggle_updates_status_immediately(tmp_path: Path) -> None:
    client = _client(tmp_path / "toggle.db")
    response = client.post("/control/capital-cap", params={"disabled": "true"})
    assert response.status_code == 200
    assert response.json()["capital_cap_disabled"] is True
    assert client.get("/system/status").json()["capital_cap_disabled"] is True

    restored = client.post("/control/capital-cap", params={"disabled": "false"})
    assert restored.json()["capital_cap_disabled"] is False
    assert client.get("/system/status").json()["capital_cap_disabled"] is False


def test_cap_state_survives_a_process_restart(tmp_path: Path) -> None:
    database = tmp_path / "persist.db"
    first = _client(database)
    first.post("/control/capital-cap", params={"disabled": "true"})

    # A fresh container (new process) must load the persisted choice.
    second = _client(database)
    assert second.get("/system/status").json()["capital_cap_disabled"] is True
