"""Price-based universe selection: cheap / expensive / mixed."""

from decimal import Decimal

import pytest

from ai_trader.application.universe import (
    MASTER_UNIVERSE,
    normalize_mode,
    price_allowed,
    select_universe,
)


def _prices(cheap: list[str], expensive: list[str]) -> dict[str, Decimal]:
    prices = {symbol: Decimal("100") for symbol in cheap}
    prices.update({symbol: Decimal("500") for symbol in expensive})
    return prices


def test_master_universe_is_wide_enough_for_every_mode() -> None:
    assert len(MASTER_UNIVERSE) >= 150
    assert len(set(MASTER_UNIVERSE)) == len(MASTER_UNIVERSE)
    assert all(symbol.startswith("NSE:") and symbol.endswith("-EQ") for symbol in MASTER_UNIVERSE)


def test_cheap_mode_picks_only_sub_200_names_in_master_order() -> None:
    master = ("NSE:A-EQ", "NSE:B-EQ", "NSE:C-EQ", "NSE:D-EQ")
    prices = {
        "NSE:A-EQ": Decimal("250"),
        "NSE:B-EQ": Decimal("150"),
        "NSE:C-EQ": Decimal("50"),
        "NSE:D-EQ": Decimal("199.99"),
    }
    assert select_universe(master, prices, "cheap", size=2) == ["NSE:B-EQ", "NSE:C-EQ"]


def test_expensive_mode_picks_only_at_or_above_the_cap() -> None:
    master = ("NSE:A-EQ", "NSE:B-EQ", "NSE:C-EQ")
    prices = {
        "NSE:A-EQ": Decimal("200"),
        "NSE:B-EQ": Decimal("199"),
        "NSE:C-EQ": Decimal("900"),
    }
    assert select_universe(master, prices, "expensive", size=2) == ["NSE:A-EQ", "NSE:C-EQ"]


def test_mixed_mode_splits_evenly() -> None:
    master = tuple(f"NSE:C{i}-EQ" for i in range(5)) + tuple(f"NSE:E{i}-EQ" for i in range(5))
    prices = _prices(
        [f"NSE:C{i}-EQ" for i in range(5)],
        [f"NSE:E{i}-EQ" for i in range(5)],
    )
    picked = select_universe(master, prices, "mixed", size=4)
    assert picked == ["NSE:C0-EQ", "NSE:C1-EQ", "NSE:E0-EQ", "NSE:E1-EQ"]


def test_short_bucket_is_topped_up_from_the_other_bucket() -> None:
    master = ("NSE:C0-EQ", "NSE:E0-EQ", "NSE:E1-EQ", "NSE:E2-EQ")
    prices = _prices(["NSE:C0-EQ"], ["NSE:E0-EQ", "NSE:E1-EQ", "NSE:E2-EQ"])
    assert select_universe(master, prices, "cheap", size=3) == [
        "NSE:C0-EQ",
        "NSE:E0-EQ",
        "NSE:E1-EQ",
    ]
    picked = select_universe(master, prices, "mixed", size=4)
    assert picked[0] == "NSE:C0-EQ"
    assert len(picked) == 4


def test_symbols_without_a_price_are_not_selected() -> None:
    master = ("NSE:KNOWN-EQ", "NSE:UNKNOWN-EQ")
    assert select_universe(master, {"NSE:KNOWN-EQ": Decimal("100")}, "cheap") == [
        "NSE:KNOWN-EQ"
    ]


def test_duplicates_in_the_master_are_ignored() -> None:
    master = ("NSE:A-EQ", "NSE:A-EQ", "NSE:B-EQ")
    prices = {"NSE:A-EQ": Decimal("100"), "NSE:B-EQ": Decimal("100")}
    assert select_universe(master, prices, "cheap") == ["NSE:A-EQ", "NSE:B-EQ"]


def test_price_filter_drops_names_that_crossed_the_cap() -> None:
    assert price_allowed("cheap", Decimal("199")) is True
    assert price_allowed("cheap", Decimal("200")) is False
    assert price_allowed("expensive", Decimal("200")) is True
    assert price_allowed("expensive", Decimal("199")) is False
    assert price_allowed("mixed", Decimal("1")) is True


def test_unknown_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="cheap, mixed, expensive"):
        normalize_mode("momentum")
