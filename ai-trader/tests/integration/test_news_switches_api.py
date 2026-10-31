"""News kill switches: status exposure, immediate apply, and persistence."""

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


def test_status_exposes_the_news_switches(tmp_path: Path) -> None:
    client = _client(tmp_path / "status.db")
    body = client.get("/system/status").json()
    assert body["tier1_news_enabled"] is True
    assert body["news_analysis_enabled"] is True
    assert body["sonnet_escalation_enabled"] is True
    assert body["sonnet_escalations_today"] == 0
    assert body["ai_max_sonnet_escalations_per_day"] == 20


def test_tier1_toggle_updates_status_immediately(tmp_path: Path) -> None:
    client = _client(tmp_path / "tier1.db")
    response = client.post("/control/tier1-news", params={"enabled": "false"})
    assert response.status_code == 200
    assert response.json()["tier1_news_enabled"] is False
    assert client.get("/system/status").json()["tier1_news_enabled"] is False

    restored = client.post("/control/tier1-news", params={"enabled": "true"})
    assert restored.json()["tier1_news_enabled"] is True
    assert client.get("/system/status").json()["tier1_news_enabled"] is True


def test_analysis_toggle_updates_status_immediately(tmp_path: Path) -> None:
    client = _client(tmp_path / "analysis.db")
    response = client.post("/control/news-analysis", params={"enabled": "false"})
    assert response.status_code == 200
    assert response.json()["news_analysis_enabled"] is False
    assert client.get("/system/status").json()["news_analysis_enabled"] is False


def test_escalation_toggle_updates_status_immediately(tmp_path: Path) -> None:
    client = _client(tmp_path / "escalation.db")
    response = client.post("/control/sonnet-escalation", params={"enabled": "false"})
    assert response.status_code == 200
    assert response.json()["sonnet_escalation_enabled"] is False
    assert client.get("/system/status").json()["sonnet_escalation_enabled"] is False


def test_switches_survive_a_process_restart(tmp_path: Path) -> None:
    database = tmp_path / "persist.db"
    first = _client(database)
    first.post("/control/tier1-news", params={"enabled": "false"})
    first.post("/control/news-analysis", params={"enabled": "false"})
    first.post("/control/sonnet-escalation", params={"enabled": "false"})

    # A fresh container (new process) must load the persisted choices.
    second = _client(database)
    body = second.get("/system/status").json()
    assert body["tier1_news_enabled"] is False
    assert body["news_analysis_enabled"] is False
    assert body["sonnet_escalation_enabled"] is False
