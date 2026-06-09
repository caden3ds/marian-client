"""User-entered credentials, stored securely (OS keychain via secure_store).

Nothing is hard-coded in the app: the user enters their own Alpaca paper keys and connects
their own Robinhood account through the Settings dialog. These helpers are the single source
of truth for those values.
"""

from __future__ import annotations

from app.adapters import secure_store

_ALPACA_KEY = "alpaca:key_id"
_ALPACA_SECRET = "alpaca:secret"
_RH_ACCOUNT = "rh:account_number"


def get_alpaca_keys() -> tuple[str | None, str | None]:
    return secure_store.get_secret(_ALPACA_KEY), secure_store.get_secret(_ALPACA_SECRET)


def set_alpaca_keys(key_id: str, secret: str) -> None:
    secure_store.set_secret(_ALPACA_KEY, key_id.strip())
    secure_store.set_secret(_ALPACA_SECRET, secret.strip())


def alpaca_configured() -> bool:
    k, s = get_alpaca_keys()
    return bool(k and s)


def get_rh_account() -> str | None:
    return secure_store.get_secret(_RH_ACCOUNT)


def set_rh_account(account_number: str) -> None:
    secure_store.set_secret(_RH_ACCOUNT, account_number)


def robinhood_connected() -> bool:
    from app.adapters import mcp_client

    return mcp_client.is_authenticated() and bool(get_rh_account())
