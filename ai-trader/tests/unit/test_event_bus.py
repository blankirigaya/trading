from decimal import Decimal

from ai_trader.domain.events import MarketTickReceived
from ai_trader.domain.market import MarketTick
from ai_trader.events.bus import EventBus


async def test_event_bus_delivers_typed_event(clock) -> None:
    bus = EventBus()
    received: list[str] = []

    async def handler(event: MarketTickReceived) -> None:
        received.append(event.event_id)

    bus.subscribe(MarketTickReceived, handler)
    tick = MarketTick(
        symbol="T",
        last_price=Decimal("1"),
        exchange_timestamp=clock.now(),
        received_timestamp=clock.now(),
        processed_timestamp=clock.now(),
    )
    await bus.publish(
        MarketTickReceived(
            event_id="event-1", correlation_id="corr-1", occurred_at=clock.now(), tick=tick
        )
    )
    assert received == ["event-1"]
    assert len(bus.recent_events) == 1
