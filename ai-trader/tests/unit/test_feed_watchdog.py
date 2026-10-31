"""Feed idle watchdog: prod a silently stalled FYERS socket during NSE hours."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ai_trader.application.fyers_session import feed_needs_reconnect, within_nse_hours
from ai_trader.domain.common import FakeClock
from ai_trader.integrations.fyers.client import FyersDataSocket
from ai_trader.integrations.fyers.market_data import FyersMarketDataService

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
IST = ZoneInfo("Asia/Kolkata")


def _ist_monday(hour: int, minute: int = 0) -> datetime:
    # 2026-10-26 is a Monday; ZoneInfo keeps the wall clock exact.
    return datetime(2026, 10, 26, hour, minute, tzinfo=IST)


def test_nse_hours_gate() -> None:
    assert within_nse_hours(_ist_monday(12, 0))
    assert within_nse_hours(_ist_monday(9, 15))
    assert within_nse_hours(_ist_monday(15, 30))
    assert not within_nse_hours(_ist_monday(9, 14))
    assert not within_nse_hours(_ist_monday(15, 31))
    assert not within_nse_hours(_ist_monday(8, 0))
    # Weekend silence is normal closure, never a stall.
    assert not within_nse_hours(datetime(2026, 10, 25, 12, 0, tzinfo=IST))  # Sunday
    assert not within_nse_hours(datetime(2026, 10, 24, 12, 0, tzinfo=IST))  # Saturday


def test_no_frames_yet_never_triggers() -> None:
    now = _ist_monday(12, 0)
    assert not feed_needs_reconnect(
        None, now, None, idle_threshold_seconds=90, cooldown_seconds=300
    )


def test_fresh_feed_never_triggers() -> None:
    now = _ist_monday(12, 0)
    assert not feed_needs_reconnect(
        30.0, now, None, idle_threshold_seconds=90, cooldown_seconds=300
    )


def test_stalled_feed_in_hours_triggers() -> None:
    now = _ist_monday(12, 0)
    assert feed_needs_reconnect(
        200.0, now, None, idle_threshold_seconds=90, cooldown_seconds=300
    )


def test_stalled_feed_out_of_hours_does_not_trigger() -> None:
    assert not feed_needs_reconnect(
        3600.0,
        _ist_monday(8, 0),
        None,
        idle_threshold_seconds=90,
        cooldown_seconds=300,
    )
    assert not feed_needs_reconnect(
        3600.0,
        datetime(2026, 10, 25, 12, 0, tzinfo=IST),
        None,
        idle_threshold_seconds=90,
        cooldown_seconds=300,
    )


def test_cooldown_suppresses_repeat_prods() -> None:
    now = _ist_monday(12, 0)
    recent = datetime(2026, 10, 26, 11, 59, tzinfo=IST)
    assert not feed_needs_reconnect(
        200.0, now, recent, idle_threshold_seconds=90, cooldown_seconds=300
    )
    old = datetime(2026, 10, 26, 11, 50, tzinfo=IST)
    assert feed_needs_reconnect(
        200.0, now, old, idle_threshold_seconds=90, cooldown_seconds=300
    )


def test_ingest_marks_frame_time(clock: FakeClock) -> None:
    from ai_trader.domain.market import Instrument

    instrument = Instrument(
        instrument_key="3045", symbol="INFY", exchange="NSE", segment="EQ"
    )
    service = FyersMarketDataService({"sf|nse_cm|3045": instrument}, clock)
    assert service.idle_seconds(clock.now()) is None
    service.ingest_frame("c1", (FIXTURES / "fyers_snapshot.bin").read_bytes())
    assert service.last_frame_at == clock.now()
    idle = service.idle_seconds(clock.now())
    assert idle is not None and idle < 1


async def test_socket_reconnect_without_session_is_noop() -> None:
    from ai_trader.integrations.fyers.auth import FyersAuth

    socket = FyersDataSocket(FyersAuth("app", "token"), on_frame=lambda *_: None)
    assert await socket.reconnect() is False


async def test_socket_reconnect_drops_live_session() -> None:
    from ai_trader.integrations.fyers.auth import FyersAuth

    closed: list[bool] = []

    class FakeWS:
        async def close(self) -> None:
            closed.append(True)

    socket = FyersDataSocket(FyersAuth("app", "token"), on_frame=lambda *_: None)
    socket._ws = FakeWS()
    socket.connected = True
    assert await socket.reconnect() is True
    assert closed == [True]
    assert socket.connected is False
    assert socket._ws is None
    # Second prod finds nothing to drop.
    assert await socket.reconnect() is False
