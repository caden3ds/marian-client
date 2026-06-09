"""Chunk 4 — execution planner + engine (network-free, deterministic)."""

import unittest
from datetime import datetime, timezone
from decimal import Decimal

from app.config import RiskConfig
from app.adapters.robinhood_adapter import PaperBroker
from app.execution.planner import plan_order
from app.execution.engine import ExecutionEngine


def signal(symbol="NVDA", limit="120.00", stop="118.50", strategy="orb",
           valid_until="2999-01-01T00:00:00Z", action="enter", mhours="regular_hours"):
    return {
        "signal_id": f"{strategy}-{symbol}",
        "trading_day": "2026-06-05",
        "phase": "opening_chaos",
        "strategy": strategy,
        "symbol": symbol,
        "side": "buy",
        "action": action,
        "order": {
            "type": "limit", "limit_price": limit, "stop_price": None,
            "time_in_force": "gfd", "market_hours": mhours,
        },
        "risk": {"stop_loss": stop, "take_profit": "123.00"},
        "reference": {"trigger_price": limit, "previous_close": "117.80"},
        "valid_until": valid_until,
        "note": "test",
    }


class Planner(unittest.TestCase):
    def test_big_account_places_whole_share_limit(self):
        # pin sizing params so the assertion is independent of the global default
        p = plan_order(signal(), buying_power=Decimal("2500"),
                       risk=RiskConfig(max_position_pct=0.25, risk_pct_per_trade=0.02),
                       account_number="A")
        self.assertEqual(p.decision, "place")
        self.assertEqual(p.order.type, "limit")
        self.assertEqual(p.order.quantity, Decimal("5"))
        self.assertEqual(p.order.limit_price, Decimal("120.00"))
        self.assertFalse(p.fractional)

    def test_two_accounts_same_signal_different_shares(self):
        r = RiskConfig(max_position_pct=0.25, risk_pct_per_trade=0.02)
        big = plan_order(signal(), buying_power=Decimal("2500"), risk=r, account_number="A")
        # $900 account, allow fewer shares but still >=1
        small = plan_order(signal(), buying_power=Decimal("900"), risk=r, account_number="B")
        self.assertEqual(big.decision, "place")
        self.assertEqual(small.decision, "place")
        self.assertGreater(big.order.quantity, small.order.quantity)

    def test_small_account_skips_without_fallback(self):
        p = plan_order(signal(), buying_power=Decimal("250"), risk=RiskConfig(), account_number="A")
        self.assertEqual(p.decision, "skip")
        self.assertIn("1 share", p.reason)

    def test_small_account_fractional_fallback(self):
        risk = RiskConfig(small_account_market_fallback=True, max_position_pct=0.25)
        p = plan_order(signal(), buying_power=Decimal("250"), risk=risk, account_number="A")
        self.assertEqual(p.decision, "place")
        self.assertTrue(p.fractional)
        self.assertEqual(p.order.type, "market")  # fractional requires market
        self.assertIsNone(p.order.quantity)
        self.assertEqual(p.order.dollar_amount, Decimal("62.50"))  # 25% of 250

    def test_fallback_blocked_when_symbol_not_fractionable(self):
        risk = RiskConfig(small_account_market_fallback=True)
        p = plan_order(signal(), buying_power=Decimal("250"), risk=risk, account_number="A",
                       fractional_allowed=False)
        self.assertEqual(p.decision, "skip")

    def test_expired_signal_skipped(self):
        p = plan_order(signal(valid_until="2020-01-01T00:00:00Z"),
                       buying_power=Decimal("2500"), risk=RiskConfig(), account_number="A",
                       now=datetime(2026, 6, 5, tzinfo=timezone.utc))
        self.assertEqual(p.decision, "skip")
        self.assertEqual(p.reason, "expired")

    def test_concurrency_cap(self):
        p = plan_order(signal(), buying_power=Decimal("2500"), risk=RiskConfig(),
                       account_number="A", open_positions=5)
        self.assertEqual(p.decision, "skip")
        self.assertIn("position cap", p.reason)


class Engine(unittest.TestCase):
    def _eng(self, cash="100000", **risk):
        return ExecutionEngine(PaperBroker(Decimal(cash)), RiskConfig(**risk), "PAPER")

    def test_execute_opens_position_and_blotter(self):
        eng = self._eng()
        res = eng.execute(eng.preview(signal()))
        self.assertTrue(res.accepted)
        self.assertEqual(len(eng.positions), 1)
        self.assertEqual(eng.blotter[-1]["status"], "filled")

    def test_cap_enforced_after_executions(self):
        eng = self._eng(max_concurrent_positions=2)
        eng.execute(eng.preview(signal(symbol="AAA")))
        eng.execute(eng.preview(signal(symbol="BBB")))
        plan3 = eng.preview(signal(symbol="CCC"))
        self.assertEqual(plan3.decision, "skip")
        self.assertIn("position cap", plan3.reason)

    def test_exit_realizes_pnl_and_frees_slot(self):
        eng = self._eng(max_concurrent_positions=1)
        eng.execute(eng.preview(signal(symbol="AAA", limit="100.00", stop="99.00")))
        self.assertEqual(len(eng.positions), 1)
        eng.execute(eng.preview(signal(symbol="AAA", limit="105.00", action="exit")))
        self.assertEqual(len(eng.positions), 0)
        self.assertGreater(eng.realized_pnl, 0)  # sold at 105 > entry 100

    def test_kill_switch_blocks_execution(self):
        eng = self._eng()
        eng.kill()
        res = eng.execute(eng.preview(signal()))
        self.assertIsNone(res)
        self.assertEqual(len(eng.positions), 0)
        self.assertEqual(eng.blotter[-1]["status"], "halted")

    def test_paper_review_recorded(self):
        eng = ExecutionEngine(PaperBroker(Decimal("2500")), RiskConfig(), "PAPER")
        s = signal()
        eng.preview(s)
        self.assertIn(s["signal_id"], eng.review_detail)
        self.assertIn("paper-review", eng.review_detail[s["signal_id"]])


class LivePlacement(unittest.TestCase):
    def test_live_place_goes_through_mcp_and_marks_live(self):
        from app.adapters.robinhood_adapter import LiveBroker

        calls = []

        def caller(name, args):
            calls.append(name)
            return {
                "get_portfolio": {"data": {"buying_power": {"buying_power": "100000"}}},
                "review_equity_order": {"data": {"order_checks": {}}},
                "place_equity_order": {"data": {"id": "order-123"}},
            }[name]

        eng = ExecutionEngine(LiveBroker("https://x/mcp", caller=caller), RiskConfig(), "778816868")
        res = eng.execute(eng.preview(signal()))
        self.assertTrue(res.accepted)
        self.assertFalse(res.paper)  # real order
        self.assertIn("place_equity_order", calls)
        self.assertTrue(eng.blotter[-1]["live"])


if __name__ == "__main__":
    unittest.main()
