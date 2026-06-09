"""Alpaca paper-trading broker — a real Broker backed by Alpaca's paper account.

Unlike PaperBroker (an in-app simulator), this places orders against Alpaca's paper trading
engine (https://paper-api.alpaca.markets) for realistic fills — with $0 real money. Used for
the multi-day paper simulation. Credentials come from ALPACA_KEY_ID / ALPACA_SECRET_KEY.
"""

from __future__ import annotations

import os
import time
from decimal import Decimal

import requests

from app.adapters.robinhood_adapter import Broker, OrderRequest, OrderResult

PAPER_BASE = "https://paper-api.alpaca.markets"

# our order types/TIF -> Alpaca's
_TYPE = {"market": "market", "limit": "limit", "stop_market": "stop", "stop_limit": "stop_limit"}
_TIF = {"gfd": "day", "gtc": "gtc"}


class AlpacaPaperBroker(Broker):
    def __init__(self, key_id: str | None = None, secret_key: str | None = None, base: str = PAPER_BASE):
        self.key_id = key_id or os.environ.get("ALPACA_KEY_ID")
        self.secret_key = secret_key or os.environ.get("ALPACA_SECRET_KEY")
        if not self.key_id or not self.secret_key:
            self._load_from_credentials()
        if not self.key_id or not self.secret_key:
            raise RuntimeError("Alpaca keys not set — enter them in Settings")
        self.base = base
        self._bp_cache: Decimal | None = None
        self._bp_ts = 0.0

    def _load_from_credentials(self) -> None:
        """Use the keys the user entered in Settings (stored in the OS keychain)."""
        from app.credentials import get_alpaca_keys

        k, s = get_alpaca_keys()
        self.key_id = self.key_id or k
        self.secret_key = self.secret_key or s

    @property
    def _headers(self) -> dict:
        return {"APCA-API-KEY-ID": self.key_id, "APCA-API-SECRET-KEY": self.secret_key}

    def get_buying_power(self, account_number: str) -> Decimal:
        """Available capital for sizing = CASH (no leverage), cached ~3s to avoid hammering
        Alpaca's rate limit. We deliberately do NOT use the 4x margin 'buying_power'."""
        now = time.monotonic()
        if self._bp_cache is not None and now - self._bp_ts < 3.0:
            return self._bp_cache
        r = requests.get(f"{self.base}/v2/account", headers=self._headers, timeout=20)
        r.raise_for_status()
        acct = r.json()
        val = acct.get("cash") or acct.get("equity") or "0"
        self._bp_cache = Decimal(str(val))
        self._bp_ts = now
        return self._bp_cache

    def review_order(self, order: OrderRequest) -> OrderResult:
        # Alpaca has no pre-trade review endpoint; the order itself returns rejections.
        return OrderResult(
            accepted=True, paper=True, ref_id=order.ref_id,
            detail=f"[alpaca-paper] {order.side} {order.symbol} ({order.type})",
        )

    def to_alpaca(self, order: OrderRequest) -> dict:
        body = {
            "symbol": order.symbol,
            "side": order.side,
            "type": _TYPE.get(order.type, "market"),
            "time_in_force": _TIF.get(order.time_in_force, "day"),
            "client_order_id": order.ref_id,
        }
        if order.dollar_amount is not None:
            body["notional"] = str(order.dollar_amount)
        elif order.quantity is not None:
            body["qty"] = str(order.quantity)
        if order.limit_price is not None:
            body["limit_price"] = str(order.limit_price)
        if order.stop_price is not None:
            body["stop_price"] = str(order.stop_price)

        # Bracket exit legs: auto take-profit + stop-loss. Requires whole-share qty (no
        # notional/fractional) and regular-hours day/gtc — which our entries satisfy.
        if (
            order.quantity is not None
            and order.bracket_take_profit is not None
            and order.bracket_stop_loss is not None
        ):
            body["order_class"] = "bracket"
            body["take_profit"] = {"limit_price": str(order.bracket_take_profit)}
            body["stop_loss"] = {"stop_price": str(order.bracket_stop_loss)}
        elif order.market_hours == "extended_hours":
            body["extended_hours"] = True
        return body

    def account_summary(self) -> dict:
        now = time.monotonic()
        if getattr(self, "_acct_cache", None) is not None and now - self._acct_ts < 3.0:
            return self._acct_cache
        r = requests.get(f"{self.base}/v2/account", headers=self._headers, timeout=20)
        r.raise_for_status()
        a = r.json()
        self._acct_cache = {
            "equity": Decimal(str(a.get("equity", "0"))),
            "last_equity": Decimal(str(a.get("last_equity", "0"))),
            "cash": Decimal(str(a.get("cash", "0"))),
        }
        self._acct_ts = now
        return self._acct_cache

    def close_all_positions(self) -> int:
        """Liquidate all open positions and cancel open orders (paper). Returns HTTP status."""
        r = requests.delete(
            f"{self.base}/v2/positions?cancel_orders=true", headers=self._headers, timeout=30
        )
        return r.status_code

    def open_position_count(self) -> int:
        now = time.monotonic()
        if getattr(self, "_pos_cache", None) is not None and now - self._pos_ts < 3.0:
            return self._pos_cache
        r = requests.get(f"{self.base}/v2/positions", headers=self._headers, timeout=20)
        r.raise_for_status()
        self._pos_cache = len(r.json())
        self._pos_ts = now
        return self._pos_cache

    def place_order(self, order: OrderRequest) -> OrderResult:
        r = requests.post(
            f"{self.base}/v2/orders", headers=self._headers, json=self.to_alpaca(order), timeout=20
        )
        data = r.json() if r.content else {}
        if not r.ok:
            return OrderResult(
                accepted=False, paper=True, ref_id=order.ref_id,
                detail=f"[alpaca-paper] REJECTED {data.get('message', r.text)}", raw=data,
            )
        oid = data.get("id")
        status = data.get("status", "")
        return OrderResult(
            accepted=status not in ("rejected", "canceled"),
            paper=True, ref_id=order.ref_id,
            detail=f"[alpaca-paper] {order.side} {order.symbol} {status} id={oid}", raw=data,
        )
