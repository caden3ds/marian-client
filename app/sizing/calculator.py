"""LOCAL position sizing — the compliance keystone.

The cloud signal contains NO quantity and NO dollar amount. This module converts an
impersonal signal + the user's own agentic-account cash + their local risk budget into a
concrete share count. Because this runs entirely on the user's machine, the cloud never
provides personalized advice.

Uses Decimal throughout to match the Robinhood order API (which takes decimal strings).
"""

from decimal import Decimal, ROUND_DOWN

from app.config import RiskConfig


def size_position(
    *,
    entry_price: Decimal,
    stop_loss: Decimal | None,
    available_cash: Decimal,
    risk: RiskConfig,
    allow_fractional: bool = False,
) -> Decimal:
    """Return the share quantity to trade. 0 means 'skip'.

    Sizing rule (risk-based when a stop exists, else cap-based):
      - If stop_loss is given: risk_budget = cash * risk_pct_per_trade;
        shares = risk_budget / |entry - stop|.
      - Always cap notional at cash * max_position_pct and at available_cash.
    """
    if entry_price <= 0 or available_cash <= 0:
        return Decimal(0)

    max_notional = available_cash * Decimal(str(risk.max_position_pct))

    if stop_loss is not None and stop_loss > 0:
        per_share_risk = abs(entry_price - stop_loss)
        if per_share_risk == 0:
            shares = max_notional / entry_price
        else:
            risk_budget = available_cash * Decimal(str(risk.risk_pct_per_trade))
            shares = risk_budget / per_share_risk
    else:
        shares = max_notional / entry_price

    # Apply notional caps.
    shares = min(shares, max_notional / entry_price, available_cash / entry_price)

    if shares <= 0:
        return Decimal(0)

    if allow_fractional:
        return shares.quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
    return shares.quantize(Decimal("1"), rounding=ROUND_DOWN)
