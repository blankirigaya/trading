"""WebSocket event stream for the monitoring UI.

Pushes high-level application events only. A slow client is never allowed to block
the trading workflow: each subscriber has a bounded queue and the hub drops the
oldest entries when a client falls behind.
"""

import asyncio
import contextlib

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ai_trader.api.events import new_event

router = APIRouter()

HEARTBEAT_SECONDS = 20.0


@router.websocket("/events")
async def events_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    hub = websocket.app.state.container.event_hub
    queue = await hub.subscribe()
    try:
        await websocket.send_json(
            new_event("SYSTEM", "connected", "Connected to AI trader event stream")
        )
        for event in hub.recent(50):
            await websocket.send_json(event)

        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
            except TimeoutError:
                await websocket.send_json(new_event("SYSTEM", "heartbeat", "stream alive"))
                continue
            await websocket.send_json(event)
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception:  # noqa: BLE001 - never let one client take down the server
        pass
    finally:
        hub.unsubscribe(queue)
        with contextlib.suppress(Exception):
            await websocket.close()
