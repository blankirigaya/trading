"""Disabled live-execution primitives.

Nothing in this module can trade unless a live broker is explicitly constructed with
``enabled=True``. Production construction stays in one factory, and that factory
requires paper-off/live-approved configuration plus broker credentials. Every public
live-broker method refuses to touch the network while disabled.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from ai_trader.brokers.base import Broker
from ai_trader.config import Settings
from ai_trader.domain.common import Clock, SystemClock
from ai_trader.domain.orders import LiveOrderUpdate, Order, OrderRequest
from ai_trader.domain.portfolio import PortfolioState
from ai_trader.safety.envelope import SafetyEnvelope, SafetyResult

IST = ZoneInfo("Asia/Kolkata")


class LiveTradingDisabled(RuntimeError):
    """Live execution was requested without all explicit live approvals."""


class LiveBrokerError(RuntimeError):
    """A live-broker operation failed without changing paper state."""


class LiveBrokerTimeout(LiveBrokerError):
    """The broker did not answer in time. The order state is unknown, not failed."""


class LiveBrokerResponseError(LiveBrokerError):
    """The broker returned an error. Only safe status and code metadata are retained."""

    def __init__(self, *, status_code: int | None, code: str | None, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class LiveOrderUnknown(LiveBrokerError):
    """No authoritative broker record was found. Do not retry blindly."""


class LiveBrokerUnsupported(LiveBrokerError):
    """The operation is outside the initial disabled live-trading scope."""


class LiveBroker(Broker, ABC):
    """Common contract for real-money-capable brokers.

    PaperBroker remains the only broker used by the live paper-trading path. These
    implementations are never constructed there.
    """

    is_execution_provider = True
    broker_name = "live"

    @abstractmethod
    async def update_order(self, update: LiveOrderUpdate) -> Order:
        """Change an order with explicit replacement terms."""

    @abstractmethod
    async def reconcile_order(self, broker_order_id: str) -> Order:
        """Return the broker's authoritative state for a known broker order."""

    @abstractmethod
    async def reconcile_client_order(self, client_order_id: str) -> Order | None:
        """Find an order created under our idempotency key, if the broker has it."""


def live_trading_enabled(settings: Settings) -> bool:
    """All three live approvals must be present. Defaults keep trading disabled."""
    return bool(
        settings.live_trading_approved
        and settings.live_order_execution
        and settings.trading_enabled
    )


def require_live_trading(settings: Settings, broker_name: str) -> None:
    """Fail closed unless live trading is explicitly approved for this broker."""
    if settings.execution_broker != broker_name:
        raise LiveTradingDisabled(
            f"EXECUTION_BROKER is {settings.execution_broker!r}, not {broker_name!r}"
        )
    if not live_trading_enabled(settings):
        raise LiveTradingDisabled(
            "Live trading requires LIVE_TRADING_APPROVED, LIVE_ORDER_EXECUTION, "
            "and TRADING_ENABLED all to be true"
        )


class LiveSafetyEnvelope:
    """Live-specific risk checks layered on the shared mechanical safety envelope."""

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
        if not live_trading_enabled(self.settings):
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
