from decimal import Decimal

from ai_trader.ai.portfolio_manager import position_limits_text
from ai_trader.domain.orders import OrderRequest, OrderSide, OrderType
from ai_trader.domain.portfolio import PortfolioState
from ai_trader.safety.envelope import KillSwitch, SafetyEnvelope


def request() -> OrderRequest:
    return OrderRequest(
        client_order_id="c",
        decision_id="d",
        correlation_id="x",
        symbol="T",
        exchange="NSE",
        segment="EQ",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=10,
        requested_allocation=Decimal("1000"),
        actual_order_value=Decimal("1000"),
        conversion_reason="test",
    )


def test_safety_denies_when_trading_disabled(enabled_settings, clock) -> None:
    disabled = enabled_settings.model_copy(update={"trading_enabled": False})
    assert (
        SafetyEnvelope(disabled, KillSwitch(), clock)
        .check(request(), PortfolioState(cash=Decimal("10000")))
        .reason
        == "Trading disabled"
    )


def test_safety_passes_mechanical_order(enabled_settings, clock) -> None:
    assert (
        SafetyEnvelope(enabled_settings, KillSwitch(), clock)
        .check(request(), PortfolioState(cash=Decimal("10000")))
        .allowed
    )


def _large_buy() -> OrderRequest:
    return request().model_copy(
        update={
            "quantity": 120,
            "requested_allocation": Decimal("120000"),
            "actual_order_value": Decimal("120000"),
        }
    )


def test_capital_caps_deny_a_large_buy_by_default(enabled_settings, clock) -> None:
    result = SafetyEnvelope(enabled_settings, KillSwitch(), clock).check(
        _large_buy(), PortfolioState(cash=Decimal("500000"))
    )
    assert result.reason == "Order would exceed MAX_AI_CAPITAL_INR"


def test_disabled_capital_cap_allows_a_large_buy(enabled_settings, clock) -> None:
    result = SafetyEnvelope(
        enabled_settings, KillSwitch(), clock, capital_cap_disabled=True
    ).check(_large_buy(), PortfolioState(cash=Decimal("500000")))
    assert result.allowed


def test_capital_caps_never_block_an_exit(enabled_settings, clock) -> None:
    sell = _large_buy().model_copy(update={"side": OrderSide.SELL})
    result = SafetyEnvelope(enabled_settings, KillSwitch(), clock).check(
        sell, PortfolioState(cash=Decimal("0"))
    )
    assert result.allowed


def test_prompt_limits_follow_the_capital_cap_switch(enabled_settings) -> None:
    limited = position_limits_text(enabled_settings)
    assert "50000" in limited
    assert "denied" in limited

    lifted = position_limits_text(enabled_settings, capital_cap_disabled=True)
    assert "no fixed AI capital" in lifted
    assert "available cash" in lifted


def test_position_count_cap_denies_a_new_symbol_by_default(
    enabled_settings, clock
) -> None:
    from ai_trader.domain.portfolio import Position

    held = [
        Position(
            symbol=f"S{i}",
            quantity=1,
            average_entry_price=Decimal("10"),
            realized_pnl=Decimal("0"),
        )
        for i in range(enabled_settings.max_open_positions)
    ]
    portfolio = PortfolioState(cash=Decimal("500000"), positions=held)
    result = SafetyEnvelope(enabled_settings, KillSwitch(), clock).check(
        request(), portfolio
    )
    assert result.reason == "Maximum open positions reached"


def test_disabled_position_count_cap_allows_a_new_symbol(
    enabled_settings, clock
) -> None:
    from ai_trader.domain.portfolio import Position

    held = [
        Position(
            symbol=f"S{i}",
            quantity=1,
            average_entry_price=Decimal("10"),
            realized_pnl=Decimal("0"),
        )
        for i in range(enabled_settings.max_open_positions)
    ]
    portfolio = PortfolioState(cash=Decimal("500000"), positions=held)
    result = SafetyEnvelope(
        enabled_settings, KillSwitch(), clock, position_count_cap_disabled=True
    ).check(request(), portfolio)
    assert result.allowed


def test_prompt_limits_follow_the_position_count_switch(enabled_settings) -> None:
    lifted = position_limits_text(enabled_settings, position_count_cap_disabled=True)
    assert "no fixed limit on the number of open positions" in lifted

    both = position_limits_text(
        enabled_settings,
        capital_cap_disabled=True,
        position_count_cap_disabled=True,
    )
    assert "no fixed AI capital" in both
    assert "no fixed limit on the number of open positions" in both
