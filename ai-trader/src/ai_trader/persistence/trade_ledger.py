"""Permanent paper-trade ledger behind a small async boundary.

All SQLite work runs in ``asyncio.to_thread`` so a slow disk never blocks the
event loop. Fills and closed trades are append-only history (they survive
/paper/reset); positions and cash are the restorable open book.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ai_trader.domain.orders import Fill
from ai_trader.domain.portfolio import Position
from ai_trader.persistence.retry import with_sqlite_retry
from ai_trader.persistence.trade_models import (
    ClosedTradeRow,
    PaperAccountRow,
    PaperPositionRow,
    TradeFillRow,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClosedTrade:
    """One completed round trip with its realized rupee P&L."""

    closed_trade_id: str
    shard: int
    symbol: str
    quantity: int
    entry_price: Decimal
    exit_price: Decimal
    realized_pnl: Decimal
    opened_at: datetime | None
    closed_at: datetime
    entry_decision_id: str | None
    exit_decision_id: str
    # Total charges for the round trip (entry + exit). Gross = realized + charges.
    charges: Decimal = Decimal("0")


def _to_closed_trade(row: ClosedTradeRow) -> ClosedTrade:
    return ClosedTrade(
        closed_trade_id=row.closed_trade_id,
        shard=row.shard,
        symbol=row.symbol,
        quantity=row.quantity,
        entry_price=row.entry_price,
        exit_price=row.exit_price,
        realized_pnl=row.realized_pnl,
        opened_at=row.opened_at,
        closed_at=row.closed_at,
        entry_decision_id=row.entry_decision_id,
        exit_decision_id=row.exit_decision_id,
        charges=row.charges or Decimal("0"),
    )


class TradeLedger:
    """Persists paper fills, the open book, and closed-trade P&L per shard."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    async def record_fill(
        self, shard: int, fill: Fill, decision_id: str, cash_after: Decimal
    ) -> None:
        await with_sqlite_retry(
            "record_fill", self._record_fill_sync, shard, fill, decision_id, cash_after
        )

    def _record_fill_sync(
        self, shard: int, fill: Fill, decision_id: str, cash_after: Decimal
    ) -> None:
        with Session(self.engine) as session:
            session.add(
                TradeFillRow(
                    fill_id=fill.fill_id,
                    shard=shard,
                    broker_order_id=fill.broker_order_id,
                    decision_id=decision_id,
                    symbol=fill.symbol,
                    side=fill.side.value,
                    quantity=fill.quantity,
                    price=fill.price,
                    fee=fill.fees,
                    entry_fees=fill.entry_fees,
                    brokerage=fill.charge_breakdown.get("brokerage", Decimal("0")),
                    stt=fill.charge_breakdown.get("stt", Decimal("0")),
                    exchange_charge=fill.charge_breakdown.get("exchange", Decimal("0")),
                    sebi_charge=fill.charge_breakdown.get("sebi", Decimal("0")),
                    stamp_duty=fill.charge_breakdown.get("stamp_duty", Decimal("0")),
                    gst=fill.charge_breakdown.get("gst", Decimal("0")),
                    cash_after=cash_after,
                    filled_at=fill.filled_at,
                )
            )
            session.commit()

    async def save_book(
        self,
        shard: int,
        cash: Decimal,
        positions: list[Position],
        now: datetime,
        entry_charges: dict[str, Decimal] | None = None,
    ) -> None:
        """Persist the open book (cash + positions) so a restart restores it."""
        await with_sqlite_retry(
            "save_book", self._save_book_sync, shard, cash, positions, now, entry_charges
        )

    def _save_book_sync(
        self,
        shard: int,
        cash: Decimal,
        positions: list[Position],
        now: datetime,
        entry_charges: dict[str, Decimal] | None = None,
    ) -> None:
        charges = entry_charges or {}
        with Session(self.engine) as session:
            account = session.get(PaperAccountRow, shard)
            if account is None:
                session.add(PaperAccountRow(shard=shard, cash=cash, updated_at=now))
            else:
                account.cash = cash
                account.updated_at = now
            live = {p.symbol for p in positions if p.quantity}
            for position in positions:
                if not position.quantity:
                    continue
                row = session.get(PaperPositionRow, {"shard": shard, "symbol": position.symbol})
                entry_decision = row.entry_decision_id if row is not None else None
                opened = row.opened_at if row is not None else None
                if row is None:
                    session.add(
                        PaperPositionRow(
                            shard=shard,
                            symbol=position.symbol,
                            quantity=position.quantity,
                            average_entry_price=position.average_entry_price,
                            realized_pnl=position.realized_pnl,
                            entry_charges=charges.get(position.symbol, Decimal("0")),
                            entry_decision_id=entry_decision,
                            opened_at=opened,
                        )
                    )
                else:
                    row.quantity = position.quantity
                    row.average_entry_price = position.average_entry_price
                    row.realized_pnl = position.realized_pnl
                    row.entry_charges = charges.get(position.symbol, Decimal("0"))
            for (row_shard, symbol) in [
                (r.shard, r.symbol)
                for r in session.scalars(
                    select(PaperPositionRow).where(PaperPositionRow.shard == shard)
                ).all()
            ]:
                if symbol not in live:
                    session.delete(session.get(PaperPositionRow, {"shard": row_shard, "symbol": symbol}))
            session.commit()

    async def record_close(self, trade: ClosedTrade) -> None:
        await with_sqlite_retry("record_close", self._record_close_sync, trade)

    def _record_close_sync(self, trade: ClosedTrade) -> None:
        with Session(self.engine) as session:
            session.add(
                ClosedTradeRow(
                    closed_trade_id=trade.closed_trade_id,
                    shard=trade.shard,
                    symbol=trade.symbol,
                    quantity=trade.quantity,
                    entry_price=trade.entry_price,
                    exit_price=trade.exit_price,
                    realized_pnl=trade.realized_pnl,
                    charges=trade.charges,
                    opened_at=trade.opened_at,
                    closed_at=trade.closed_at,
                    entry_decision_id=trade.entry_decision_id,
                    exit_decision_id=trade.exit_decision_id,
                )
            )
            session.commit()

    async def load_book(self, shard: int) -> tuple[Decimal | None, list[Position]]:
        """Restore (cash, positions) for a shard; (None, []) when never traded."""
        return await with_sqlite_retry("load_book", self._load_book_sync, shard)

    def _load_book_sync(self, shard: int) -> tuple[Decimal | None, list[Position]]:
        with Session(self.engine) as session:
            account = session.get(PaperAccountRow, shard)
            rows = (
                session.scalars(
                    select(PaperPositionRow).where(PaperPositionRow.shard == shard)
                ).all()
            )
            positions = [
                Position(
                    symbol=row.symbol,
                    quantity=row.quantity,
                    average_entry_price=row.average_entry_price,
                    realized_pnl=row.realized_pnl,
                )
                for row in rows
                if row.quantity
            ]
            return (account.cash if account is not None else None, positions)

    async def load_entry_charges(self, shard: int) -> dict[str, Decimal]:
        """Entry charges still carried by each open position of a shard."""
        return await with_sqlite_retry(
            "load_entry_charges", self._load_entry_charges_sync, shard
        )

    def _load_entry_charges_sync(self, shard: int) -> dict[str, Decimal]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(PaperPositionRow).where(PaperPositionRow.shard == shard)
            ).all()
            return {
                row.symbol: row.entry_charges or Decimal("0")
                for row in rows
                if row.quantity and (row.entry_charges or Decimal("0")) > 0
            }

    async def position_open_context(
        self, shard: int, symbol: str
    ) -> tuple[str | None, datetime | None]:
        """(entry decision, opened_at) of the currently open position, if any."""
        return await with_sqlite_retry(
            "position_open_context", self._position_open_context_sync, shard, symbol
        )

    def _position_open_context_sync(
        self, shard: int, symbol: str
    ) -> tuple[str | None, datetime | None]:
        with Session(self.engine) as session:
            row = session.get(PaperPositionRow, {"shard": shard, "symbol": symbol})
            if row is None:
                return (None, None)
            return (row.entry_decision_id, row.opened_at)

    async def note_position_opened(
        self, shard: int, symbol: str, decision_id: str, opened_at: datetime
    ) -> None:
        await with_sqlite_retry(
            "note_position_opened",
            self._note_position_opened_sync,
            shard,
            symbol,
            decision_id,
            opened_at,
        )

    def _note_position_opened_sync(
        self, shard: int, symbol: str, decision_id: str, opened_at: datetime
    ) -> None:
        with Session(self.engine) as session:
            row = session.get(PaperPositionRow, {"shard": shard, "symbol": symbol})
            if row is None:
                return
            if row.entry_decision_id is None:
                row.entry_decision_id = decision_id
            if row.opened_at is None:
                row.opened_at = opened_at
            session.commit()

    async def closed_trades(self, limit: int = 300) -> list[ClosedTrade]:
        rows = await with_sqlite_retry("closed_trades", self._closed_trades_sync, limit)
        return [_to_closed_trade(row) for row in rows]

    def _closed_trades_sync(self, limit: int) -> list[ClosedTradeRow]:
        with Session(self.engine) as session:
            return list(
                session.scalars(
                    select(ClosedTradeRow)
                    .order_by(ClosedTradeRow.closed_at.desc())
                    .limit(limit)
                ).all()
            )

    async def realized_pnl(self, shard: int) -> Decimal:
        """Sum of closed-trade P&L for a shard.

        The open book cannot answer this: a fully closed position is deleted
        from ``paper_positions``, so its realized P&L must come from the
        append-only closed-trade history. Restores use it to keep the reported
        realized P&L after a restart.
        """
        return await with_sqlite_retry("realized_pnl", self._realized_pnl_sync, shard)

    def _realized_pnl_sync(self, shard: int) -> Decimal:
        with Session(self.engine) as session:
            total = session.scalar(
                select(func.coalesce(func.sum(ClosedTradeRow.realized_pnl), 0)).where(
                    ClosedTradeRow.shard == shard
                )
            )
            return Decimal(str(total or 0))

    async def total_charges(self) -> Decimal:
        """Sum of charges across every closed trade (all shards)."""
        return await with_sqlite_retry("total_charges", self._total_charges_sync)

    def _total_charges_sync(self) -> Decimal:
        with Session(self.engine) as session:
            total = session.scalar(
                select(func.coalesce(func.sum(ClosedTradeRow.charges), 0))
            )
            return Decimal(str(total or 0))

    async def fill_count(self, shard: int) -> int:
        return await with_sqlite_retry("fill_count", self._fill_count_sync, shard)

    def _fill_count_sync(self, shard: int) -> int:
        with Session(self.engine) as session:
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(TradeFillRow)
                    .where(TradeFillRow.shard == shard)
                )
                or 0
            )

    async def clear_book(self, shard: int) -> None:
        """Forget the open book (paper reset). Fill/close history is permanent."""
        await with_sqlite_retry("clear_book", self._clear_book_sync, shard)

    def _clear_book_sync(self, shard: int) -> None:
        with Session(self.engine) as session:
            session.execute(
                delete(PaperPositionRow).where(PaperPositionRow.shard == shard)
            )
            session.execute(
                delete(PaperAccountRow).where(PaperAccountRow.shard == shard)
            )
            session.commit()
