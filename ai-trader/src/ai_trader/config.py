import json
import re
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
    # Intraday (MIS-style) margin multiple for paper fills. A BUY blocks
    # value / multiple of cash instead of the full notional; P&L stays on the
    # full notional. 1 preserves delivery-style blocking.
    paper_margin_multiple: Decimal = Decimal("1")
    # Brokerage and statutory charges applied to every paper fill. Rates are the
    # Zerodha equity-intraday schedule; see ai_trader.brokers.charges. Disabling
    # this keeps the old free-fill behaviour (useful for isolated experiments).
    paper_charges_enabled: bool = True
    paper_brokerage_pct: Decimal = Decimal("0.0003")
    paper_brokerage_cap_inr: Decimal = Decimal("20")
    paper_stt_sell_pct: Decimal = Decimal("0.00025")
    paper_exchange_txn_pct: Decimal = Decimal("0.0000297")
    paper_sebi_pct: Decimal = Decimal("0.000001")
    paper_stamp_duty_buy_pct: Decimal = Decimal("0.00003")
    paper_gst_pct: Decimal = Decimal("0.18")
    # Minimum-edge entry bar: a proposed target must earn at least this many
    # times the round-trip charges that realising it would cost. Purely
    # arithmetic — it never consults the model. 1.0 would merely break even
    # before slippage, so the default keeps a real margin. Tune with
    # MIN_EDGE_COST_MULTIPLE; set 0 to disable.
    min_edge_cost_multiple: Decimal = Decimal("2.5")
    execution_broker: str = "paper"
    live_trading_approved: bool = False
    live_allowed_symbols: tuple[str, ...] = ("NSE:INFY-EQ",)
    live_max_order_value_inr: Decimal = Decimal("2000")
    live_max_position_value_inr: Decimal = Decimal("5000")
    live_max_daily_loss_inr: Decimal = Decimal("500")
    live_trading_start_ist: str = "09:20"
    live_trading_end_ist: str = "15:15"
    live_broker_timeout_seconds: int = 15
    ai_base_url: str = ""
    ai_model: str = ""
    ai_api_key: str | None = None
    ai_provider: str = "remote_openai_compatible"
    ai_timeout_seconds: int = 120
    ai_max_concurrent_requests: int = 1
    ai_schema_max_retries: int = 2
    bedrock_region: str = "ap-south-1"
    aws_region: str | None = None
    bedrock_model_id: str = "apac.amazon.nova-pro-v1:0"
    bedrock_max_input_tokens: int = 32000
    bedrock_max_tokens: int = 1024
    bedrock_temperature: float = 0.0
    bedrock_timeout_seconds: int = 30
    bedrock_max_retries: int = 2
    bedrock_retry_base_seconds: float = 0.5
    bedrock_retry_max_seconds: float = 8.0
    bedrock_max_estimated_usd_per_request: float | None = None
    bedrock_input_usd_per_million_tokens: float | None = None
    bedrock_output_usd_per_million_tokens: float | None = None
    # Cheaper Bedrock model for routine cadence reviews (e.g. Nova Micro via an
    # inference profile). Empty means cadence uses BEDROCK_MODEL_ID. News
    # analysis and anything built without the cadence flag keep the main model.
    bedrock_cadence_model_id: str = ""
    bedrock_cadence_input_usd_per_million_tokens: float | None = None
    bedrock_cadence_output_usd_per_million_tokens: float | None = None
    jev_api_key: str | None = None
    jev_enabled: bool = False
    jev_base_url: str = "https://api.typesafe.ai"
    jev_model: str = "jev-latest"
    jev_failure_mode: str = "FAIL_CLOSED"
    upstox_access_token: str | None = None
    upstox_api_base_url: str = "https://api.upstox.com"
    upstox_order_base_url: str = "https://api-hft.upstox.com"
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
    # Broad candidate list the AI universe is selected from each cycle. Empty
    # uses the built-in liquid list (application.universe.MASTER_UNIVERSE).
    universe_master: tuple[str, ...] = ()
    # cheap | mixed | expensive. The persisted control value wins over this
    # default once the operator switches it from the UI.
    universe_mode: str = "cheap"
    # How many symbols the AI reviews per cycle (split across shards).
    universe_size: int = 100
    # Price boundary between the cheap and expensive buckets, in INR.
    universe_cheap_max_price_inr: Decimal = Decimal("200")
    shadow_initial_capital_inr: Decimal = Decimal("100000")
    # Operator override for the fixed money caps. When true, MAX_AI_CAPITAL_INR,
    # MAX_SINGLE_POSITION_INR, and MAX_GROSS_EXPOSURE_INR stop constraining new
    # buys and the AI sizes positions inside the paper account's available cash.
    # Persisted in the control table; this is the default before the operator
    # flips it from the UI.
    capital_cap_disabled: bool = False
    # Operator override for the max-open-positions cap. When true, the safety
    # envelope stops limiting how many distinct symbols can be held at once;
    # paper cash remains the real limit. Persisted in the control table.
    position_count_cap_disabled: bool = False
    # Kill switches for the news pipeline and the Sonnet escalation lever.
    # All persisted in the control table; the UI toggles them at runtime.
    # Tier-1 desks (CNBC-TV18, NDTV Profit, Business Today) on/off.
    tier1_news_enabled: bool = True
    # AI analysis of news items on/off. When off, headlines still ingest but
    # no Sonnet analysis runs, so no news-triggered reviews happen either.
    news_analysis_enabled: bool = True
    # Route news-triggered reviews through Sonnet instead of the cadence
    # model. Gated by ai_max_sonnet_escalations_per_day.
    sonnet_escalation_enabled: bool = True
    ai_max_sonnet_escalations_per_day: int = 20
    # Jev entry bar: an OPEN or INCREASE_POSITION proposal only reaches the
    # broker when the critic's score for the decision is at least this high.
    # Weak agrees used to sail through and bleed on charges; exits are never
    # gated, so winners stay easy to bank. Tune with JEV_MIN_ENTRY_SCORE.
    jev_min_entry_score: float = 0.65
    portfolio_review_interval_seconds: int = 60
    # Daily Bedrock spend cap in USD. When set (and per-model rates are
    # configured so spend is measurable), cadence and news reviews pause for
    # the rest of the IST trading day once estimated spend crosses it. Paper
    # positions stay open; the 15:10 square-off still runs.
    ai_max_daily_spend_usd: float | None = None
    # Parallel reviewer shards. 1 preserves the legacy single reviewer over the
    # whole watchlist. Higher values split symbols round-robin across independent
    # reviewers, each with its own slice of paper capital (even split).
    ai_review_shards: int = 1
    # Intraday discipline: the session flattens every open paper position at or
    # after this IST time and opens nothing new afterwards. No overnight carry.
    intraday_square_off_time_ist: str = "15:10"
    # A review (and any trade from it) requires a fresh, complete market state:
    # a positive last price, a current OHLCV candle, and a tick received within
    # this many seconds. Stale or partial data skips the review instead of
    # trading blind.
    max_market_data_age_seconds: int = 120
    # A connected-but-silent FYERS socket trips no reconnect on its own, so the
    # session prods it when no datafeed frame arrives for this long during NSE
    # hours. Fires before reviews start skipping (see max_market_data_age_seconds).
    # Overnight/weekend silence is normal and never triggers.
    feed_idle_reconnect_seconds: int = 90
    # Minimum gap between forced reconnects: a persistent outage must not spin.
    feed_reconnect_cooldown_seconds: int = 300
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

    @field_validator("bedrock_max_input_tokens", "bedrock_max_tokens")
    @classmethod
    def _positive_bedrock_tokens(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Bedrock token limits must be positive")
        return value

    @field_validator("bedrock_temperature")
    @classmethod
    def _bedrock_temperature_range(cls, value: float) -> float:
        if not 0 <= value <= 1:
            raise ValueError("BEDROCK_TEMPERATURE must be between 0 and 1")
        return value

    @field_validator("bedrock_timeout_seconds")
    @classmethod
    def _positive_bedrock_timeout(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("BEDROCK_TIMEOUT_SECONDS must be positive")
        return value

    @field_validator("bedrock_max_retries")
    @classmethod
    def _nonnegative_bedrock_retries(cls, value: int) -> int:
        if value < 0:
            raise ValueError("BEDROCK_MAX_RETRIES cannot be negative")
        return value

    @field_validator("bedrock_retry_base_seconds", "bedrock_retry_max_seconds")
    @classmethod
    def _positive_bedrock_retry_delay(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("Bedrock retry delays must be positive")
        return value

    @field_validator(
        "bedrock_max_estimated_usd_per_request",
        "bedrock_input_usd_per_million_tokens",
        "bedrock_output_usd_per_million_tokens",
        "bedrock_cadence_input_usd_per_million_tokens",
        "bedrock_cadence_output_usd_per_million_tokens",
        mode="before",
    )
    @classmethod
    def _optional_bedrock_amount(cls, value: object) -> object:
        if value in (None, ""):
            return None
        if isinstance(value, (int, float)) and value < 0:
            raise ValueError("Bedrock cost controls cannot be negative")
        return value

    @field_validator("execution_broker")
    @classmethod
    def _supported_execution_broker(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"paper", "fyers", "upstox"}:
            raise ValueError("EXECUTION_BROKER must be paper, fyers, or upstox")
        return normalized

    @field_validator("live_allowed_symbols", mode="before")
    @classmethod
    def _live_allowed_symbols(cls, value: object) -> object:
        parsed: object = value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except ValueError:
                parsed = [item.strip() for item in value.split(",") if item.strip()]
        if not isinstance(parsed, (list, tuple)) or not parsed:
            raise ValueError("LIVE_ALLOWED_SYMBOLS must contain at least one symbol")
        return tuple(parsed)

    @field_validator(
        "live_max_order_value_inr",
        "live_max_position_value_inr",
        "live_max_daily_loss_inr",
    )
    @classmethod
    def _positive_live_limits(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("Live trading limits must be positive")
        return value

    @field_validator("universe_cheap_max_price_inr")
    @classmethod
    def _positive_universe_cap(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("UNIVERSE_CHEAP_MAX_PRICE_INR must be positive")
        return value

    @field_validator("live_broker_timeout_seconds")
    @classmethod
    def _positive_live_timeout(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("LIVE_BROKER_TIMEOUT_SECONDS must be positive")
        return value

    @field_validator("universe_mode")
    @classmethod
    def _supported_universe_mode(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"cheap", "mixed", "expensive"}:
            raise ValueError("UNIVERSE_MODE must be cheap, mixed, or expensive")
        return normalized

    @field_validator("universe_size")
    @classmethod
    def _positive_universe_size(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("UNIVERSE_SIZE must be positive")
        return value

    @field_validator("universe_master", mode="before")
    @classmethod
    def _universe_master(cls, value: object) -> object:
        if value in (None, ""):
            return ()
        parsed: object = value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except ValueError:
                parsed = [item.strip() for item in value.split(",") if item.strip()]
        if not isinstance(parsed, (list, tuple)):
            raise ValueError("UNIVERSE_MASTER must be a list of FYERS symbols")
        return tuple(parsed)

    @field_validator("paper_margin_multiple", mode="before")
    @classmethod
    def _positive_paper_margin(cls, value: object) -> object:
        try:
            multiple = Decimal(str(value))
        except (ArithmeticError, ValueError) as exc:
            raise ValueError("PAPER_MARGIN_MULTIPLE must be a positive number") from exc
        if multiple <= 0:
            raise ValueError("PAPER_MARGIN_MULTIPLE must be positive")
        return multiple

    @field_validator(
        "paper_brokerage_pct",
        "paper_brokerage_cap_inr",
        "paper_stt_sell_pct",
        "paper_exchange_txn_pct",
        "paper_sebi_pct",
        "paper_stamp_duty_buy_pct",
        "paper_gst_pct",
        mode="before",
    )
    @classmethod
    def _non_negative_charge_rate(cls, value: object) -> object:
        name = "paper charge rate"
        try:
            rate = Decimal(str(value))
        except (ArithmeticError, ValueError) as exc:
            raise ValueError(f"{name} must be a non-negative number") from exc
        if rate < 0:
            raise ValueError(f"{name} must not be negative")
        return rate

    @field_validator("ai_max_sonnet_escalations_per_day")
    @classmethod
    def _non_negative_escalation_cap(cls, value: int) -> int:
        if value < 0:
            raise ValueError("AI_MAX_SONNET_ESCALATIONS_PER_DAY cannot be negative")
        return value

    @field_validator("jev_min_entry_score")
    @classmethod
    def _entry_score_in_unit_interval(cls, value: float) -> float:
        if not 0 <= value <= 1:
            raise ValueError("JEV_MIN_ENTRY_SCORE must be between 0 and 1")
        return value

    @field_validator("min_edge_cost_multiple")
    @classmethod
    def _edge_multiple_not_negative(cls, value: Decimal) -> Decimal:
        if value < 0:
            raise ValueError("MIN_EDGE_COST_MULTIPLE must not be negative")
        return value

    @field_validator("live_trading_start_ist", "live_trading_end_ist")
    @classmethod
    def _live_trading_time(cls, value: str) -> str:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value.strip()):
            raise ValueError("Live trading times must use HH:MM")
        return value.strip()

    def model_post_init(self, __context: Any) -> None:
        if self.live_trading_start_ist >= self.live_trading_end_ist:
            raise ValueError("LIVE_TRADING_START_IST must be before LIVE_TRADING_END_IST")
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
