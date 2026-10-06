"""FYERS V3 Data WebSocket client.

Handles authentication, subscription, flow-control acknowledgements, and automatic
reconnection with resubscription. Ingestion is decoupled from AI inference: decoded
frames are pushed into a bounded buffer and never block on a downstream reviewer.
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable
from uuid import uuid4

import websockets

from ai_trader.integrations.fyers.auth import CHANNEL, CLIENT_SOURCE, FyersAuth, extract_hsm_key
from ai_trader.integrations.fyers.protocol import (
    DataType,
    Mode,
    ProtocolError,
    ReqType,
    ScripFeed,
    build_ack_message,
    build_auth_message,
    build_channel_bitmap_message,
    build_subscribe_message,
    build_unsubscribe_message,
    parse_datafeed,
    parse_envelope,
)

logger = logging.getLogger(__name__)
FrameHandler = Callable[[str, bytes], None]

MAX_RECONNECT_DELAY_SECONDS = 30


class FyersDataSocket:
    """Maintains one FYERS data-socket session."""

    def __init__(
        self,
        auth: FyersAuth,
        on_frame: FrameHandler,
        feed_mode: str = "symbolUpdate",
        max_reconnect_delay: int = MAX_RECONNECT_DELAY_SECONDS,
    ) -> None:
        self.auth = auth
        self.on_frame = on_frame
        self.feed_mode = feed_mode
        self.max_reconnect_delay = max_reconnect_delay
        self.connection_id: str | None = None
        self.connected = False
        self.subscribe_topics: list[str] = []
        self.topic_to_symbol: dict[str, str] = {}
        self.reconnect_count = 0
        self.message_count = 0
        self._stop = asyncio.Event()

    @property
    def lite_mode(self) -> bool:
        return self.feed_mode.lower() == "lite"

    async def subscribe(self, topics: list[str], topic_to_symbol: dict[str, str]) -> None:
        self.subscribe_topics = list(topics)
        self.topic_to_symbol = dict(topic_to_symbol)

    async def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        """Connect, authenticate, subscribe, and stream until stopped.

        Reconnects with exponential backoff and resubscribes on every new session.
        """
        delay = 1
        while not self._stop.is_set():
            try:
                await self._session()
                delay = 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a socket session must not kill the loop
                if self._stop.is_set():
                    break
                self.connected = False
                self.reconnect_count += 1
                logger.warning("fyers socket session ended: %s", type(exc).__name__)
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.max_reconnect_delay)

    async def _session(self) -> None:
        hsm_key = extract_hsm_key(self.auth.authorization_header.split(":", 1)[1])
        mode = Mode.LITE if self.lite_mode else Mode.FULL
        marker = Mode.LITE_HEADER if self.lite_mode else Mode.FULL_HEADER
        self.connection_id = str(uuid4())

        async with websockets.connect(
            self.auth.data_ws_url,
            ping_interval=20,
            ping_timeout=20,
            additional_headers={"Authorization": self.auth.authorization_header},
        ) as socket:
            await socket.send(build_auth_message(hsm_key, mode, CLIENT_SOURCE))

            auth_response = _require_bytes(await asyncio.wait_for(socket.recv(), timeout=15))
            envelope = parse_envelope(auth_response)
            if not envelope.is_ok:
                raise ConnectionError("FYERS data socket rejected authentication")
            ack_count = envelope.ack_count() or 1

            await socket.send(build_channel_bitmap_message(ReqType.FULL_MODE, CHANNEL, marker))
            channel_response = _require_bytes(await asyncio.wait_for(socket.recv(), timeout=15))
            if not parse_envelope(channel_response).is_ok:
                raise ConnectionError("FYERS data socket rejected the channel request")

            if self.subscribe_topics:
                await socket.send(
                    build_subscribe_message(
                        self.subscribe_topics,
                        CHANNEL,
                        self.auth.authorization_header,
                        CLIENT_SOURCE,
                    )
                )

            self.connected = True
            logger.info("fyers data socket connected")

            since_ack = 0
            async for frame in socket:
                if self._stop.is_set():
                    break
                if not isinstance(frame, bytes):
                    continue
                self.message_count += 1
                self.on_frame(self.connection_id, frame)
                if frame[2] == ReqType.DATAFEED:
                    since_ack += 1
                    if since_ack >= ack_count:
                        since_ack = 0
                        with contextlib.suppress(Exception):
                            await socket.send(build_ack_message(_message_num(frame)))

    async def unsubscribe(self) -> bytes | None:
        """Build the unsubscribe frame for the current topics, if any.

        Returned rather than sent so shutdown ordering stays explicit and testable.
        """
        if not self.subscribe_topics:
            return None
        frame = build_unsubscribe_message(
            self.subscribe_topics, CHANNEL, self.auth.authorization_header, CLIENT_SOURCE
        )
        self.subscribe_topics = []
        return frame


def _message_num(frame: bytes) -> int:
    return int.from_bytes(frame[3:7], "big")


def _require_bytes(frame: str | bytes) -> bytes:
    """Control frames are binary; a text frame here means an unexpected error page."""
    if not isinstance(frame, bytes):
        raise ProtocolError("FYERS control frame was not binary")
    return frame


def decode_binary_frame(frame: bytes) -> tuple[int, tuple[ScripFeed, ...]]:
    """Parse a datafeed frame. Raises on non-datafeed frames."""
    return parse_datafeed(frame)


def is_market_data_frame(frame: bytes) -> bool:
    return len(frame) >= 3 and frame[2] == ReqType.DATAFEED


def is_lite_feed(feed: ScripFeed) -> bool:
    return feed.data_type == DataType.LITE
