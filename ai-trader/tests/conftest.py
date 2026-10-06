from datetime import UTC, datetime
from decimal import Decimal

import pytest

from ai_trader.config import Settings
from ai_trader.domain.common import FakeClock


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 9, 1, 4, 0, tzinfo=UTC))


@pytest.fixture
def enabled_settings() -> Settings:
    return Settings(trading_enabled=True, max_ai_capital_inr=Decimal("50000"))
