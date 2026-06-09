"""Local client configuration.

Two kinds of config live here, kept deliberately separate:

1. Connection config (cloud endpoints, Firebase project) — same for everyone.
2. The user's LOCAL risk budget and strategy toggles — these never leave the machine and
   are what make sizing personal *on the client* while the cloud stays impersonal.
"""

from dataclasses import dataclass, field
from decimal import Decimal


# ---- Connection (shared) ------------------------------------------------------
FIREBASE_PROJECT_ID = "hardspace-finance"
FIREBASE_API_KEY = "AIzaSyDwJwHQTC0YVpWOUgj_HlwP3LxH46kUjc4"  # Web API key (public; safe to ship)
ROBINHOOD_MCP_URL = "https://agent.robinhood.com/mcp/trading"

# Free-demo mode: auto-grant entitlement on login (no payment). OFF for production —
# access now requires a real Stripe subscription.
DEMO_MODE = False

# ---- Broker selection ---------------------------------------------------------
BROKER_SIM = "sim"  # in-app instant-fill simulator (PaperBroker)
BROKER_ALPACA_PAPER = "alpaca_paper"  # Alpaca paper-trading engine (realistic, no real money)
BROKER_RH_LIVE = "robinhood_live"  # Robinhood Agentic account (REAL money)


# ---- Trading mode -------------------------------------------------------------
PAPER = "paper"  # simulated fills, never touches the broker
LIVE = "live"  # real place_equity_order

# ---- Execution tiers (graduated discretion) -----------------------------------
MANUAL = "manual"  # every order requires explicit approval (no discretion)
DELAYED = "delayed"  # auto-executes after a cancel window unless the user vetoes
AUTO = "auto"  # fires immediately (full-auto)


def default_tier(mode: str) -> str:
    """PAPER defaults to AUTO (no real orders → no discretion risk). LIVE defaults to MANUAL
    (the protected-side posture per the compliance notes; auto tiers are an explicit opt-in)."""
    return AUTO if mode == PAPER else MANUAL


@dataclass
class RiskConfig:
    """The user's LOCAL risk budget. Never transmitted to the cloud."""

    risk_pct_per_trade: float = 0.01  # fraction of cash risked per trade
    max_position_pct: float = 0.05  # cap any single position at 5% of cash (diversify)
    max_concurrent_positions: int = 5
    # Small-account fallback: when a whole-share limit order rounds to 0 shares, optionally
    # place a fractional MARKET order sized by dollar notional instead. This trades the
    # limit-price protection for the ability to participate (the "$50 sandbox" path).
    small_account_market_fallback: bool = False


@dataclass
class ClientConfig:
    mode: str = PAPER
    broker: str = BROKER_SIM  # which execution backend to use
    execution_tier: str | None = None  # None → default_tier(mode)
    delayed_seconds: int = 15  # cancel window for the DELAYED tier
    paper_balance: Decimal = Decimal("2500")  # simulated agentic-account cash (paper mode)

    def tier(self) -> str:
        return self.execution_tier or default_tier(self.mode)
    # Defaults reflect the COST-AWARE backtest (net of ~3bps round-trip):
    #   orb        +0.28R  (robust)      → ON
    #   vwap_breakout +0.12R (thin)      → ON
    #   gap_and_go  rare/untested        → ON (harmless; fires only on >2% gaps)
    #   ema_cross  −0.56R net (tiny stop → costs dominate) → OFF
    #   mean_reversion −0.19R net        → OFF
    enabled_strategies: set[str] = field(
        default_factory=lambda: {
            "gap_and_go",
            "orb",
            "vwap_breakout",
        }
    )
    risk: RiskConfig = field(default_factory=RiskConfig)
    account_number: str | None = None  # set after connecting Robinhood (Settings)
