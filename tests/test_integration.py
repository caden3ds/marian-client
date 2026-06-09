"""Emulator-backed integration tests for the client auth + signal-read path.

Runs only when the Firebase emulators are active (env vars set by `emulators:exec`):
  cd client && npx -y firebase-tools@latest emulators:exec --only auth,firestore \
      --config ../cloud/firebase.json --project hardspace-finance \
      "python -m unittest discover -s tests -p 'test_*.py'"

Verifies: a SUBSCRIBED user can sign in and read today's signals; an UNSUBSCRIBED user is
denied (security rules enforced); and dedupe holds across polls.
"""

import os
import unittest
import uuid

from app.auth import AuthClient
from app.signals import SignalStream
from app import firebase_rest as fb

PROJECT = "hardspace-finance"
API_KEY = "demo-key"  # the Auth emulator accepts any key
DAY = "2026-06-05"


def _valid_signal(symbol="NVDA"):
    sid = str(uuid.uuid4())
    return {
        "schema_version": "1.0.0",
        "signal_id": sid,
        "issued_at": "2026-06-05T14:00:00Z",
        "trading_day": DAY,
        "phase": "opening_chaos",
        "strategy": "orb",
        "symbol": symbol,
        "side": "buy",
        "action": "enter",
        "order": {
            "type": "limit",
            "limit_price": "120.00",
            "stop_price": None,
            "time_in_force": "gfd",
            "market_hours": "regular_hours",
        },
        "risk": {"stop_loss": "118.50", "take_profit": "123.00"},
        "reference": {"trigger_price": "120.00", "previous_close": "117.80"},
        "valid_until": "2026-06-05T14:05:00Z",
        "note": "integration-test signal",
    }


@unittest.skipUnless(
    os.environ.get("FIRESTORE_EMULATOR_HOST") and os.environ.get("FIREBASE_AUTH_EMULATOR_HOST"),
    "requires Firebase emulators",
)
class AuthAndSignalFlow(unittest.TestCase):
    def test_subscribed_user_reads_signals(self):
        auth = AuthClient(API_KEY, PROJECT)
        email = f"sub_{uuid.uuid4().hex[:8]}@example.com"
        s = auth.sign_up(email, "password123")
        self.assertFalse(s.subscription_active)  # not subscribed at creation

        # Grant subscription + seed a signal via the emulator admin bypass ('owner').
        fb.set_document(PROJECT, f"users/{s.uid}", {"subscriptionActive": True})
        sig = _valid_signal("NVDA")
        fb.set_document(PROJECT, f"signals/{DAY}/live/{sig['signal_id']}", sig)

        s2 = auth.sign_in(email, "password123")
        self.assertTrue(s2.subscription_active)

        stream = SignalStream(s2, PROJECT, day=DAY, since="2026-06-05T00:00:00Z")
        fresh = stream.poll()
        self.assertIn("NVDA", [x["symbol"] for x in fresh])
        got = next(x for x in fresh if x["symbol"] == "NVDA")
        self.assertEqual(got["order"]["limit_price"], "120.00")  # nested map decoded
        self.assertEqual(stream.poll(), [])  # dedupe across polls

    def test_unsubscribed_user_denied(self):
        auth = AuthClient(API_KEY, PROJECT)
        email = f"free_{uuid.uuid4().hex[:8]}@example.com"
        s = auth.sign_up(email, "password123")
        self.assertFalse(s.subscription_active)

        stream = SignalStream(s, PROJECT, day=DAY)
        with self.assertRaises(PermissionError):
            stream.poll()


if __name__ == "__main__":
    unittest.main()
