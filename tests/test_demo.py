"""Free-demo end-to-end (emulator-backed): sign up → grantDemoAccess → read signals.

Runs only under the Firebase emulators (auth + firestore + functions), e.g.:
  cd client && npx -y firebase-tools@latest emulators:exec --only auth,firestore,functions \
      --config ../cloud/firebase.json --project hardspace-finance \
      "python -m unittest tests.test_demo"

Proves a brand-new user, with no payment, can claim demo access and then read today's signals.
"""

import os
import unittest
import uuid

from app.auth import AuthClient
from app.signals import SignalStream, eastern_day
from app import firebase_rest as fb

PROJECT = "hardspace-finance"
API_KEY = "demo-key"


def _signal(symbol="NVDA"):
    return {
        "schema_version": "1.0.0",
        "signal_id": str(uuid.uuid4()),
        "issued_at": "2026-06-05T14:00:00Z",
        "trading_day": eastern_day(),
        "phase": "opening_chaos",
        "strategy": "orb",
        "symbol": symbol,
        "side": "buy",
        "action": "enter",
        "order": {"type": "limit", "limit_price": "120.00", "stop_price": None,
                   "time_in_force": "gfd", "market_hours": "regular_hours"},
        "risk": {"stop_loss": "118.50", "take_profit": "123.00"},
        "reference": {"trigger_price": "120.00", "previous_close": "117.80"},
        "valid_until": "2099-01-01T00:00:00Z",
        "note": "demo signal",
    }


@unittest.skipUnless(
    os.environ.get("FIRESTORE_EMULATOR_HOST") and os.environ.get("FIREBASE_AUTH_EMULATOR_HOST"),
    "requires Firebase emulators (auth+firestore+functions)",
)
class FreeDemoFlow(unittest.TestCase):
    def test_new_user_claims_demo_and_reads_signals(self):
        auth = AuthClient(API_KEY, PROJECT)
        email = f"demo_{uuid.uuid4().hex[:8]}@example.com"
        s = auth.sign_up(email, "password123")
        self.assertFalse(s.subscription_active)  # not entitled yet

        # Before granting, reads are denied by the rules.
        stream = SignalStream(s, PROJECT, day=eastern_day(), since="2026-06-05T00:00:00Z")
        with self.assertRaises(PermissionError):
            stream.poll()

        # Claim the free demo (calls grantDemoAccess on the functions emulator).
        s = auth.grant_demo(s)
        self.assertTrue(s.subscription_active)

        # Seed a signal for today and confirm the now-entitled user reads it.
        sig = _signal("NVDA")
        fb.set_document(PROJECT, f"signals/{eastern_day()}/live/{sig['signal_id']}", sig)
        fresh = stream.poll()
        self.assertIn("NVDA", [x["symbol"] for x in fresh])


if __name__ == "__main__":
    unittest.main()
