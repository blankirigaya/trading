from ai_trader.application.workflows.portfolio_review import PortfolioReviewWorkflow
from ai_trader.domain.events import MarketTickReceived


class ApplicationOrchestrator:
    def __init__(self, workflow: PortfolioReviewWorkflow) -> None:
        self.workflow = workflow

    async def on_market_tick(self, event: MarketTickReceived) -> None:
        await self.workflow.handle_market_tick(event)
