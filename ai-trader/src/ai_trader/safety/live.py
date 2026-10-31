"""Live-only risk checks layered on the shared mechanical safety envelope.

Paper trading continues to use `SafetyEnvelope` directly. Live trading must pass these
additional gates first, and this module never submits an order itself.
"""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from ai_trader.config import Settings
from ai_trader.domain.common import Clock, SystemClock
from ai_trader.domain.orders import OrderRequest
from ai_trader.domain.portfolio import PortfolioState
from ai_trader.safety.envelope import SafetyEnvelope, SafetyResult

IST = ZoneInfo("Asia/Kolkata")


class LiveSafetyEnvelope:
    """Extra live-trading gates for fully autonomous operation within limits."""

    def __init__(
        self, settings: Settings, base_safety: SafetyEnvelope, clock: Clock | None = None
    ) -> None:
        self.settings = settings
        self.base_safety = base_safety
        self.clock = clock or SystemClock()

    def check(
        self,
        request: OrderRequest,
        portfolio: PortfolioState,
        *,
        broker_name: str,
        now: datetime | None = None,
    ) -> SafetyResult:
        """Apply live-only gates before the shared mechanical safety checks."""
        if not (
            self.settings.live_trading_approved
            and self.settings.live_order_execution
            and self.settings.trading_enabled
        ):
            return SafetyResult(False, "Live trading is disabled")
        if broker_name != self.settings.execution_broker:
            return SafetyResult(False, "Order broker does not match EXECUTION_BROKER")
        if request.symbol not in set(self.settings.live_allowed_symbols):
            return SafetyResult(False, "Symbol is not in LIVE_ALLOWED_SYMBOLS")
        if request.product != "INTRADAY":
            return SafetyResult(False, "Initial live scope is intraday orders only")
        if not self._within_trading_window(now or self.clock.now()):
            return SafetyResult(
                False,
                "Outside LIVE_TRADING_START_IST/LIVE_TRADING_END_IST; no new live orders",
            )

        base_result = self.base_safety.check(request, portfolio)
        if not base_result.allowed:
            return base_result
        if request.actual_order_value > self.settings.live_max_order_value_inr:
            return SafetyResult(False, "Order would exceed LIVE_MAX_ORDER_VALUE_INR")

        current_value = sum(
            (position.average_entry_price * position.quantity for position in portfolio.positions),
            Decimal("0"),
        )
        if request.side.value == "BUY":
            projected_value = current_value + request.actual_order_value
        else:
            projected_value = max(Decimal("0"), current_value - request.actual_order_value)
        if projected_value > self.settings.live_max_position_value_inr:
            return SafetyResult(False, "Order would exceed LIVE_MAX_POSITION_VALUE_INR")
        if -portfolio.realized_pnl >= self.settings.live_max_daily_loss_inr:
            return SafetyResult(False, "Live daily loss limit reached")
        return SafetyResult(True)

    def record_submission(self) -> None:
        self.base_safety.record_submission()

    def _within_trading_window(self, now: datetime) -> bool:
        current = now.astimezone(IST).strftime("%H:%M")
        return self.settings.live_trading_start_ist <= current < self.settings.live_trading_end_ist
