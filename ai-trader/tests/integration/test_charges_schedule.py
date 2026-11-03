"""Read-only tests for the charge schedule endpoint the holdings tables use."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_trader.api.routes import router
from ai_trader.application.container import Container
from ai_trader.config import Settings


def _client(**overrides) -> TestClient:
    app = FastAPI()
    app.state.container = Container(Settings(**overrides))
    app.include_router(router)
    return TestClient(app)


def test_schedule_reports_active_rates_from_settings() -> None:
    body = _client().get("/charges/schedule").json()
    assert body["enabled"] is True
    rates = body["rates"]
    assert rates["brokerage_pct"] == str(Settings().paper_brokerage_pct)
    assert rates["stt_sell_pct"] == str(Settings().paper_stt_sell_pct)
    assert rates["gst_pct"] == str(Settings().paper_gst_pct)
    assert rates["brokerage_cap_inr"] == str(Settings().paper_brokerage_cap_inr)


def test_schedule_reports_disabled_without_rates() -> None:
    body = _client(paper_charges_enabled=False).get("/charges/schedule").json()
    assert body == {"enabled": False, "rates": None}
