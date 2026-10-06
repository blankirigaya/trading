from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from ai_trader.config import Settings
from ai_trader.domain.common import Clock
from ai_trader.domain.orders import OrderRequest
from ai_trader.domain.portfolio import PortfolioState


@dataclass(frozen=True)
class SafetyResult:
    allowed: bool
    reason: str | None = None


class KillSwitch:
    def __init__(self) -> None:
        self.enabled = False


class SafetyEnvelope:
    def __init__(self, settings: Settings, kill_switch: KillSwitch, clock: Clock) -> None:
        self.settings = settings
        self.kill_switch = kill_switch
        self.clock = clock
        self._recent_submissions: list[datetime] = []

    def check(self, order: OrderRequest, portfolio: PortfolioState) -> SafetyResult:
        self._recent_submissions = [
            timestamp
            for timestamp in self._recent_submissions
            if timestamp >= self.clock.now() - timedelta(minutes=1)
        ]
        if not self.settings.trading_enabled:
            return SafetyResult(False, "Trading disabled")
        if self.kill_switch.enabled:
            return SafetyResult(False, "Emergency kill switch enabled")
        if order.exchange not in self.settings.allowed_exchanges:
            return SafetyResult(False, "Symbol exchange is not allowed")
        if order.segment not in self.settings.allowed_segments:
            return SafetyResult(False, "Symbol segment is not allowed")
        if order.requested_allocation > self.settings.max_ai_capital_inr:
            return SafetyResult(False, "Order would exceed MAX_AI_CAPITAL_INR")
        if order.actual_order_value > self.settings.max_single_position_inr:
            return SafetyResult(False, "Order would exceed MAX_SINGLE_POSITION_INR")
        gross = sum(
            (position.average_entry_price * position.quantity for position in portfolio.positions),
            Decimal("0"),
        )
        if gross + order.actual_order_value > self.settings.max_gross_exposure_inr:
            return SafetyResult(False, "Order would exceed MAX_GROSS_EXPOSURE_INR")
        if -portfolio.realized_pnl >= self.settings.max_daily_loss_inr:
            return SafetyResult(False, "Daily loss limit reached")
        if len(self._recent_submissions) >= self.settings.max_orders_per_minute:
            return SafetyResult(False, "Maximum orders per minute reached")
        if len(portfolio.positions) >= self.settings.max_open_positions and order.symbol not in {
            p.symbol for p in portfolio.positions
        }:
            return SafetyResult(False, "Maximum open positions reached")
        return SafetyResult(True)

    def record_submission(self) -> None:
        self._recent_submissions.append(self.clock.now())
