"""Robinhood Trading MCP adapter.

The execution boundary. Everything Robinhood-specific lives behind the `Broker` interface,
so the rest of the app never depends on MCP details. Two implementations:

  - PaperBroker  : default. Simulates fills; NEVER sends a real order. (Chunk 5)
  - LiveBroker   : MCP client to https://agent.robinhood.com/mcp/trading. (Chunk 6, gated)

The `OrderRequest` fields below mirror the REAL Robinhood `place_equity_order` tool
(verified against the live MCP): account_number, symbol, side, type, quantity OR
dollar_amount, limit_price, stop_price, time_in_force, market_hours, ref_id.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable


@dataclass
class OrderRequest:
    account_number: str
    symbol: str
    side: str  # 'buy' | 'sell'
    type: str  # 'market' | 'limit' | 'stop_market' | 'stop_limit'
    quantity: Decimal | None = None
    dollar_amount: Decimal | None = None  # market-only
    limit_price: Decimal | None = None  # required for limit/stop_limit
    stop_price: Decimal | None = None  # required for stop_market/stop_limit
    time_in_force: str = "gfd"  # 'gfd' | 'gtc'
    market_hours: str = "regular_hours"
    ref_id: str = field(default_factory=lambda: str(uuid.uuid4()))  # idempotency key
    # Optional bracket exit legs (auto take-profit / stop-loss). Used by brokers that support
    # bracket orders (Alpaca). Ignored by brokers that don't.
    bracket_take_profit: Decimal | None = None
    bracket_stop_loss: Decimal | None = None


@dataclass
class OrderResult:
    accepted: bool
    paper: bool
    ref_id: str
    detail: str
    alert: str | None = None  # broker pre-trade alert code (order_checks.alertType), if any
    disclosure: str | None = None  # market_data_disclosure — MUST be shown verbatim (compliance)
    raw: dict | None = None


# ---- MCP request/response mapping (grounded in the real Robinhood MCP) --------
def _s(x) -> str | None:
    """Decimal/number -> plain string (Robinhood order fields are decimal strings)."""
    if x is None:
        return None
    return format(x, "f") if isinstance(x, Decimal) else str(x)


def order_to_tool_args(order: OrderRequest, *, include_ref: bool = False) -> dict:
    """Build the args dict for review_equity_order / place_equity_order. Omits None fields."""
    args = {
        "account_number": order.account_number,
        "symbol": order.symbol,
        "side": order.side,
        "type": order.type,
        "quantity": _s(order.quantity),
        "dollar_amount": _s(order.dollar_amount),
        "limit_price": _s(order.limit_price),
        "stop_price": _s(order.stop_price),
        "time_in_force": order.time_in_force,
        "market_hours": order.market_hours,
    }
    if include_ref:
        args["ref_id"] = order.ref_id
    return {k: v for k, v in args.items() if v is not None}


def parse_buying_power(portfolio_json: dict) -> Decimal:
    """get_portfolio -> spendable buying power (data.buying_power.buying_power)."""
    d = portfolio_json.get("data", portfolio_json)
    bp = (d.get("buying_power") or {}).get("buying_power")
    return Decimal(str(bp)) if bp is not None else Decimal(0)


def find_agentic_account(accounts_json: dict) -> str | None:
    """get_accounts -> the account_number of the agentic_allowed account, if any."""
    d = accounts_json.get("data", accounts_json)
    for acct in d.get("accounts", []):
        if acct.get("agentic_allowed") is True:
            return acct.get("account_number")
    return None


def parse_review(ref_id: str, review_json: dict) -> "OrderResult":
    """review_equity_order -> OrderResult, surfacing alerts + the compliance disclosure."""
    d = review_json.get("data", review_json)
    checks = d.get("order_checks") or {}
    alert = checks.get("alertType")
    disclosure = d.get("market_data_disclosure")
    detail = f"[live-review] {d.get('side', '')} {d.get('symbol', '')} {d.get('type', '')}".strip()
    if alert:
        detail += f" — ALERT {alert}"
    return OrderResult(
        accepted=alert is None,
        paper=True,  # review never places — it is the paper primitive
        ref_id=ref_id,
        detail=detail,
        alert=alert,
        disclosure=disclosure,
        raw=review_json,
    )


class Broker(ABC):
    """Execution interface. Mirrors the Robinhood MCP equity tool surface."""

    @abstractmethod
    def get_buying_power(self, account_number: str) -> Decimal:
        """Agentic-account cash available — the input to local sizing.
        Backed by the MCP `get_portfolio` tool."""

    @abstractmethod
    def review_order(self, order: OrderRequest) -> OrderResult:
        """Simulate without placing — MCP `review_equity_order`. Safe to call always."""

    @abstractmethod
    def place_order(self, order: OrderRequest) -> OrderResult:
        """Place for real — MCP `place_equity_order`. LiveBroker only."""


class PaperBroker(Broker):
    """Simulation broker. Reviews are logged; places are simulated, never sent.

    Chunk 5 will route `review_order` through the real read-only `review_equity_order`
    tool (which doesn't place anything) for realistic pre-trade alerts, while `place_order`
    stays fully simulated and records a local fill + P&L.
    """

    def __init__(self, starting_cash: Decimal = Decimal("100")):
        self._cash = starting_cash
        self.fills: list[OrderResult] = []

    def get_buying_power(self, account_number: str) -> Decimal:
        return self._cash

    def _notional(self, order: OrderRequest) -> Decimal:
        if order.dollar_amount is not None:
            return order.dollar_amount
        if order.quantity is not None and order.limit_price is not None:
            return order.quantity * order.limit_price
        return Decimal(0)

    def review_order(self, order: OrderRequest) -> OrderResult:
        return OrderResult(
            accepted=True,
            paper=True,
            ref_id=order.ref_id,
            detail=f"[paper-review] {order.side} {order.quantity or order.dollar_amount} {order.symbol} ({order.type})",
        )

    def place_order(self, order: OrderRequest) -> OrderResult:
        notional = self._notional(order)
        # adjust simulated cash so paper buying power behaves realistically
        self._cash += notional if order.side == "sell" else -notional
        result = OrderResult(
            accepted=True,
            paper=True,
            ref_id=order.ref_id,
            detail=f"[paper-fill] {order.side} {order.quantity or order.dollar_amount} {order.symbol} ({order.type})",
        )
        self.fills.append(result)
        return result


class LiveBroker(Broker):
    """Real Robinhood Trading MCP client.

    Chunk 5: LIVE READS (get_accounts, get_portfolio) + PAPER review (review_equity_order,
    which never places). `place_order` stays GATED until Chunk 6 (compliance gate G1–G4).

    `caller(name, args) -> dict` is the MCP transport; defaults to the OAuth MCP client in
    app.adapters.mcp_client, and is injectable so the mapping can be unit-tested offline.
    """

    def __init__(self, mcp_url: str, caller: Callable[[str, dict], dict] | None = None):
        self.mcp_url = mcp_url
        if caller is None:
            from app.adapters import mcp_client

            caller = lambda name, args: mcp_client.call_tool(mcp_url, name, args)  # noqa: E731
        self._call = caller

    def get_accounts(self) -> dict:
        return self._call("get_accounts", {})

    def find_agentic_account(self) -> str | None:
        return find_agentic_account(self.get_accounts())

    def get_buying_power(self, account_number: str) -> Decimal:
        return parse_buying_power(self._call("get_portfolio", {"account_number": account_number}))

    def review_order(self, order: OrderRequest) -> OrderResult:
        data = self._call("review_equity_order", order_to_tool_args(order, include_ref=False))
        return parse_review(order.ref_id, data)

    def place_order(self, order: OrderRequest) -> OrderResult:
        """Place a REAL order via the Robinhood MCP. Callers must gate this behind explicit
        user action (manual approval) or an opt-in auto tier — see ExecutionEngine.

        NOTE: the live place_equity_order *response* shape is parsed defensively (we cannot
        safely place a real order to capture it); confirm against a live fill when you go live.
        """
        data = self._call("place_equity_order", order_to_tool_args(order, include_ref=True))
        d = data.get("data", data) if isinstance(data, dict) else {}
        # error surfaces vary; treat presence of an order id / accepted state as success
        order_id = d.get("id") or d.get("order_id")
        err = d.get("error") or d.get("message") if isinstance(d, dict) else None
        accepted = bool(order_id) or (err is None and bool(d))
        detail = f"[LIVE] {order.side} {order.symbol} ({order.type})"
        if order_id:
            detail += f" id={order_id}"
        if err:
            detail += f" — ERROR {err}"

        # Protective stop: Robinhood's API has no bracket/OCO, so after a buy entry we place a
        # separate stop-market SELL to cap downside. (If the entry doesn't fill, the broker
        # rejects the stop harmlessly — no shares to sell.)
        stop_detail = ""
        if accepted and order.side == "buy" and order.bracket_stop_loss is not None and order.quantity is not None:
            try:
                stop = OrderRequest(
                    account_number=order.account_number, symbol=order.symbol, side="sell",
                    type="stop_market", quantity=order.quantity,
                    stop_price=order.bracket_stop_loss, time_in_force="gtc",
                )
                self._call("place_equity_order", order_to_tool_args(stop, include_ref=True))
                stop_detail = f" +stop@{_s(order.bracket_stop_loss)}"
            except Exception as e:  # never let a stop failure mask a placed entry
                stop_detail = f" (stop failed: {e})"

        return OrderResult(
            accepted=accepted, paper=False, ref_id=order.ref_id, detail=detail + stop_detail, raw=data
        )
