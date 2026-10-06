import pytest
from pydantic import ValidationError

from ai_trader.domain.decisions import PortfolioAction, TradeProposal


def test_open_position_requires_order_fields() -> None:
    with pytest.raises(ValidationError, match="trade proposals require"):
        TradeProposal(
            proposal_id="p",
            action=PortfolioAction.OPEN_POSITION,
            confidence=0.5,
            thesis="test",
            evidence=[],
            conditions_that_change_thesis=[],
        )
