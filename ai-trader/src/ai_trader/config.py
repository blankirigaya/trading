from decimal import Decimal
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    app_mode: str = "PAPER"
    database_url: str = "sqlite:///./ai_trader.db"
    redis_url: str | None = None
    broker_provider: str = "paper"
    paper_initial_capital: Decimal = Decimal("100000")
    ai_base_url: str = ""
    ai_model: str = ""
    ai_api_key: str | None = None
    ai_provider: str = "remote_openai_compatible"
    ai_timeout_seconds: int = 120
    ai_max_concurrent_requests: int = 1
    ai_schema_max_retries: int = 2
    jev_api_key: str | None = None
    jev_enabled: bool = False
    jev_base_url: str = "https://api.typesafe.ai"
    jev_model: str = "jev-latest"
    jev_failure_mode: str = "FAIL_CLOSED"
    upstox_access_token: str | None = None
    upstox_api_base_url: str = "https://api.upstox.com"
    market_provider: str = "fyers"
    fyers_client_id: str | None = None
    fyers_secret_key: str | None = None
    fyers_redirect_uri: str | None = None
    fyers_access_token: str | None = None
    fyers_api_base_url: str = "https://api-t1.fyers.in"
    fyers_data_ws_url: str = "wss://socket.fyers.in/hsm/v1-5/prod"
    fyers_feed_mode: str = "symbolUpdate"
    shadow_symbol: str = "INFY"
    shadow_exchange: str = "NSE"
    shadow_symbols: tuple[str, ...] = ("NSE:INFY-EQ",)
    shadow_initial_capital_inr: Decimal = Decimal("100000")
    portfolio_review_interval_seconds: int = 60
    live_order_execution: bool = False
    trading_enabled: bool = False
    max_ai_capital_inr: Decimal = Decimal("50000")
    max_gross_exposure_inr: Decimal = Decimal("50000")
    max_daily_loss_inr: Decimal = Decimal("5000")
    max_single_position_inr: Decimal = Decimal("25000")
    max_open_positions: int = 5
    max_orders_per_minute: int = 10
    allowed_exchanges: tuple[str, ...] = ("NSE",)
    allowed_segments: tuple[str, ...] = ("EQ",)

    # News intelligence
    news_enabled: bool = True
    news_poll_interval_seconds: int = 900
    news_ai_max_concurrent_requests: int = 1
    news_ai_max_retries: int = 1
    news_queue_capacity: int = 200
    news_headline_similarity_threshold: float = 0.6
    news_max_items_per_poll: int = 40
    news_documents_enabled: bool = True
    news_document_storage: str = "data/raw/news_documents"
    news_excerpt_chars: int = 600
    # Continuous ingestion. Per-source intervals let an official exchange feed be
    # polled far more aggressively than a regulator's site.
    nse_poll_seconds: int = 5
    bse_poll_seconds: int = 5
    financial_news_poll_seconds: int = 10
    rbi_poll_seconds: int = 30
    sebi_poll_seconds: int = 30
    # Bounded exponential backoff for a failing provider.
    news_error_backoff_initial_seconds: float = 2.0
    news_error_backoff_max_seconds: float = 300.0
    # Consecutive failures before a feed is reported DEGRADED / OFFLINE in the UI.
    news_degraded_after_failures: int = 2
    news_offline_after_failures: int = 5
    # A news item that concerns a currently held symbol always triggers a review,
    # so an existing position can be re-examined on new information.
    news_review_held_positions: bool = True
    # Hosts permitted for document downloads. Anything else is refused.
    news_document_allowed_hosts: tuple[str, ...] = (
        "nsearchives.nseindia.com",
        "nseindia.com",
        "rbi.org.in",
        "website.rbi.org.in",
        "rbidocs.rbi.org.in",
        "sebi.gov.in",
        "bseindia.com",
        "pii.bseindia.com",
    )

    def poll_interval_for(self, source_id: str) -> int:
        """Per-source poll interval. Unknown sources fall back to the general cadence."""
        table: dict[str, int] = {
            "nse-corporate": self.nse_poll_seconds,
            "bse-corporate": self.bse_poll_seconds,
            "rbi-press-releases": self.rbi_poll_seconds,
            "sebi-news": self.sebi_poll_seconds,
        }
        if source_id in table:
            return table[source_id]
        if source_id.startswith(("livemint-", "et-", "bs-", "mint-", "cnbc-", "reuters-")):
            return self.financial_news_poll_seconds
        return self.news_poll_interval_seconds

    def model_post_init(self, __context: Any) -> None:
        # Enforce the single-inference-slot invariant regardless of configuration.
        # The remote model server is a shared resource with the portfolio AI, so more
        # than one concurrent news request would contend with it.
        self.news_ai_max_concurrent_requests = max(1, self.news_ai_max_concurrent_requests)
        self.news_queue_capacity = max(1, self.news_queue_capacity)

    @field_validator("allowed_exchanges", "allowed_segments", mode="before")
    @classmethod
    def split_csv(cls, value: str | tuple[str, ...]) -> tuple[str, ...]:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value
