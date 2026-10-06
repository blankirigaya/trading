"""In-process event hub backing the UI WebSocket.

High-level application events only. The hub is a bounded ring buffer plus one bounded
queue per subscriber, so a slow or vanished client can never block ingestion or the
trading workflow.
"""

import asyncio
from collections import deque
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, Literal

MAX_BUFFERED_EVENTS = 500
MAX_QUEUE_SIZE = 200

EventCategory = Literal["MARKET", "AI", "JEV", "SAFETY", "PAPER", "SYSTEM", "ERROR", "JOURNAL"]


def new_event(
    category: EventCategory | str,
    kind: str,
    message: str,
    *,
    decision_id: str | None = None,
    correlation_id: str | None = None,
    symbol: str | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "category": str(category),
        "kind": kind,
        "message": message,
        "decision_id": decision_id,
        "correlation_id": correlation_id,
        "symbol": symbol,
        "data": data or {},
    }


class EventHub:
    """Fan-out of timeline events to WebSocket subscribers."""

    def __init__(self) -> None:
        self._buffer: deque[dict[str, Any]] = deque(maxlen=MAX_BUFFERED_EVENTS)
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._sequence = 0

    def publish(self, event: dict[str, Any]) -> dict[str, Any]:
        self._sequence += 1
        stamped = {**event, "seq": self._sequence}
        self._buffer.append(stamped)
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(stamped)
            except asyncio.QueueFull:
                # Drop the oldest entry for this subscriber rather than blocking.
                try:
                    queue.get_nowait()
                    queue.put_nowait(stamped)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    self._subscribers.discard(queue)
        return stamped

    def recent(self, limit: int = 200) -> list[dict[str, Any]]:
        return list(self._buffer)[-limit:]

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


def summarise(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for event in events:
        key = str(event.get("category", "SYSTEM"))
        counts[key] = counts.get(key, 0) + 1
    return {
        "total": len(list(events)) if not isinstance(events, list) else len(events),
        "by_category": counts,
    }
