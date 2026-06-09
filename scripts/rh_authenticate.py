"""One-time Robinhood MCP authentication for the thin client.

Run this once (it opens your browser to log in to Robinhood and authorize the Agentic
account). Tokens are stored in your OS keychain, so the app won't ask again.

    cd client
    python scripts/rh_authenticate.py

It prints your accounts and the agentic account_number to put in ClientConfig.account_number.
This script performs READ-ONLY calls; it never places an order.
"""

import sys
from pathlib import Path

# Windows consoles default to cp1252; force UTF-8 so non-ASCII output never crashes.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters import mcp_client  # noqa: E402
from app.adapters.robinhood_adapter import find_agentic_account  # noqa: E402
from app.config import ROBINHOOD_MCP_URL  # noqa: E402


def mask(num: str) -> str:
    return "••••" + num[-4:] if num and len(num) >= 4 else num


def main() -> int:
    print(f"Authenticating with {ROBINHOOD_MCP_URL} ...")
    accounts = mcp_client.authenticate(ROBINHOOD_MCP_URL)
    data = accounts.get("data", accounts)
    rows = data.get("accounts", [])
    if not rows:
        print("No accounts returned. Response:", accounts)
        return 1

    print("\nAccounts:")
    for a in rows:
        flag = " [AGENTIC]" if a.get("agentic_allowed") else ""
        nick = f" ({a['nickname']})" if a.get("nickname") else ""
        print(f"  {mask(a.get('account_number',''))}{nick}  {a.get('brokerage_account_type','')}{flag}")

    agentic = find_agentic_account(accounts)
    if agentic:
        print(f"\n[OK] Agentic account: {mask(agentic)}")
        print(f'     Set ClientConfig.account_number = "{agentic}"')
    else:
        print("\n[WARNING] No agentic_allowed account found. Open one in the Robinhood app first.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
