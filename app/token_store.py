"""Secure storage for the Firebase refresh token (OS keychain via `keyring`).

Falls back to an in-memory store if no keyring backend is available (e.g. headless CI), so
the app still runs — it just won't persist the session across restarts.
"""

from __future__ import annotations

_SERVICE = "hardspace-finance"
_KEY = "refresh_token"

try:
    import keyring

    _HAVE_KEYRING = True
except Exception:  # pragma: no cover
    _HAVE_KEYRING = False

_memory: dict[str, str] = {}


def save_refresh_token(token: str) -> None:
    if _HAVE_KEYRING:
        try:
            keyring.set_password(_SERVICE, _KEY, token)
            return
        except Exception:
            pass
    _memory[_KEY] = token


def load_refresh_token() -> str | None:
    if _HAVE_KEYRING:
        try:
            v = keyring.get_password(_SERVICE, _KEY)
            if v is not None:
                return v
        except Exception:
            pass
    return _memory.get(_KEY)


def clear_refresh_token() -> None:
    _memory.pop(_KEY, None)
    if _HAVE_KEYRING:
        try:
            keyring.delete_password(_SERVICE, _KEY)
        except Exception:
            pass
