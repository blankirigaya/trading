from ai_trader.config import Settings


def test_config_parses_csv_market_allow_lists() -> None:
    settings = Settings(allowed_exchanges="NSE,BSE", allowed_segments="EQ")
    assert settings.allowed_exchanges == ("NSE", "BSE")
    assert settings.allowed_segments == ("EQ",)
