"""Zerodha equity-intraday charge model for paper fills.

The paper book used to fill for free, which let the AI churn for a few rupees
of gross profit that a real account would have lost to costs. Every fill now
carries the statutory breakdown below, computed from that fill's own turnover.

Rates mirror Zerodha's published equity-intraday schedule (2026): brokerage
0.03% or Rs 20 per executed order (whichever is lower), STT 0.025% on the sell
side, NSE transaction charge 0.00297%, SEBI Rs 10 per crore, stamp duty 0.003%
on the buy side, and 18% GST on brokerage + transaction + SEBI. There is no
daily clubbing or monthly minimum: each fill stands alone.

These are estimates for paper accounting, not a broker contract note.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from ai_trader.domain.orders import OrderSide

if TYPE_CHECKING:
    from ai_trader.config import Settings

PAISE = Decimal("0.01")
ZERO = Decimal("0.00")


def _paise(value: Decimal) -> Decimal:
    return value.quantize(PAISE, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class ChargeRates:
    """Tunable rates; defaults are the Zerodha equity-intraday schedule."""

    brokerage_pct: Decimal = Decimal("0.0003")
    brokerage_cap_inr: Decimal = Decimal("20")
    stt_sell_pct: Decimal = Decimal("0.00025")
    exchange_pct: Decimal = Decimal("0.0000297")
    sebi_pct: Decimal = Decimal("0.000001")
    stamp_duty_buy_pct: Decimal = Decimal("0.00003")
    gst_pct: Decimal = Decimal("0.18")

    @classmethod
    def from_settings(cls, settings: "Settings") -> "ChargeRates":
        return cls(
            brokerage_pct=settings.paper_brokerage_pct,
            brokerage_cap_inr=settings.paper_brokerage_cap_inr,
            stt_sell_pct=settings.paper_stt_sell_pct,
            exchange_pct=settings.paper_exchange_txn_pct,
            sebi_pct=settings.paper_sebi_pct,
            stamp_duty_buy_pct=settings.paper_stamp_duty_buy_pct,
            gst_pct=settings.paper_gst_pct,
        )


def charge_rates_for(settings: "Settings") -> ChargeRates | None:
    """Charge schedule for a settings object, or ``None`` when disabled."""
    if not settings.paper_charges_enabled:
        return None
    return ChargeRates.from_settings(settings)


@dataclass(frozen=True)
class ChargeBreakdown:
    brokerage: Decimal = ZERO
    stt: Decimal = ZERO
    exchange: Decimal = ZERO
    sebi: Decimal = ZERO
    stamp_duty: Decimal = ZERO
    gst: Decimal = ZERO

    @property
    def total(self) -> Decimal:
        return _paise(
            self.brokerage
            + self.stt
            + self.exchange
            + self.sebi
            + self.stamp_duty
            + self.gst
        )

    def as_dict(self) -> dict[str, Decimal]:
        return {
            "brokerage": self.brokerage,
            "stt": self.stt,
            "exchange": self.exchange,
            "sebi": self.sebi,
            "stamp_duty": self.stamp_duty,
            "gst": self.gst,
            "total": self.total,
        }


def compute_charges(
    side: OrderSide,
    turnover: Decimal,
    rates: ChargeRates | None = None,
) -> ChargeBreakdown:
    """Charges for one fill at ``turnover`` (price x quantity)."""
    schedule = rates or ChargeRates()
    if turnover <= 0:
        return ChargeBreakdown()

    brokerage = _paise(min(turnover * schedule.brokerage_pct, schedule.brokerage_cap_inr))
    exchange = _paise(turnover * schedule.exchange_pct)
    sebi = _paise(turnover * schedule.sebi_pct)
    gst = _paise((brokerage + exchange + sebi) * schedule.gst_pct)
    stt = _paise(turnover * schedule.stt_sell_pct) if side == OrderSide.SELL else ZERO
    stamp_duty = (
        _paise(turnover * schedule.stamp_duty_buy_pct) if side == OrderSide.BUY else ZERO
    )
    return ChargeBreakdown(
        brokerage=brokerage,
        stt=stt,
        exchange=exchange,
        sebi=sebi,
        stamp_duty=stamp_duty,
        gst=gst,
    )
