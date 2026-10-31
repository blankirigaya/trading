"""Small persisted runtime controls (operator switches that survive restarts).

Unlike decisions and paper trades these are configuration, not history: each
key holds exactly one value that the operator can change from the UI.
"""

from datetime import UTC, datetime
from typing import cast

from sqlalchemy import String, Table
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Mapped, mapped_column

from ai_trader.persistence.models import Base, UTCDateTime


class RuntimeSettingRow(Base):
    __tablename__ = "runtime_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


def ensure_control_tables(engine: Engine) -> None:
    """Create the control table when absent (fresh SQLite DB, tests)."""
    Base.metadata.create_all(
        engine,
        tables=cast("list[Table]", [RuntimeSettingRow.__table__]),
    )
