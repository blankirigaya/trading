from datetime import UTC, datetime
from decimal import Decimal

from ai_trader.application.container import Container
from ai_trader.config import Settings
from ai_trader.domain.common import FakeClock
from ai_trader.domain.events import MarketTickReceived
from ai_trader.domain.market import MarketTick


async def test_full_mock_ai_to_paper_flow() -> None:
    clock = FakeClock(datetime(2026, 9, 1, 4, 0, tzinfo=UTC))
    app = Container(Settings(trading_enabled=True), clock)
    app.broker.set_price("TESTSTOCK", Decimal("100"))
    tick = MarketTick(
        symbol="TESTSTOCK",
        last_price=Decimal("100"),
        exchange_timestamp=clock.now(),
        received_timestamp=clock.now(),
        processed_timestamp=clock.now(),
        raw_payload={"raw": "preserved"},
    )
    await app.bus.publish(
        MarketTickReceived(
            event_id="event",
            correlation_id="correlation",
            occurred_at=clock.now(),
            raw_payload={"raw": "preserved"},
            tick=tick,
        )
    )
    records = await app.journal.list_recent()
    assert records[0].status.value == "EXECUTED"
    assert records[0].jev_critique is not None
    assert records[0].orders_generated[0].request.client_order_id.startswith(records[0].decision_id)
    assert (await app.broker.get_account()).cash == Decimal("90000")
