"""ExecutionEngine — drives a signal through sizing → review → (tiered) execution.

Flow per signal:
  preview(signal)  -> ExecutionPlan (local sizing + broker review; captures disclosure)
  execute(plan)    -> places the order (paper sim or live), records position + blotter + P&L

Tiers are dispatched by the UI (manual approval / 15s delay / auto), but the engine enforces
the safety-relevant parts: a global KILL SWITCH, the concurrency cap, and position tracking.
Paper mode uses PaperBroker (simulated fills); live uses LiveBroker (real place_equity_order).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

from app.adapters.robinhood_adapter import Broker, OrderRequest, OrderResult
from app.config import RiskConfig, AUTO
from app.execution.planner import ExecutionPlan, plan_order, _dec


@dataclass
class Position:
    strategy: str
    symbol: str
    qty: Decimal
    entry: Decimal
    stop: Decimal | None
    target: Decimal | None
    fractional: bool


class ExecutionEngine:
    def __init__(
        self,
        broker: Broker,
        risk: RiskConfig,
        account_number: str,
        fractional_lookup: Callable[[str], bool] | None = None,
        tier: str = AUTO,
    ):
        self.broker = broker
        self.risk = risk
        self.account_number = account_number
        self.fractional_lookup = fractional_lookup or (lambda _sym: True)
        self.tier = tier
        self.killed = False
        self.positions: dict[tuple[str, str], Position] = {}
        self.blotter: list[dict] = []
        self.review_detail: dict[str, str] = {}
        self.realized_pnl: Decimal = Decimal(0)

    # ---- kill switch ---------------------------------------------------------
    def kill(self) -> None:
        self.killed = True

    def resume(self) -> None:
        self.killed = False

    # ---- preview (sizing + review) ------------------------------------------
    def preview(self, signal: dict, now: datetime | None = None) -> ExecutionPlan:
        now = now or datetime.now(timezone.utc)
        key = (signal.get("strategy", ""), signal.get("symbol", ""))

        if signal.get("action") == "exit":
            if key in self.positions:
                return ExecutionPlan(decision="exit", signal=signal)
            return ExecutionPlan(decision="skip", signal=signal, reason="no open position")

        plan = plan_order(
            signal,
            buying_power=self._buying_power(),
            risk=self.risk,
            account_number=self.account_number,
            fractional_allowed=self.fractional_lookup(signal.get("symbol", "")),
            open_positions=len(self.positions),
            now=now,
        )
        if plan.decision == "place" and plan.order is not None:
            result = self.broker.review_order(plan.order)  # review → captures disclosure
            self.review_detail[signal.get("signal_id", "")] = result.detail
            plan.review = result
        return plan

    # ---- execute -------------------------------------------------------------
    def execute(self, plan: ExecutionPlan, now: datetime | None = None) -> OrderResult | None:
        if self.killed:
            self._record(plan.signal, "HALTED (kill switch)", None)
            return None
        if plan.decision == "skip":
            return None
        if plan.decision == "exit":
            return self._close(plan.signal)
        if plan.decision != "place" or plan.order is None:
            return None

        result = self.broker.place_order(plan.order)
        key = (plan.signal.get("strategy", ""), plan.signal.get("symbol", ""))
        entry = plan.order.limit_price
        if entry is None and plan.est_cost and plan.shares:
            entry = plan.est_cost / plan.shares
        if result.accepted and plan.signal.get("action", "enter") == "enter":
            risk = plan.signal.get("risk") or {}
            self.positions[key] = Position(
                strategy=key[0], symbol=key[1], qty=plan.shares or Decimal(0),
                entry=entry or Decimal(0), stop=_dec(risk.get("stop_loss")),
                target=_dec(risk.get("take_profit")), fractional=plan.fractional,
            )
        self._record(plan.signal, result.detail, result, qty=plan.shares, price=entry)
        return result

    def _close(self, signal: dict) -> OrderResult | None:
        key = (signal.get("strategy", ""), signal.get("symbol", ""))
        pos = self.positions.pop(key, None)
        if pos is None:
            return None
        order = OrderRequest(
            account_number=self.account_number, symbol=pos.symbol, side="sell",
            type="market", quantity=pos.qty, market_hours="regular_hours",
        )
        result = self.broker.place_order(order)
        exit_px = _dec((signal.get("order") or {}).get("limit_price")) or _dec(
            (signal.get("reference") or {}).get("trigger_price")
        ) or pos.target or pos.entry
        if result.accepted:
            self.realized_pnl += (exit_px - pos.entry) * pos.qty
        self._record(signal, f"exit {pos.symbol} @ {exit_px}", result, qty=pos.qty, price=exit_px)
        return result

    # ---- helpers -------------------------------------------------------------
    def _record(self, signal, detail, result, qty=None, price=None) -> None:
        self.blotter.append({
            "signal_id": signal.get("signal_id"),
            "symbol": signal.get("symbol"),
            "strategy": signal.get("strategy"),
            "side": signal.get("side"),
            "qty": str(qty) if qty is not None else "",
            "price": str(price) if price is not None else "",
            "status": "filled" if (result and result.accepted) else ("halted" if result is None else "rejected"),
            "live": bool(result and not result.paper),
            "detail": detail,
        })

    def _buying_power(self) -> Decimal:
        try:
            return self.broker.get_buying_power(self.account_number)
        except NotImplementedError:
            return Decimal(0)
