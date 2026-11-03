"""Exit-first context: the manager sees pre-digested open positions."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from ai_trader.ai.base import AIProvider
from ai_trader.ai.portfolio_manager import (
    OpenPositionBrief,
    PortfolioManagerAgent,
    render_open_position_brief,
)
from ai_trader.domain.market import MarketTick
from ai_trader.domain.portfolio import PortfolioState, Position


def _brief(**overrides: Any) -> OpenPositionBrief:
    base: dict[str, Any] = {
        "symbol": "GAIL",
        "quantity": 111,
        "average_entry_price": Decimal("175.67"),
        "last_price": Decimal("176.87"),
        "unrealized_inr": Decimal("133.20"),
        "unrealized_pct": Decimal("0.68"),
        "held_minutes": 42,
        "target_price": Decimal("177.50"),
        "invalidation_price": Decimal("174.00"),
        "entry_thesis": "Breakout above 175 resistance.",
    }
    base.update(overrides)
    return OpenPositionBrief(**base)


def test_empty_book_renders_nothing() -> None:
    assert render_open_position_brief([]) == ""


def test_line_shows_unrealized_age_and_entry_plan() -> None:
    text = render_open_position_brief([_brief()])
    assert "Open positions" in text
    assert "GAIL" in text
    assert "111" in text
    assert "+133.20 INR" in text
    assert "+0.68%" in text
    assert "held 42m" in text
    assert "target 177.50" in text
    assert "invalidation 174.00" in text
    assert "Breakout above 175" in text


def test_missing_mark_is_explicit_never_zero() -> None:
    text = render_open_position_brief(
        [_brief(last_price=None, unrealized_inr=None, unrealized_pct=None)]
    )
    assert "no live mark" in text
    # Must not show a fabricated zero P&L.
    assert "+0.00" not in text


def test_missing_plan_and_age_degrade_gracefully() -> None:
    text = render_open_position_brief(
        [
            _brief(
                held_minutes=None,
                target_price=None,
                invalidation_price=None,
                entry_thesis=None,
            )
        ]
    )
    assert "held age unknown" in text
    assert "no recorded entry plan" in text


def test_long_thesis_is_capped() -> None:
    text = render_open_position_brief([_brief(entry_thesis="x" * 500)])
    assert len(text) < 600
    assert text.rstrip().endswith("...")


class _CapturingAI(AIProvider):
    def __init__(self) -> None:
        self.contexts: list[str] = []

    @property
    def model_name(self) -> str:
        return "capturing"

    async def structured_completion(
        self, system_prompt: Any, user_prompt: Any, response_model: Any, **kwargs: Any
    ) -> Any:
        self.contexts.append(str(user_prompt))
        raise AssertionError("no canned decision needed")


def _tick() -> MarketTick:
    now = datetime(2026, 11, 2, 6, 30, tzinfo=UTC)
    return MarketTick(
        symbol="GAIL",
        last_price=Decimal("176.87"),
        exchange_timestamp=now,
        received_timestamp=now,
        processed_timestamp=now,
        raw_payload={},
    )


def _portfolio() -> PortfolioState:
    return PortfolioState(
        cash=Decimal("80000"),
        positions=[
            Position(symbol="GAIL", quantity=111, average_entry_price=Decimal("175.67"))
        ],
    )


async def test_review_includes_position_brief_when_provided() -> None:
    ai = _CapturingAI()
    manager = PortfolioManagerAgent(ai, "v13")
    with pytest.raises(AssertionError):
        await manager.review(
            _tick(),
            _portfolio(),
            None,
            position_brief="Open positions (yours):\n  GAIL: LONG 111 @ avg 175.67\n",
        )
    assert len(ai.contexts) == 1
    assert "GAIL: LONG 111 @ avg 175.67" in ai.contexts[0]
    assert "Portfolio:" in ai.contexts[0]


async def test_review_without_brief_keeps_legacy_shape() -> None:
    ai = _CapturingAI()
    manager = PortfolioManagerAgent(ai, "v13")
    with pytest.raises(AssertionError):
        await manager.review(_tick(), _portfolio(), None)
    assert len(ai.contexts) == 1
    assert "Open positions (yours" not in ai.contexts[0]


def test_v12_prompt_contains_exit_discipline() -> None:
    manager = PortfolioManagerAgent(_CapturingAI(), "v13")
    assert "Exit review comes first" in manager.prompt
    assert "OPEN POSITIONS" in manager.prompt
