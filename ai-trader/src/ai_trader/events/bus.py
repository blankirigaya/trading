from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import TypeVar

from ai_trader.domain.events import Event

E = TypeVar("E", bound=Event)
EventHandler = Callable[[E], Awaitable[None]]


class EventBus:
    """In-process event bus; replaceable at the application boundary later."""

    def __init__(self) -> None:
        self._handlers: dict[type[Event], list[EventHandler[Event]]] = defaultdict(list)
        self.recent_events: list[Event] = []

    def subscribe(self, event_type: type[E], handler: EventHandler[E]) -> None:
        self._handlers[event_type].append(handler)  # type: ignore[arg-type]

    async def publish(self, event: Event) -> None:
        self.recent_events.append(event)
        for handler in self._handlers[type(event)]:
            await handler(event)
