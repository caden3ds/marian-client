# Hardspace Finance — Desktop Client

The open-source execution client for Hardspace Finance. It signs you in, streams the
**impersonal trading signals** published by the Hardspace cloud, and executes them through
**your own brokerage account** — either **Alpaca paper** (simulated, no real money) or
**Robinhood** (live). You enter your own broker credentials in-app; nothing is bundled in.

> ⚠️ **Not investment advice.** Signals are generic and identical for all subscribers; you are
> responsible for every trade. Paper trading by default. Trading involves risk of loss.

## What it does
- Firebase sign-in; reads today's signals from the cloud (subscription-gated).
- Local position sizing (cash-based, % risk caps) — the cloud never sees your balance.
- Execution tiers: **manual approval**, delayed, or full-auto.
- **Alpaca paper** with bracket exits (auto take-profit/stop-loss); **Robinhood live** with
  manual approval + a protective stop. Kill switch, order blotter, P&L.
- Credentials stored in the OS keychain (entered via ⚙ Settings) — never in code or on disk in
  plaintext.

## Run from source
```bash
python -m venv .venv && .venv/Scripts/activate   # (Windows)  or  source .venv/bin/activate
pip install -r requirements.txt
python main.py
```
On first launch open **⚙ Settings** to enter your Alpaca paper keys and/or connect Robinhood.

## Build a standalone .exe
```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
# → dist/HardspaceFinance.exe  (single file, no Python needed)
```

## Tests
```bash
python -m unittest discover -s tests
```

## License
MIT — see [LICENSE](LICENSE).
