"""Regression tests for secure_store — the keyring + encrypted-file fallback that fixes the
Windows Credential Manager ~1.3KB blob limit (which silently dropped OAuth tokens)."""

import unittest
import uuid

from app.adapters import secure_store as ss


class SecureStore(unittest.TestCase):
    def test_small_value_roundtrip(self):
        key = f"test:{uuid.uuid4().hex}"
        try:
            ss.set_secret(key, "small-value")
            self.assertEqual(ss.get_secret(key), "small-value")
        finally:
            ss.delete_secret(key)
        self.assertIsNone(ss.get_secret(key))

    def test_large_blob_roundtrip(self):
        # 4 KB exceeds the WinVault limit -> must go through the encrypted file path.
        key = f"test:{uuid.uuid4().hex}"
        val = "Z" * 4000
        try:
            ss.set_secret(key, val)
            self.assertEqual(ss.get_secret(key), val)
        finally:
            ss.delete_secret(key)
        self.assertIsNone(ss.get_secret(key))

    def test_missing_key_returns_none(self):
        self.assertIsNone(ss.get_secret(f"test:{uuid.uuid4().hex}"))


if __name__ == "__main__":
    unittest.main()
