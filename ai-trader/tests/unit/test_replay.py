from datetime import UTC, datetime
from decimal import Decimal

from ai_trader.application.container import Container
from ai_trader.config import Settings
from ai_trader.domain.common import FakeClock
from ai_trader.replay.service import ReplayService


async def test_replay_is_deterministic() -> None:
    async def run_once() -> tuple[list[str], str, str]:
        clock = FakeClock(datetime(2026, 9, 1, 4, 0, tzinfo=UTC))
        container = Container(Settings(trading_enabled=True), clock)
        container.broker.set_price("TESTSTOCK", Decimal("100"))
        replay = ReplayService(container.bus, container.orchestrator)
        from pathlib import Path

        await replay.replay_file(Path("data/replay/demo_events.jsonl"))
        records = await container.journal.list_recent()
        orders = await container.broker.get_orders()
        return (
            [event.event_id for event in container.bus.recent_events],
            records[0].decision_id,
            orders[0].broker_order_id,
        )

    assert await run_once() == await run_once()
