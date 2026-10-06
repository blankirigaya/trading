"""FYERS symbol identity and internal instrument resolution.

Wire symbols use FYERS' own format (``NSE:INFY-EQ``). The domain keeps an internal
instrument identity so application code never depends on FYERS formatting.
"""

import re
from dataclasses import dataclass

from ai_trader.domain.market import Instrument

SYMBOL_PATTERN = re.compile(
    r"^(?P<exchange>[A-Z]+):(?P<ticker>[A-Z0-9&._-]+?)(?:-(?P<suffix>EQ|FUT|OPT))?$"
)

# FYERS segment token used in HSM topics for NSE cash-market equity.
NSE_EQUITY_SEGMENT = "nse_cm"
BSE_EQUITY_SEGMENT = "bse_cm"


class InvalidFyersSymbol(ValueError):
    """The configured symbol is not a supported FYERS equity symbol."""


@dataclass(frozen=True)
class FyersSymbol:
    exchange: str
    ticker: str
    suffix: str | None
    text: str

    @property
    def is_equity(self) -> bool:
        return self.suffix in (None, "EQ")

    @property
    def segment_token(self) -> str | None:
        if self.exchange == "NSE":
            return NSE_EQUITY_SEGMENT
        if self.exchange == "BSE":
            return BSE_EQUITY_SEGMENT
        return None

    def hsm_topic(self, token: int) -> str:
        segment = self.segment_token
        if segment is None:
            raise InvalidFyersSymbol(f"unsupported FYERS exchange: {self.exchange}")
        return f"sf|{segment}|{token}"


def parse_symbol(symbol: str) -> FyersSymbol:
    match = SYMBOL_PATTERN.match(symbol.strip().upper())
    if match is None:
        raise InvalidFyersSymbol(f"cannot parse FYERS symbol: {symbol!r}")
    return FyersSymbol(
        exchange=match.group("exchange"),
        ticker=match.group("ticker"),
        suffix=match.group("suffix"),
        text=symbol.strip().upper(),
    )


def to_instrument(symbol: str, token: int | None = None) -> Instrument:
    """Map a FYERS equity symbol onto the internal instrument identity.

    ``token`` is the FYERS exchange token, stored as the instrument key when known so
    the domain stays independent of FYERS symbol formatting either way.
    """
    parsed = parse_symbol(symbol)
    if not parsed.is_equity:
        raise InvalidFyersSymbol(f"only equity symbols are supported: {symbol!r}")
    instrument_key = f"{parsed.exchange}:{parsed.ticker}-EQ" if token is None else str(token)
    return Instrument(
        instrument_key=instrument_key,
        symbol=parsed.ticker,
        exchange=parsed.exchange,
        segment="EQ",
    )


def resolve_universe(symbols: list[str]) -> list[Instrument]:
    return [to_instrument(symbol) for symbol in symbols]


def match_hsm_topic(topic: str, mapping: dict[str, str]) -> str | None:
    """Map a wire topic (``sf|nse_cm|3045``) back to a configured trading symbol."""
    return mapping.get(topic)
