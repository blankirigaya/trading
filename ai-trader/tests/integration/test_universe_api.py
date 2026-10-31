"""Universe mode control: validation, status exposure, and persistence."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_trader.api.routes import router
from ai_trader.application.container import Container
from ai_trader.application.universe import MASTER_UNIVERSE
from ai_trader.config import Settings


def _client(database_path: Path) -> TestClient:
    app = FastAPI()
    app.state.container = Container(Settings(database_url=f"sqlite:///{database_path}"))
    app.include_router(router)
    return TestClient(app)


def test_status_exposes_the_universe_controls(tmp_path: Path) -> None:
    client = _client(tmp_path / "status.db")
    body = client.get("/system/status").json()
    assert body["universe_mode"] == "cheap"
    assert body["universe_size"] == 100
    assert body["universe_master_size"] == len(MASTER_UNIVERSE)
    assert isinstance(body["universe_selected"], list)


def test_switch_updates_status_immediately(tmp_path: Path) -> None:
    client = _client(tmp_path / "switch.db")
    response = client.post("/control/universe", params={"mode": "expensive"})
    assert response.status_code == 200
    assert response.json() == {"universe_mode": "expensive", "applies": "next_review"}
    assert client.get("/system/status").json()["universe_mode"] == "expensive"


def test_mode_survives_a_process_restart(tmp_path: Path) -> None:
    database = tmp_path / "persist.db"
    first = _client(database)
    first.post("/control/universe", params={"mode": "mixed"})

    # A fresh container (new process) must load the persisted choice.
    second = _client(database)
    assert second.get("/system/status").json()["universe_mode"] == "mixed"


def test_invalid_mode_is_rejected(tmp_path: Path) -> None:
    client = _client(tmp_path / "invalid.db")
    response = client.post("/control/universe", params={"mode": "momentum"})
    assert response.status_code == 400
    assert client.get("/system/status").json()["universe_mode"] == "cheap"


@pytest.mark.parametrize("mode", ["cheap", "mixed", "expensive"])
def test_each_supported_mode_is_accepted(tmp_path: Path, mode: str) -> None:
    client = _client(tmp_path / f"{mode}.db")
    assert client.post("/control/universe", params={"mode": mode}).status_code == 200
