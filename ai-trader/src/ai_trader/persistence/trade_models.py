"""Permanent paper-trading ledger: every fill, every closed trade, open books.

The in-memory PaperBroker loses everything on restart. These tables make the
trade history permanent: fills are append-only evidence, closed trades carry
their realized rupee P&L, and the per-shard open book (positions + cash)
is restored on the next boot.
"""

from datetime import datetime
from decimal import Decimal
from typing import cast

from sqlalchemy import Integer, Numeric, String, Table, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Mapped, mapped_column

from ai_trader.persistence.models import Base, UTCDateTime


class TradeFillRow(Base):
    __tablename__ = "paper_fills"
    fill_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    shard: Mapped[int] = mapped_column(Integer, index=True)
    broker_order_id: Mapped[str] = mapped_column(String(64), index=True)
    decision_id: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    side: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[int] = mapped_column(Integer)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    fee: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    # Entry-side charges released by this fill (0 for buys).
    entry_fees: Mapped[Decimal] = mapped_column(Numeric(18, 4), server_default="0")
    brokerage: Mapped[Decimal] = mapped_column(Numeric(18, 4), server_default="0")
    stt: Mapped[Decimal] = mapped_column(Numeric(18, 4), server_default="0")
    exchange_charge: Mapped[Decimal] = mapped_column(Numeric(18, 4), server_default="0")
    sebi_charge: Mapped[Decimal] = mapped_column(Numeric(18, 4), server_default="0")
    stamp_duty: Mapped[Decimal] = mapped_column(Numeric(18, 4), server_default="0")
    gst: Mapped[Decimal] = mapped_column(Numeric(18, 4), server_default="0")
    cash_after: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    filled_at: Mapped[datetime] = mapped_column(UTCDateTime(timezone=True), index=True)


class PaperPositionRow(Base):
    __tablename__ = "paper_positions"
    shard: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(40), primary_key=True)
    quantity: Mapped[int] = mapped_column(Integer)
    average_entry_price: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    # Charges already paid to open the quantity still held; netted from realized
    # P&L when the shares are sold. Restored on restart so a reopened book keeps
    # counting the entry leg.
    entry_charges: Mapped[Decimal] = mapped_column(Numeric(18, 4), server_default="0")
    entry_decision_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(UTCDateTime(timezone=True), nullable=True)


class PaperAccountRow(Base):
    __tablename__ = "paper_accounts"
    shard: Mapped[int] = mapped_column(Integer, primary_key=True)
    cash: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(timezone=True))


class ClosedTradeRow(Base):
    __tablename__ = "paper_closed_trades"
    closed_trade_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    shard: Mapped[int] = mapped_column(Integer, index=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    exit_price: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    # Total charges for the round trip (entry + exit legs). Gross P&L is
    # realized_pnl + charges. Zero on trades recorded before charges existed.
    charges: Mapped[Decimal] = mapped_column(Numeric(18, 4), server_default="0")
    opened_at: Mapped[datetime | None] = mapped_column(UTCDateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime] = mapped_column(UTCDateTime(timezone=True), index=True)
    entry_decision_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    exit_decision_id: Mapped[str] = mapped_column(String(64))


def ensure_trade_tables(engine: Engine) -> None:
    """Create the ledger tables if they are absent.

    Same rationale as ensure_news_tables: a fresh SQLite database (new local
    runs, tests) must be able to trade without a separate migration step.
    Existing databases get the newer charge columns added in place; old rows
    default to 0, so historical P&L stays exactly as recorded.
    """
    Base.metadata.create_all(
        engine,
        tables=cast(
            "list[Table]",
            [
                TradeFillRow.__table__,
                PaperPositionRow.__table__,
                PaperAccountRow.__table__,
                ClosedTradeRow.__table__,
            ],
        ),
    )
    _add_missing_columns(engine)


# (table, column) pairs added after the first release; kept as plain ALTERs
# because this project has no migration runner and SQLite supports adding
# columns cheaply. Fresh databases already have them from create_all.
_ADDED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("paper_fills", "entry_fees"),
    ("paper_fills", "brokerage"),
    ("paper_fills", "stt"),
    ("paper_fills", "exchange_charge"),
    ("paper_fills", "sebi_charge"),
    ("paper_fills", "stamp_duty"),
    ("paper_fills", "gst"),
    ("paper_positions", "entry_charges"),
    ("paper_closed_trades", "charges"),
)


def _add_missing_columns(engine: Engine) -> None:
    inspector = inspect(engine)
    existing = {
        table: {column["name"] for column in inspector.get_columns(table)}
        for table in {table for table, _ in _ADDED_COLUMNS}
    }
    with engine.begin() as connection:
        for table, column in _ADDED_COLUMNS:
            if column not in existing.get(table, set()):
                connection.execute(
                    text(
                        f"ALTER TABLE {table} "
                        f"ADD COLUMN {column} NUMERIC(18, 4) NOT NULL DEFAULT 0"
                    )
                )
