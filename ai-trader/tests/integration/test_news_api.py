"""Tests for the news monitoring API surface used by the Electron UI.

The API is read-only. These tests also pin the separation between publisher facts and
AI interpretation, which the UI relies on.
"""

from datetime import UTC, datetime

import anyio
import pytest
from fastapi.testclient import TestClient

from ai_trader.main import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_news_feed_returns_a_list(client: TestClient) -> None:
    assert isinstance(client.get("/news").json(), list)


def test_news_sources_include_official_and_secondary(client: TestClient) -> None:
    body = client.get("/news/sources").json()
    assert isinstance(body, list)
    kinds = {source["source_type"] for source in body}
    assert "OFFICIAL" in kinds
    assert "NEWS_MEDIA" in kinds
    # Trust level must be present so the UI never has to guess provenance.
    for source in body:
        assert source["trust_level"]
        assert "homepage" in source


def test_news_detail_returns_404_for_unknown_item(client: TestClient) -> None:
    assert client.get("/news/does-not-exist").status_code == 404


def test_symbol_lookup_is_case_insensitive_and_empty_when_unmatched(client: TestClient) -> None:
    assert client.get("/news/symbol/infy").json() == []
    assert client.get("/news/symbol/NOSUCH").json() == []


def test_status_reports_news_enabled(client: TestClient) -> None:
    assert "news_enabled" in client.get("/system/status").json()


def test_news_endpoints_expose_no_order_control(client: TestClient) -> None:
    """News must never surface an action the user could execute."""
    for path in ("/news", "/news/sources", "/news/recent"):
        text = client.get(path).text.lower()
        assert "place_order" not in text
        assert "action_required" not in text


def test_news_module_cannot_reach_a_broker() -> None:
    """Structural guarantee: the news package has no broker dependency."""
    from pathlib import Path

    package = Path(__file__).resolve().parents[2] / "src" / "ai_trader" / "news"
    for module in package.glob("*.py"):
        text = module.read_text(encoding="utf8")
        assert "PaperBroker" not in text, f"{module.name} references PaperBroker"
        assert "brokers." not in text, f"{module.name} imports a broker"
        assert "place_order" not in text, f"{module.name} references place_order"


def test_news_review_handler_only_schedules_a_review(client: TestClient) -> None:
    """The container's news review hook emits an event; it places nothing."""
    from ai_trader.application.container import Container
    from ai_trader.domain.news import (
        AnalysisStatus,
        Materiality,
        NewsAnalysis,
        NewsCategory,
        NewsItem,
        SourceType,
        TrustLevel,
    )

    container = Container()
    now = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    item = NewsItem(
        item_id="i1",
        source_id="nse-corporate",
        source_name="NSE",
        source_type=SourceType.OFFICIAL,
        trust_level=TrustLevel.PRIMARY,
        headline="Order win",
        canonical_url="https://www.nseindia.com/x",
        url="https://www.nseindia.com/x",
        published_at=now,
        received_at=now,
        category=NewsCategory.CORPORATE_ACTION,
        symbols=("INFY",),
        content_hash="c" * 64,
        headline_hash="d" * 64,
    )
    analysis = NewsAnalysis(
        analysis_id="a1",
        cluster_id="cl1",
        item_id="i1",
        status=AnalysisStatus.COMPLETED,
        materiality=Materiality.HIGH,
        requires_portfolio_review=True,
    )

    async def run() -> None:
        context = container.news_intelligence().review_context(item, analysis)
        await container._on_news_review(item, analysis, context)

    anyio.run(run)

    kinds = [event["kind"] for event in container.event_hub.recent()]
    assert "news_portfolio_review_scheduled" in kinds
    # Nothing resembling an order was published.
    assert not any("order" in kind and "news" not in kind for kind in kinds)
