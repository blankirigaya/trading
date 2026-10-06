"""FYERS market data connectivity probe.

Authenticates, resolves symbols, opens the data socket, waits for a real decodable
frame, and normalizes it. Credentials are never printed.
"""

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal

from ai_trader.config import Settings
from ai_trader.domain.market import MarketState
from ai_trader.integrations.fyers.auth import MissingFyersCredential
from ai_trader.integrations.fyers.feed import FyersFeed, build_auth, build_feed
from ai_trader.integrations.fyers.instruments import InvalidFyersSymbol

CONNECTED_AND_DECODED = "CONNECTED_AND_DECODED"
CONNECTED_BUT_NO_DATA = "CONNECTED_BUT_NO_DATA"
PROBE_FAILED = "PROBE_FAILED"

DEFAULT_WAIT_SECONDS = 45


@dataclass
class FyersProbeResult:
    ok: bool
    outcome: str
    states: list[MarketState] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    feed: FyersFeed | None = None


def _format_price(value: Decimal | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def describe_state(state: MarketState) -> list[str]:
    tick = state.recent_ticks[-1]
    candle = state.current_1m_candle
    ohlcv = (
        "n/a"
        if candle is None
        else f"O={_format_price(candle.open)} H={_format_price(candle.high)} "
        f"L={_format_price(candle.low)} C={_format_price(candle.close)} V={candle.volume}"
    )
    return [
        f"  {state.instrument.symbol} ({state.instrument.exchange}:{state.instrument.symbol}-EQ)",
        f"    LTP: {_format_price(state.last_price)}",
        f"    OHLCV: {ohlcv}",
        f"    timestamp: {tick.exchange_timestamp.isoformat()}",
    ]


async def probe_fyers(
    settings: Settings,
    symbols: list[str] | None = None,
    wait_seconds: int = DEFAULT_WAIT_SECONDS,
) -> FyersProbeResult:
    wanted = symbols or list(settings.shadow_symbols)
    lines = [
        "provider: fyers",
        f"symbols: {', '.join(wanted)}",
        f"feed_mode: {settings.fyers_feed_mode}",
    ]

    try:
        build_auth(settings)
    except MissingFyersCredential as exc:
        return FyersProbeResult(False, PROBE_FAILED, [], [*lines, f"credential: {exc}"])

    try:
        feed = await build_feed(settings, wanted)
    except InvalidFyersSymbol as exc:
        return FyersProbeResult(False, PROBE_FAILED, [], [*lines, f"symbol: {exc}"])
    except LookupError as exc:
        return FyersProbeResult(False, PROBE_FAILED, [], [*lines, f"instrument_resolution: {exc}"])
    except Exception as exc:  # noqa: BLE001 - probe reports, it does not raise
        return FyersProbeResult(
            False,
            PROBE_FAILED,
            [],
            [*lines, f"instrument_resolution: failed ({type(exc).__name__})"],
        )

    lines.append(f"instrument_tokens: {len(feed.instruments)} resolved")
    lines.append("websocket: connecting")

    socket_task = asyncio.create_task(feed.socket.run())
    try:
        deadline = asyncio.get_running_loop().time() + wait_seconds
        while asyncio.get_running_loop().time() < deadline:
            if feed.states():
                break
            if socket_task.done():
                break
            await asyncio.sleep(0.25)
        lines.append(
            "connection: successful" if feed.socket.connected else "connection: not established"
        )
    finally:
        await feed.socket.stop()
        socket_task.cancel()
        try:
            await socket_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001 - shutdown is best effort
            pass

    states = feed.states()
    if not states:
        lines.append("normalization: FAIL (no decodable market data received)")
        lines.append(f"decoded_frames: {feed.service.decoded_count}")
        if feed.service.last_error:
            lines.append(f"last_error: {feed.service.last_error}")
        lines.append(f"outcome: {CONNECTED_BUT_NO_DATA}")
        return FyersProbeResult(False, CONNECTED_BUT_NO_DATA, [], lines, feed)

    lines.append(f"decoded_frames: {feed.service.decoded_count}")
    lines.append(f"snapshots: {feed.service.snapshot_count} updates: {feed.service.update_count}")
    lines.append("normalization: PASS")
    for state in states:
        lines.extend(describe_state(state))
    lines.append(f"outcome: {CONNECTED_AND_DECODED}")
    return FyersProbeResult(True, CONNECTED_AND_DECODED, states, lines, feed)
