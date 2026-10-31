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

# Segment prefix (first 4 chars of a raw FYERS token) to HSM segment name,
# mirroring the reference fyers_apiv3 SDK's exch_seg_dict.
EXCHANGE_SEGMENTS = {
    "1010": "nse_cm",
    "1011": "nse_fo",
    "1020": "nse_com",
    "1120": "mcx_fo",
    "1210": "bse_cm",
    "1211": "bse_fo",
    "1212": "bcs_fo",
    "1012": "cde_fo",
}


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

    def hsm_topic(self, fytoken: str) -> str:
        """Build the HSM topic for a raw FYERS token such as ``10100000001594``.

        The wire topic uses the segment for the token's 4-char prefix and the
        token suffix after the 10-char exchange header (``sf|nse_cm|1594``),
        mirroring the reference SDK. Falls back to the exchange segment when
        the prefix is unknown.
        """
        segment = EXCHANGE_SEGMENTS.get(fytoken[:4], self.segment_token)
        suffix = fytoken[10:] if len(fytoken) > 10 else fytoken
        if segment is None or not suffix:
            raise InvalidFyersSymbol(f"cannot build HSM topic for token: {fytoken!r}")
        return f"sf|{segment}|{suffix}"


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


def hsm_token_suffix(fytoken: str) -> int:
    """Return the numeric HSM topic token for a raw FYERS token string."""
    suffix = fytoken[10:] if len(fytoken) > 10 else fytoken
    try:
        return int(suffix)
    except ValueError as exc:
        raise InvalidFyersSymbol(f"non-numeric FYERS token: {fytoken!r}") from exc


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
