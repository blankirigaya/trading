"""Per-tick market snapshots, honest staleness, and the feed -> UI sink wiring.

Regression cover for two production symptoms: the UI strip only refreshed once
per AI review (and froze completely while reviews were paused), and the feed
watchdog measured liveness from frames that decoded to nothing.
"""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from ai_trader.application.container import Container
from ai_trader.config import Settings
from ai_trader.domain.common import FakeClock
from ai_trader.domain.market import Instrument, MarketState
from ai_trader.integrations.fyers.auth import FyersAuth
from ai_trader.integrations.fyers.market_data import FyersMarketDataService

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
TOPIC = "sf|nse_cm|3045"
INSTRUMENT = Instrument(instrument_key="3045", symbol="INFY", exchange="NSE", segment="EQ")


def _state(symbol: str, price: str, when: datetime) -> MarketState:
    return MarketState(
        snapshot_id=f"snap-{symbol}-{price}",
        instrument=Instrument(
            instrument_key=f"key-{symbol}", symbol=symbol, exchange="NSE", segment="EQ"
        ),
        timestamp=when,
        last_price=Decimal(price),
        recent_ticks=[],
    )


def test_snapshots_refresh_on_every_tick_not_on_each_review() -> None:
    clock = FakeClock(datetime(2026, 10, 28, 4, 0, tzinfo=UTC))
    container = Container(Settings(app_mode="PAPER"), clock=clock)

    container.record_market_state([_state("INFY", "100", clock.now())])
    clock.advance_seconds(1)
    container.record_market_state([_state("INFY", "101", clock.now())])

    snapshots = container.market_snapshots()
    assert len(snapshots) == 1
    assert Decimal(str(snapshots[0]["last_price"])) == Decimal("101")
    assert container.market_snapshot_age_seconds() == 0


def test_snapshot_age_keeps_rising_when_ticks_stop() -> None:
    clock = FakeClock(datetime(2026, 10, 28, 4, 0, tzinfo=UTC))
    container = Container(Settings(app_mode="PAPER"), clock=clock)
    assert container.market_snapshot_age_seconds() is None

    container.record_market_state([_state("INFY", "100", clock.now())])
    clock.advance_seconds(745)
    # The API still answers, but freshness must tell the truth.
    assert container.market_snapshot_age_seconds() == 745


def test_market_timeline_events_are_throttled_per_symbol() -> None:
    clock = FakeClock(datetime(2026, 10, 28, 4, 0, tzinfo=UTC))
    container = Container(Settings(app_mode="PAPER"), clock=clock)

    for price in ("100", "101", "102"):
        container.record_market_state([_state("INFY", price, clock.now())])
        clock.advance_seconds(1)
    updates = [e for e in container.event_hub.recent() if e["kind"] == "market_update"]
    assert len(updates) == 1

    clock.advance_seconds(61)
    container.record_market_state([_state("INFY", "103", clock.now())])
    updates = [e for e in container.event_hub.recent() if e["kind"] == "market_update"]
    assert len(updates) == 2


def test_idle_ignores_frames_that_produced_no_tick(clock: FakeClock) -> None:
    service = FyersMarketDataService({TOPIC: INSTRUMENT}, clock)
    service.ingest_frame("c1", (FIXTURES / "fyers_snapshot.bin").read_bytes())
    clock.advance_seconds(300)

    # An update for a topic no snapshot introduced: the socket is busy, but no
    # price was refreshed. That is a stall, not a healthy feed.
    other = bytearray((FIXTURES / "fyers_update.bin").read_bytes())
    other[10:12] = (9999).to_bytes(2, "little")
    assert service.ingest_frame("c1", bytes(other)) == []

    assert service.last_frame_at == clock.now()
    idle = service.idle_seconds(clock.now())
    assert idle is not None and idle >= 300
    assert service.newest_state_age_seconds(clock.now()) >= 300


async def test_build_feed_forwards_each_decoded_batch_to_the_sink(monkeypatch) -> None:
    from ai_trader.integrations.fyers import feed as feed_module

    async def fake_hsm_symbols(self, symbols: list[str]) -> dict[str, str]:  # type: ignore[no-untyped-def]
        return {symbol: "3045" for symbol in symbols}

    monkeypatch.setattr(FyersAuth, "hsm_symbols", fake_hsm_symbols)
    seen: list[MarketState] = []

    feed = await feed_module.build_feed(
        Settings(shadow_symbols=("NSE:INFY-EQ",)), market_sink=seen.extend
    )
    feed.socket.on_frame("c1", (FIXTURES / "fyers_snapshot.bin").read_bytes())

    assert [state.instrument.symbol for state in seen] == ["INFY"]
    assert feed.service.latest(TOPIC) is not None
