"""Deterministic entry-plan checks and risk-based position sizing.

These are arithmetic guards around the model, not opinions: they decide whether
a stated plan is internally sound and how large a position that plan justifies.
Keeping them here (rather than in the workflow) makes every rule unit-testable
and reusable by a future replay harness.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import TYPE_CHECKING

from ai_trader.brokers.charges import ChargeRates, evaluate_entry_edge
from ai_trader.domain.orders import OrderSide

if TYPE_CHECKING:
    from ai_trader.config import Settings


@dataclass(frozen=True)
class RiskRules:
    """Deterministic risk policy: entry gates, sizing, and protective exits.

    Collected into one value so the workflow takes a single, testable argument
    instead of a dozen loose knobs, and so a future replay harness can sweep
    whole policies rather than individual fields.
    """

    charge_rates: ChargeRates | None = None
    min_edge_cost_multiple: Decimal = Decimal("2.5")
    min_reward_risk: Decimal = Decimal("2.0")
    risk_per_trade_pct: Decimal = Decimal("0.5")
    trail_atr_multiple: Decimal = Decimal("0.5")
    trail_give_back_fraction: Decimal = Decimal("0.5")
    time_stop_minutes: int = 90
    time_stop_min_gain_pct: Decimal = Decimal("0.25")
    exit_floor_cost_multiple: Decimal = Decimal("2.5")

    @classmethod
    def from_settings(cls, settings: Settings) -> RiskRules:
        from ai_trader.brokers.charges import charge_rates_for

        return cls(
            charge_rates=charge_rates_for(settings),
            min_edge_cost_multiple=settings.min_edge_cost_multiple,
            min_reward_risk=settings.min_reward_risk,
            risk_per_trade_pct=settings.risk_per_trade_pct,
            trail_atr_multiple=settings.trail_atr_multiple,
            trail_give_back_fraction=settings.trail_give_back_fraction,
            time_stop_minutes=settings.time_stop_minutes,
            time_stop_min_gain_pct=settings.time_stop_min_gain_pct,
            exit_floor_cost_multiple=settings.exit_floor_cost_multiple,
        )


@dataclass(frozen=True)
class EntryPlanCheck:
    """Outcome of the deterministic entry plan gate."""

    allowed: bool
    reason: str | None = None


def evaluate_entry_plan(
    *,
    entry_price: Decimal,
    target_price: Decimal | None,
    invalidation_price: Decimal | None,
    quantity: int,
    rates: ChargeRates | None,
    min_cost_multiple: Decimal,
    min_reward_risk: Decimal,
    entry_side: OrderSide = OrderSide.BUY,
) -> EntryPlanCheck:
    """Check a stated entry against round-trip costs and its own reward/risk.

    Two independent bars, both arithmetic:

    * the target must clear ``min_cost_multiple`` times the charges of the round
      trip that would realise it;
    * the distance to target must be at least ``min_reward_risk`` times the
      distance to invalidation, so a losing trade is always smaller than a
      winning one.

    Missing target or invalidation is treated as "not checkable" rather than a
    pass or a fail: presence is enforced where fresh AI output is accepted, and
    historical records must remain replayable.
    """
    if quantity <= 0 or entry_price <= 0:
        return EntryPlanCheck(allowed=True)

    # Long entries are the supported case; a short would invert both legs.
    sign = Decimal(1) if entry_side is OrderSide.BUY else Decimal(-1)
    target_distance = (
        (target_price - entry_price) * sign if target_price is not None else None
    )
    risk_distance = (
        (entry_price - invalidation_price) * sign
        if invalidation_price is not None
        else None
    )

    if target_distance is not None and min_cost_multiple > 0 and rates is not None:
        edge = evaluate_entry_edge(
            entry_price=entry_price,
            target_price=target_price,
            quantity=quantity,
            rates=rates,
            required_multiple=min_cost_multiple,
        )
        if edge.multiple is not None and not edge.allowed:
            return EntryPlanCheck(
                allowed=False,
                reason=(
                    f"edge gate: target {target_price} earns "
                    f"{edge.expected_gross_inr:.2f} INR gross against "
                    f"{edge.round_trip_cost_inr:.2f} INR round-trip charges "
                    f"({edge.multiple:.2f}x, needs {edge.required_multiple}x)"
                ),
            )

    if min_reward_risk > 0 and target_distance is not None and risk_distance is not None:
        if risk_distance <= 0:
            return EntryPlanCheck(
                allowed=False,
                reason=(
                    f"plan gate: invalidation {invalidation_price} is not below the "
                    f"entry {entry_price}; the trade has no bounded loss"
                ),
            )
        if target_distance <= 0:
            return EntryPlanCheck(
                allowed=False,
                reason=(
                    f"plan gate: target {target_price} does not exceed the entry "
                    f"{entry_price}; the trade has no upside"
                ),
            )
        reward_risk = target_distance / risk_distance
        if reward_risk < min_reward_risk:
            return EntryPlanCheck(
                allowed=False,
                reason=(
                    f"plan gate: reward/risk {reward_risk:.2f} is below the minimum "
                    f"{min_reward_risk:.2f} (reward {target_distance:.2f} vs "
                    f"risk {risk_distance:.2f} per share)"
                ),
            )

    return EntryPlanCheck(allowed=True)


@dataclass(frozen=True)
class RiskSizing:
    """Result of sizing a position from its own stop distance."""

    quantity: int
    risk_per_share: Decimal
    risk_budget: Decimal
    capped: bool


def size_entry_by_risk(
    *,
    equity: Decimal,
    risk_per_trade_pct: Decimal,
    entry_price: Decimal,
    invalidation_price: Decimal | None,
    requested_quantity: int | None,
) -> RiskSizing | None:
    """Cap an entry's size so its stop can never cost more than the risk budget.

    ``risk_budget`` is a fraction of current equity; ``risk_per_share`` is how far
    the stop sits below the entry. Dividing gives the largest share count whose
    full stop-out stays inside the budget. The requested size is only ever
    reduced, never increased — a model that deliberately chose a smaller position
    is left alone.

    Returns ``None`` when sizing does not apply (no invalidation, or the feature
    is switched off); a returned ``quantity`` of 0 means the plan is too risky to
    take at all.
    """
    if (
        risk_per_trade_pct <= 0
        or invalidation_price is None
        or entry_price <= 0
        or equity <= 0
    ):
        return None
    risk_per_share = entry_price - invalidation_price
    risk_budget = equity * risk_per_trade_pct / Decimal(100)
    if risk_per_share <= 0:
        return RiskSizing(
            quantity=0,
            risk_per_share=risk_per_share,
            risk_budget=risk_budget,
            capped=False,
        )
    cap = int((risk_budget / risk_per_share).to_integral_value(rounding=ROUND_DOWN))
    if requested_quantity is None:
        return RiskSizing(
            quantity=cap, risk_per_share=risk_per_share, risk_budget=risk_budget, capped=True
        )
    return RiskSizing(
        quantity=min(requested_quantity, cap),
        risk_per_share=risk_per_share,
        risk_budget=risk_budget,
        capped=requested_quantity > cap,
    )
