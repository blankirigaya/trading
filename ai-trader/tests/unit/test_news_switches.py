"""Unit coverage for the Tier-1 / analysis / Sonnet-escalation kill switches."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import create_engine

from ai_trader.application.fyers_session import ReviewShard, ShadowSession
from ai_trader.config import Settings
from ai_trader.domain.common import FakeClock
from ai_trader.journal.service import InMemoryDecisionJournal
from ai_trader.news.realtime import RealtimeNewsService
from ai_trader.news.sources import TIER1_SOURCE_IDS, enabled_sources
from ai_trader.persistence.control_models import ensure_control_tables
from ai_trader.persistence.control_store import ControlStore


def _control_store(tmp_path) -> ControlStore:  # noqa: ANN001 - pytest tmp dir
    from pathlib import Path

    engine = create_engine(f"sqlite:///{Path(tmp_path) / 'control.db'}")
    ensure_control_tables(engine)
    return ControlStore(engine)


def test_control_store_round_trips_the_news_switches(tmp_path) -> None:  # noqa: ANN001 - pytest tmp dir
    store = _control_store(tmp_path)
    assert asyncio.run(store.get_tier1_news_enabled(True)) is True
    assert asyncio.run(store.get_news_analysis_enabled(True)) is True
    assert asyncio.run(store.get_sonnet_escalation_enabled(True)) is True

    assert asyncio.run(store.set_tier1_news_enabled(False)) is False
    assert asyncio.run(store.set_news_analysis_enabled(False)) is False
    assert asyncio.run(store.set_sonnet_escalation_enabled(False)) is False

    assert asyncio.run(store.get_tier1_news_enabled(True)) is False
    assert asyncio.run(store.get_news_analysis_enabled(True)) is False
    assert asyncio.run(store.get_sonnet_escalation_enabled(True)) is False


def test_tier1_filter_drops_only_tier1() -> None:
    all_ids = {source.source_id for source in enabled_sources()}
    assert TIER1_SOURCE_IDS <= all_ids

    filtered = {source.source_id for source in enabled_sources(include_tier1=False)}
    assert filtered == all_ids - TIER1_SOURCE_IDS
    # Official filings and secondary dailies survive the filter.
    assert any("nse" in source_id for source_id in filtered)


class _StubFeed:
    """Feed double that idles until cancelled."""

    def __init__(self, source_id: str) -> None:
        self.source_id = source_id

    async def run(self, on_items) -> None:  # noqa: ANN001, ANN202 - test double
        await asyncio.sleep(3600)


def _realtime_service() -> RealtimeNewsService:
    from ai_trader.domain.news import NewsCategory, NewsSource, SourceType, TrustLevel

    settings = Settings(database_url="sqlite://", news_enabled=False)
    sources = [
        NewsSource(
            source_id="tier1-a",
            name="Tier 1 A",
            homepage="https://example.com/a.xml",
            source_type=SourceType.NEWS_MEDIA,
            trust_level=TrustLevel.SECONDARY,
            default_category=NewsCategory.MARKET_NEWS,
        ),
        NewsSource(
            source_id="plain-b",
            name="Plain B",
            homepage="https://example.com/b.xml",
            source_type=SourceType.NEWS_MEDIA,
            trust_level=TrustLevel.SECONDARY,
            default_category=NewsCategory.MARKET_NEWS,
        ),
    ]
    return RealtimeNewsService(settings=settings, intelligence=object(), sources=sources)  # type: ignore[arg-type]


def test_realtime_toggle_stops_and_restarts_one_feed() -> None:
    service = _realtime_service()
    service.feeds = {sid: _StubFeed(sid) for sid in ("tier1-a", "plain-b")}

    async def scenario() -> None:
        await service.start()
        assert set(service._tasks) == {"tier1-a", "plain-b"}
        assert await service.set_source_enabled("tier1-a", False) is True
        assert set(service._tasks) == {"plain-b"}
        assert await service.set_source_enabled("tier1-a", False) is False
        assert await service.set_source_enabled("tier1-a", True) is True
        assert set(service._tasks) == {"tier1-a", "plain-b"}
        assert await service.set_source_enabled("missing", False) is False
        await service.stop()

    asyncio.run(scenario())


def _record() -> SimpleNamespace:
    return SimpleNamespace(
        decision_id="d1",
        status=SimpleNamespace(value="HELD"),
        final_ai_decision=None,
        failure_reason=None,
    )


class _Workflow:
    """Workflow double recording which model path ran it."""

    def __init__(self, name: str, seen: list[str]) -> None:
        self._name = name
        self._seen = seen
        self.manager = SimpleNamespace(set_position_limits=lambda limits: None)
        self.safety = SimpleNamespace()

    async def run(self, state, bundle, board=None, prices=None):  # noqa: ANN001, ANN202 - test double
        self._seen.append(self._name)
        return _record()


def _session_with_shard() -> tuple[ShadowSession, list[str]]:
    from ai_trader.brokers.paper import PaperBroker
    from ai_trader.domain.market import Candle, Instrument, MarketState, MarketTick

    now = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
    clock = FakeClock(now)
    settings = Settings(
        database_url="sqlite://",
        news_enabled=False,
        ai_max_sonnet_escalations_per_day=2,
    )

    tick = MarketTick(
        symbol="T",
        exchange="NSE",
        segment="EQ",
        last_price=Decimal("100"),
        exchange_timestamp=now,
        received_timestamp=now,
        processed_timestamp=now,
        raw_payload={},
    )
    candle = Candle(
        start_timestamp=now,
        end_timestamp=now,
        open=Decimal("100"),
        high=Decimal("100"),
        low=Decimal("100"),
        close=Decimal("100"),
        volume=1000,
    )
    state = MarketState(
        snapshot_id="s-T",
        instrument=Instrument(instrument_key="NSE:T-EQ", symbol="T", exchange="NSE"),
        timestamp=now,
        last_price=Decimal("100"),
        previous_close=Decimal("100"),
        recent_ticks=[tick],
        current_1m_candle=candle,
        recent_1m_candles=[candle],
    )

    class _FeedStub:
        def states(self) -> list[MarketState]:
            return [state]

    session = ShadowSession(
        settings=settings,
        feed=_FeedStub(),  # type: ignore[arg-type]
        journal=InMemoryDecisionJournal(),
        clock=clock,
        interval_seconds=0,
    )
    seen: list[str] = []
    broker = PaperBroker(Decimal("100000"), clock)
    session.shards.append(
        ReviewShard(
            index=0,
            symbols=("T",),
            broker=broker,
            workflow=_Workflow("cadence", seen),  # type: ignore[arg-type]
            provider=object(),  # type: ignore[arg-type]
            sonnet_workflow=_Workflow("sonnet", seen),  # type: ignore[arg-type]
        )
    )
    return session, seen


def _board_of(session: ShadowSession) -> list:
    return session.feed.states()


def test_news_review_escalates_to_sonnet_within_cap() -> None:
    session, seen = _session_with_shard()

    async def scenario() -> None:
        board = _board_of(session)
        await session._review_shard(session.shards[0], board, trigger="NEWS", use_sonnet=True)
        await session._review_shard(session.shards[0], board, trigger="NEWS", use_sonnet=True)
        # Cap reached: falls back to the cadence workflow.
        await session._review_shard(session.shards[0], board, trigger="NEWS", use_sonnet=True)

    asyncio.run(scenario())
    assert seen == ["sonnet", "sonnet", "cadence"]
    assert session.sonnet_escalations_today == 2


def test_escalation_off_stays_on_cadence() -> None:
    session, seen = _session_with_shard()
    session.sonnet_escalation_enabled = False

    async def scenario() -> None:
        board = _board_of(session)
        await session._review_shard(session.shards[0], board, trigger="NEWS", use_sonnet=True)

    asyncio.run(scenario())
    assert seen == ["cadence"]
    assert session.sonnet_escalations_today == 0


def test_escalation_counter_resets_on_ist_day_change() -> None:
    session, seen = _session_with_shard()

    async def scenario() -> None:
        board = _board_of(session)
        await session._review_shard(session.shards[0], board, trigger="NEWS", use_sonnet=True)
        await session._review_shard(session.shards[0], board, trigger="NEWS", use_sonnet=True)
        # Next IST day: budget renews.
        session.clock.advance_seconds(24 * 3600)
        await session._review_shard(session.shards[0], board, trigger="NEWS", use_sonnet=True)

    asyncio.run(scenario())
    assert seen == ["sonnet", "sonnet", "sonnet"]
    assert session.sonnet_escalations_today == 1


def test_analysis_gate_skips_ai_when_disabled() -> None:
    from sqlalchemy import create_engine as _create_engine

    from ai_trader.news.service import NewsIntelligence
    from ai_trader.persistence.models import Base
    from ai_trader.persistence.news_repository import NewsRepository

    engine = _create_engine("sqlite://")
    Base.metadata.create_all(engine)
    repository = NewsRepository(engine)

    called: list[str] = []

    class _Provider:
        async def structured_completion(self, *args, **kwargs):  # noqa: ANN002, ANN003 - test double
            called.append("ai")
            raise AssertionError("AI must not be called while disabled")

    settings = Settings(database_url="sqlite://", news_enabled=False)
    intelligence = NewsIntelligence(settings, repository, ai_provider=_Provider())  # type: ignore[arg-type]
    intelligence.analysis_enabled = False
    assert asyncio.run(intelligence.enqueue_for_analysis([])) == 0

    from ai_trader.domain.news import NewsItem

    item = NewsItem(
        item_id="x",
        source_id="s",
        source_name="S",
        source_type="NEWS_MEDIA",
        trust_level="SECONDARY",
        headline="Test headline",
        canonical_url="https://example.com/x",
        url="https://example.com/x",
        published_at=datetime.now(UTC),
        received_at=datetime.now(UTC),
        category="MARKET_NEWS",
        symbols=(),
        content_hash="a" * 64,
        headline_hash="b" * 64,
    )
    analysis = asyncio.run(intelligence.analyse_item(item))
    assert called == []
    assert "disabled" in (analysis.failure_reason or "").lower()
