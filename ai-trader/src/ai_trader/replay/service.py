import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from ai_trader.application.orchestrator import ApplicationOrchestrator
from ai_trader.domain.events import MarketTickReceived
from ai_trader.domain.market import MarketTick
from ai_trader.events.bus import EventBus


class ReplayService:
    def __init__(self, bus: EventBus, orchestrator: ApplicationOrchestrator) -> None:
        self.bus = bus
        self.orchestrator = orchestrator

    async def replay_file(self, path: Path) -> None:
        contents = await asyncio.to_thread(path.read_text)
        for line in contents.splitlines():
            raw = json.loads(line)
            timestamp = datetime.fromisoformat(raw["exchange_timestamp"]).astimezone(UTC)
            tick = MarketTick(
                symbol=raw["symbol"],
                last_price=raw["last_price"],
                exchange_timestamp=timestamp,
                received_timestamp=timestamp,
                processed_timestamp=timestamp,
                raw_payload=raw,
            )
            await self.bus.publish(
                MarketTickReceived(
                    event_id=raw["event_id"],
                    correlation_id=raw["correlation_id"],
                    occurred_at=timestamp,
                    raw_payload=raw,
                    tick=tick,
                )
            )
