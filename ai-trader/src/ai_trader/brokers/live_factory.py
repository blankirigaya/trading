"""Execution-broker selection with live trading disabled by default.

Paper remains the only broker used by the production session, container, workflow, and
routes. This factory is the single place allowed to construct a live broker, and it
refuses unless every explicit live approval is present plus broker credentials.
"""

from collections.abc import Callable

import httpx

from ai_trader.brokers.base import Broker
from ai_trader.brokers.charges import charge_rates_for
from ai_trader.brokers.live import LiveTradingDisabled, require_live_trading
from ai_trader.brokers.live_fyers import LiveFyersBroker
from ai_trader.brokers.live_upstox import LiveUpstoxBroker
from ai_trader.brokers.paper import PaperBroker
from ai_trader.config import Settings
from ai_trader.domain.common import Clock, SystemClock
from ai_trader.integrations.fyers.auth import FyersAuth
from ai_trader.integrations.upstox.auth import UpstoxAuth


def build_execution_broker(
    settings: Settings,
    clock: Clock | None = None,
    *,
    fyers_client: httpx.AsyncClient | None = None,
    upstox_client: httpx.AsyncClient | None = None,
    upstox_instruments: Callable[[str, str], str] | None = None,
) -> Broker:
    """Return PaperBroker unless live trading is fully and explicitly approved."""
    active_clock = clock or SystemClock()
    if settings.execution_broker == "paper":
        return PaperBroker(
            settings.paper_initial_capital,
            active_clock,
            margin_multiple=settings.paper_margin_multiple,
            charge_rates=charge_rates_for(settings),
        )
    if settings.execution_broker == "fyers":
        require_live_trading(settings, "fyers")
        fyers_auth = FyersAuth(
            settings.fyers_client_id,
            settings.fyers_access_token,
            settings.fyers_api_base_url,
            settings.fyers_data_ws_url,
        )
        return LiveFyersBroker(
            fyers_auth,
            clock=active_clock,
            client=fyers_client,
            enabled=True,
            timeout_seconds=settings.live_broker_timeout_seconds,
        )
    if settings.execution_broker == "upstox":
        require_live_trading(settings, "upstox")
        if upstox_instruments is None:
            raise LiveTradingDisabled(
                "Live Upstox trading requires configured instrument-token resolution"
            )
        upstox_auth = UpstoxAuth(settings.upstox_api_base_url, settings.upstox_access_token or "")
        return LiveUpstoxBroker(
            upstox_auth,
            upstox_instruments,
            clock=active_clock,
            client=upstox_client,
            order_base_url=settings.upstox_order_base_url,
            enabled=True,
            timeout_seconds=settings.live_broker_timeout_seconds,
        )
    raise LiveTradingDisabled(f"Unsupported EXECUTION_BROKER: {settings.execution_broker!r}")


__all__ = ["build_execution_broker"]
