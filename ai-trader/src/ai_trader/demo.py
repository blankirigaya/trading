import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from ai_trader.application.container import Container
from ai_trader.config import Settings
from ai_trader.domain.common import FakeClock
from ai_trader.domain.events import MarketTickReceived
from ai_trader.domain.market import MarketTick


async def run_demo() -> None:
    clock = FakeClock(datetime(2026, 9, 1, 4, 0, tzinfo=UTC))
    settings = Settings(trading_enabled=True)
    app = Container(settings=settings, clock=clock)
    app.broker.set_price("TESTSTOCK", Decimal("100"))
    tick = MarketTick(
        symbol="TESTSTOCK",
        last_price=Decimal("100"),
        exchange_timestamp=clock.now(),
        received_timestamp=clock.now(),
        processed_timestamp=clock.now(),
        raw_payload={"source": "demo"},
    )
    await app.bus.publish(
        MarketTickReceived(
            event_id="demo-event-1",
            correlation_id="demo-correlation-1",
            occurred_at=clock.now(),
            raw_payload={"source": "demo"},
            tick=tick,
        )
    )
    record = (await app.journal.list_recent())[0]
    portfolio = await app.broker.get_account()
    print("Fake market event received")
    print("Scout requests review")
    print("Mock Portfolio AI: BUY TESTSTOCK allocate INR 10,000")
    print("Mock Jev: agrees")
    print(f"Safety: {'PASS' if record.status.value == 'EXECUTED' else record.status.value}")
    print(f"PaperBroker: order accepted ({record.orders_generated[0].broker_order_id})")
    print("Decision journal: saved")
    print(f"Portfolio: cash={portfolio.cash} positions={len(portfolio.positions)}")


if __name__ == "__main__":
    asyncio.run(run_demo())
