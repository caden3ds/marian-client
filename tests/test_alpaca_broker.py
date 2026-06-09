"""Offline tests for the Alpaca order mapping — especially bracket (auto-exit) orders."""

import unittest
from decimal import Decimal

from app.adapters.alpaca_broker import AlpacaPaperBroker
from app.adapters.robinhood_adapter import OrderRequest


class AlpacaMapping(unittest.TestCase):
    def setUp(self):
        self.b = AlpacaPaperBroker(key_id="k", secret_key="s")

    def test_bracket_order_includes_exit_legs(self):
        o = OrderRequest(
            account_number="x", symbol="NVDA", side="buy", type="limit",
            quantity=Decimal("10"), limit_price=Decimal("120.00"),
            bracket_take_profit=Decimal("123.00"), bracket_stop_loss=Decimal("118.50"),
        )
        body = self.b.to_alpaca(o)
        self.assertEqual(body["order_class"], "bracket")
        self.assertEqual(body["take_profit"], {"limit_price": "123.00"})
        self.assertEqual(body["stop_loss"], {"stop_price": "118.50"})
        self.assertEqual(body["qty"], "10")
        self.assertEqual(body["time_in_force"], "day")  # gfd -> day

    def test_no_bracket_without_legs(self):
        o = OrderRequest(account_number="x", symbol="NVDA", side="buy", type="limit",
                         quantity=Decimal("10"), limit_price=Decimal("120.00"))
        body = self.b.to_alpaca(o)
        self.assertNotIn("order_class", body)

    def test_fractional_notional_has_no_bracket(self):
        # brackets need whole-share qty; a dollar/notional order must not get bracket legs
        o = OrderRequest(account_number="x", symbol="NVDA", side="buy", type="market",
                         dollar_amount=Decimal("50.00"),
                         bracket_take_profit=Decimal("123"), bracket_stop_loss=Decimal("118"))
        body = self.b.to_alpaca(o)
        self.assertIn("notional", body)
        self.assertNotIn("order_class", body)


if __name__ == "__main__":
    unittest.main()
