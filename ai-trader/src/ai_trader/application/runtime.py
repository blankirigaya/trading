"""Runtime handle exposed to the monitoring API.

The API can start, stop, and trigger a review through this object, but it never
constructs one. The session runner attaches an instance to the container, so
no market or AI orchestration lives in request handlers.
"""

import asyncio
import contextlib
from typing import Any, Protocol


class ReviewResult(Protocol):
    record_id: str
    status: str
    failure_reason: str | None


class ShadowRuntime(Protocol):
    """Minimal control surface the API depends on."""

    running: bool

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def review_once(self, *, entry_only: bool = False) -> ReviewResult | None: ...


class NullShadowRuntime:
    """Used when no session is attached. Control endpoints stay well-behaved."""

    def __init__(self) -> None:
        self.running = False

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def review_once(self) -> ReviewResult | None:
        return None


class ManagedShadowRuntime:
    """Wraps a session so the API can drive start/stop without owning the loop."""

    def __init__(self, session: Any) -> None:
        self.session = session
        self.running = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        await self.session.start_ingestion()
        self._task = asyncio.create_task(self.session.run_loop())

    async def stop(self) -> None:
        self.running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None

    async def review_once(self, *, entry_only: bool = False) -> Any:
        if not self.session.socket_task:
            await self.session.start_ingestion()
        return await self.session.review_once(entry_only=entry_only)
