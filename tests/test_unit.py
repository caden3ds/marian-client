"""Network-free unit tests: Firestore value conversion + SignalStream dedupe."""

import unittest

from app import firebase_rest as fb
from app import signals as signals_mod
from app.signals import SignalStream


class ValueConversion(unittest.TestCase):
    def test_round_trip(self):
        d = {
            "s": "x",
            "i": 3,
            "b": True,
            "n": None,
            "f": 1.5,
            "m": {"k": "v"},
            "arr": ["p", "q"],
        }
        encoded = {k: fb.to_value(v) for k, v in d.items()}
        decoded = {k: fb.from_value(v) for k, v in encoded.items()}
        self.assertEqual(decoded, d)

    def test_bool_not_treated_as_int(self):
        self.assertEqual(fb.to_value(True), {"booleanValue": True})

    def test_doc_to_dict_nested(self):
        doc = {
            "name": "projects/p/databases/(default)/documents/signals/d/live/x",
            "fields": {
                "symbol": {"stringValue": "NVDA"},
                "order": {"mapValue": {"fields": {"type": {"stringValue": "limit"}}}},
            },
        }
        self.assertEqual(fb.doc_to_dict(doc), {"symbol": "NVDA", "order": {"type": "limit"}})


class StreamDedupe(unittest.TestCase):
    def test_dedupes_by_signal_id(self):
        rows = [
            ("d1", {"signal_id": "a", "strategy": "orb", "symbol": "X"}),
            ("d2", {"signal_id": "b", "strategy": "vwap_breakout", "symbol": "Y"}),
        ]
        original = signals_mod.fb.query_signals_after
        signals_mod.fb.query_signals_after = lambda project, day, after, token: rows
        try:
            stream = SignalStream(_FakeSession(), "proj", day="2026-06-05", since="2000-01-01T00:00:00Z")
            first = stream.poll()
            self.assertEqual(len(first), 2)
            self.assertEqual(stream.poll(), [])  # nothing new on second poll
        finally:
            signals_mod.fb.query_signals_after = original


class StreamTokenRefresh(unittest.TestCase):
    def test_refreshes_expired_token_and_retries(self):
        calls = {"n": 0}
        rows = [("d1", {"signal_id": "a", "strategy": "orb", "symbol": "X"})]

        def flaky(project, day, after, token):
            calls["n"] += 1
            if token == "stale":
                raise fb.AuthError("token expired")
            return rows

        original = signals_mod.fb.query_signals_after
        signals_mod.fb.query_signals_after = flaky
        try:
            sess = _FakeSession()
            sess.id_token = "stale"
            stream = SignalStream(
                sess, "proj", day="2026-06-05", refresher=lambda: "fresh", since="2000-01-01T00:00:00Z"
            )
            out = stream.poll()
            self.assertEqual(len(out), 1)
            self.assertEqual(sess.id_token, "fresh")  # token was refreshed
            self.assertEqual(calls["n"], 2)  # failed once, retried once
        finally:
            signals_mod.fb.query_signals_after = original

    def test_raises_when_no_refresher(self):
        def expired(project, day, after, token):
            raise fb.AuthError("token expired")

        original = signals_mod.fb.query_signals_after
        signals_mod.fb.query_signals_after = expired
        try:
            stream = SignalStream(_FakeSession(), "proj", day="2026-06-05")
            with self.assertRaises(fb.AuthError):
                stream.poll()
        finally:
            signals_mod.fb.query_signals_after = original


class _FakeSession:
    id_token = "fake-token"


if __name__ == "__main__":
    unittest.main()
