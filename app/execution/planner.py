"""Execution planner — the LOCAL brain of the thin client.

Turns one impersonal signal + the user's own buying power + their local risk budget into a
concrete decision: place a specific order, or skip with a reason. This is where the
"impersonal -> personal" conversion happens entirely on the user's machine.

Key rule it encodes (the Chunk-0 finding): Robinhood allows fractional shares only on
MARKET + regular_hours orders. So when a whole-share LIMIT order rounds to 0 (small account,
pricey stock), we either fall back to a fractional dollar-based MARKET order (losing the
limit-price protection) or skip — controlled by RiskConfig.small_account_market_fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from app.adapters.robinhood_adapter import OrderRequest
from app.config import RiskConfig
from app.sizing.calculator import size_position


@dataclass
class ExecutionPlan:
    decision: str  # "place" | "skip"
    signal: dict
    reason: str = ""  # populated when skipped or when a fallback changed the order
    order: OrderRequest | None = None
    shares: Decimal | None = None
    est_cost: Decimal | None = None
    fractional: bool = False
    review: object | None = None  # OrderResult from broker.review_order (paper review)


def _dec(x) -> Decimal | None:
    if x is None or x == "":
        return None
    return Decimal(str(x))


def _expired(valid_until: str | None, now: datetime) -> bool:
    if not valid_until:
        return False
    try:
        ts = datetime.fromisoformat(valid_until.replace("Z", "+00:00"))
    except ValueError:
        return False
    return now > ts


def plan_order(
    signal: dict,
    *,
    buying_power: Decimal,
    risk: RiskConfig,
    account_number: str,
    fractional_allowed: bool = True,
    open_positions: int = 0,
    now: datetime | None = None,
) -> ExecutionPlan:
    now = now or datetime.now(timezone.utc)
    order_spec = signal.get("order", {})
    ref = signal.get("reference", {})

    def skip(reason: str) -> ExecutionPlan:
        return ExecutionPlan(decision="skip", signal=signal, reason=reason)

    if _expired(signal.get("valid_until"), now):
        return skip("expired")

    if open_positions >= risk.max_concurrent_positions:
        return skip(f"position cap ({risk.max_concurrent_positions}) reached")

    entry = _dec(order_spec.get("limit_price")) or _dec(ref.get("trigger_price"))
    if entry is None or entry <= 0:
        return skip("no entry price")
    if buying_power <= 0:
        return skip("no buying power")

    stop = _dec((signal.get("risk") or {}).get("stop_loss"))

    # First try a whole-share order in the signal's native order type (limit for our signals).
    shares = size_position(
        entry_price=entry,
        stop_loss=stop,
        available_cash=buying_power,
        risk=risk,
        allow_fractional=False,
    )

    if shares >= 1:
        take_profit = _dec((signal.get("risk") or {}).get("take_profit"))
        order = OrderRequest(
            account_number=account_number,
            symbol=signal["symbol"],
            side=signal.get("side", "buy"),
            type=order_spec.get("type", "limit"),
            quantity=shares,
            limit_price=entry if "limit" in order_spec.get("type", "limit") else None,
            stop_price=_dec(order_spec.get("stop_price")),
            time_in_force=order_spec.get("time_in_force", "gfd"),
            market_hours=order_spec.get("market_hours", "regular_hours"),
            # attach exit legs only when they form a valid long bracket (tp > entry > sl)
            bracket_take_profit=take_profit if (take_profit and take_profit > entry) else None,
            bracket_stop_loss=stop if (stop and stop < entry) else None,
        )
        return ExecutionPlan(
            decision="place", signal=signal, order=order, shares=shares, est_cost=shares * entry
        )

    # Whole-share rounded to 0 → small-account fork.
    can_fractional = (
        risk.small_account_market_fallback
        and fractional_allowed
        and order_spec.get("market_hours", "regular_hours") == "regular_hours"
    )
    if not can_fractional:
        return skip("balance too small for 1 share (enable market fallback for fractional)")

    # Fractional dollar-based MARKET order. Notional capped by position size + buying power.
    notional = min(buying_power * Decimal(str(risk.max_position_pct)), buying_power)
    notional = notional.quantize(Decimal("0.01"))
    if notional <= 0:
        return skip("balance too small")

    order = OrderRequest(
        account_number=account_number,
        symbol=signal["symbol"],
        side=signal.get("side", "buy"),
        type="market",  # required for fractional
        dollar_amount=notional,
        market_hours="regular_hours",
        time_in_force=order_spec.get("time_in_force", "gfd"),
    )
    return ExecutionPlan(
        decision="place",
        signal=signal,
        order=order,
        shares=(notional / entry),
        est_cost=notional,
        fractional=True,
        reason="fractional market fallback (no limit-price protection)",
    )
