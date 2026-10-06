import asyncio
from collections.abc import Awaitable, Callable
from uuid import uuid4

import websockets

from ai_trader.integrations.upstox.auth import UpstoxAuth

BinaryHandler = Callable[[str, bytes], Awaitable[None]]


class UpstoxV3WebSocketClient:
    """V3-only feed client with reconnect and resubscribe; it cannot submit orders."""

    def __init__(self, auth: UpstoxAuth, on_binary: BinaryHandler) -> None:
        self.auth = auth
        self.on_binary = on_binary
        self.connection_id: str | None = None
        self.connected = False
        self._instrument_keys: set[str] = set()
        self._stop = asyncio.Event()

    async def subscribe(self, instrument_key: str) -> None:
        self._instrument_keys.add(instrument_key)

    async def run(self) -> None:
        delay = 1
        while not self._stop.is_set():
            try:
                url = await self.auth.authorized_websocket_url()
                self.connection_id = str(uuid4())
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as socket:
                    self.connected = True
                    await self._resubscribe(socket)
                    delay = 1
                    async for frame in socket:
                        if isinstance(frame, bytes):
                            await self.on_binary(self.connection_id, frame)
            except (OSError, websockets.WebSocketException):
                self.connected = False
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
            finally:
                self.connected = False

    async def stop(self) -> None:
        self._stop.set()

    async def _resubscribe(self, socket: websockets.ClientConnection) -> None:
        if not self._instrument_keys:
            return
        await socket.send(
            '{"guid":"shadow","method":"sub","data":{"mode":"full","instrumentKeys":['
            + ",".join(f'"{key}"' for key in sorted(self._instrument_keys))
            + "]}}"
        )
