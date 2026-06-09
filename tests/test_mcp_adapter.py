"""Chunk 5 — Robinhood MCP adapter mapping tests (offline).

Fixtures are REAL responses captured from the live Robinhood MCP, so the parsers are tested
against ground truth without any network or OAuth.
"""

import unittest
from decimal import Decimal

from app.adapters.robinhood_adapter import (
    LiveBroker,
    OrderRequest,
    find_agentic_account,
    order_to_tool_args,
    parse_buying_power,
    parse_review,
)

# --- real captured responses --------------------------------------------------
ACCOUNTS_JSON = {
    "data": {
        "accounts": [
            {"account_number": "571427681", "agentic_allowed": False, "brokerage_account_type": "individual"},
            {"account_number": "479485476", "agentic_allowed": False, "brokerage_account_type": "ira_roth"},
            {"account_number": "778816868", "nickname": "Agentic", "agentic_allowed": True,
             "brokerage_account_type": "individual"},
        ]
    }
}

PORTFOLIO_JSON = {
    "data": {
        "total_value": "98.3971814056",
        "cash": "20.31",
        "buying_power": {"buying_power": "0.5000", "unleveraged_buying_power": "0.5000",
                         "display_currency": "USD"},
    }
}

REVIEW_JSON = {
    "data": {
        "symbol": "SPY", "side": "buy", "type": "market", "dollar_amount": "1.00",
        "order_checks": {"alertType": "EQUITY_NOT_ENOUGH_BP_DOLLAR_BASED",
                         "equityNotEnoughBpAlertDetails": {"depositAmount": {"amount": "0.5000"}}},
        "quote_data": {"symbol": "SPY", "last_trade_price": "737.430000"},
        "market_data_disclosure": "Bid $735.00 × 80 P · Ask $735.39 × 80 P · Last $735.3639 × 100.",
    }
}


class Parsers(unittest.TestCase):
    def test_find_agentic_account(self):
        self.assertEqual(find_agentic_account(ACCOUNTS_JSON), "778816868")

    def test_parse_buying_power(self):
        self.assertEqual(parse_buying_power(PORTFOLIO_JSON), Decimal("0.5000"))

    def test_parse_review_surfaces_alert_and_disclosure(self):
        r = parse_review("ref-1", REVIEW_JSON)
        self.assertFalse(r.accepted)  # an alert is present
        self.assertTrue(r.paper)  # review never places
        self.assertEqual(r.alert, "EQUITY_NOT_ENOUGH_BP_DOLLAR_BASED")
        self.assertIn("Bid $735.00", r.disclosure)

    def test_parse_review_clean_when_no_alerts(self):
        clean = {"data": {"symbol": "AAPL", "side": "buy", "type": "limit", "order_checks": {}}}
        r = parse_review("ref-2", clean)
        self.assertTrue(r.accepted)
        self.assertIsNone(r.alert)


class OrderArgs(unittest.TestCase):
    def test_limit_order_args_omit_none_and_stringify(self):
        o = OrderRequest(account_number="778816868", symbol="NVDA", side="buy", type="limit",
                         quantity=Decimal("5"), limit_price=Decimal("120.00"))
        args = order_to_tool_args(o)
        self.assertEqual(args["quantity"], "5")
        self.assertEqual(args["limit_price"], "120.00")
        self.assertNotIn("dollar_amount", args)  # None omitted
        self.assertNotIn("stop_price", args)
        self.assertNotIn("ref_id", args)  # review excludes ref by default

    def test_fractional_market_args(self):
        o = OrderRequest(account_number="778816868", symbol="NVDA", side="buy", type="market",
                         dollar_amount=Decimal("62.50"))
        args = order_to_tool_args(o, include_ref=True)
        self.assertEqual(args["dollar_amount"], "62.50")
        self.assertNotIn("quantity", args)
        self.assertIn("ref_id", args)


class LiveBrokerMapping(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def fake_caller(name, args):
            self.calls.append((name, args))
            return {
                "get_accounts": ACCOUNTS_JSON,
                "get_portfolio": PORTFOLIO_JSON,
                "review_equity_order": REVIEW_JSON,
            }[name]

        self.broker = LiveBroker("https://example/mcp", caller=fake_caller)

    def test_get_buying_power(self):
        self.assertEqual(self.broker.get_buying_power("778816868"), Decimal("0.5000"))
        self.assertEqual(self.calls[-1], ("get_portfolio", {"account_number": "778816868"}))

    def test_find_agentic(self):
        self.assertEqual(self.broker.find_agentic_account(), "778816868")

    def test_review_order_paths_through(self):
        o = OrderRequest(account_number="778816868", symbol="SPY", side="buy", type="market",
                         dollar_amount=Decimal("1.00"))
        r = self.broker.review_order(o)
        self.assertEqual(r.alert, "EQUITY_NOT_ENOUGH_BP_DOLLAR_BASED")
        self.assertEqual(self.calls[-1][0], "review_equity_order")

    def test_place_order_calls_mcp_and_is_live(self):
        def caller(name, args):
            self.calls.append((name, args))
            return {"data": {"id": "order-xyz"}}

        broker = LiveBroker("https://example/mcp", caller=caller)
        o = OrderRequest(account_number="778816868", symbol="SPY", side="buy", type="market",
                         dollar_amount=Decimal("1.00"))
        r = broker.place_order(o)
        self.assertTrue(r.accepted)
        self.assertFalse(r.paper)  # real order
        self.assertIn("order-xyz", r.detail)
        self.assertEqual(self.calls[-1][0], "place_equity_order")

    def test_place_order_adds_protective_stop(self):
        calls = []

        def caller(name, args):
            calls.append((name, args))
            return {"data": {"id": "order-1"}}

        broker = LiveBroker("https://example/mcp", caller=caller)
        o = OrderRequest(account_number="A", symbol="NVDA", side="buy", type="limit",
                         quantity=Decimal("3"), limit_price=Decimal("120.00"),
                         bracket_stop_loss=Decimal("118.50"))
        r = broker.place_order(o)
        self.assertTrue(r.accepted)
        placed = [a for (n, a) in calls if n == "place_equity_order"]
        self.assertEqual(len(placed), 2)  # entry + protective stop-market sell
        self.assertEqual(placed[1]["side"], "sell")
        self.assertEqual(placed[1]["type"], "stop_market")
        self.assertEqual(placed[1]["stop_price"], "118.50")
        self.assertIn("+stop", r.detail)


if __name__ == "__main__":
    unittest.main()
