"""Hardspace Finance thin client — entry point.

Flow: resume a stored session, else show the login dialog. On success, attach the session to
the dashboard, which (if subscribed) starts polling today's signal stream. PAPER mode default.
Run:  python main.py
"""

import sys

from PySide6.QtWidgets import QApplication

from app.auth import AuthClient
from app.config import (
    ClientConfig, PAPER, DEMO_MODE, BROKER_ALPACA_PAPER, FIREBASE_API_KEY, FIREBASE_PROJECT_ID,
)
from app.ui.dashboard import Dashboard
from app.ui.login import LoginDialog


def main() -> int:
    app = QApplication(sys.argv)
    auth = AuthClient(FIREBASE_API_KEY, FIREBASE_PROJECT_ID)

    session = auth.resume()
    if session is None:
        dialog = LoginDialog(auth)
        if dialog.exec() != LoginDialog.Accepted:
            return 0
        session = dialog.session

    # Free demo: auto-claim entitlement so the user sees signals without paying.
    if DEMO_MODE and not session.subscription_active:
        try:
            session = auth.grant_demo(session)
        except Exception as e:
            print(f"demo grant failed: {e}")

    def refresher() -> str:
        """Refresh an expired idToken from the stored refresh token."""
        s = auth.resume()
        if not s:
            raise RuntimeError("re-authentication required")
        return s.id_token

    window = Dashboard(
        ClientConfig(mode=PAPER, broker=BROKER_ALPACA_PAPER), project_id=FIREBASE_PROJECT_ID
    )
    window.attach_session(session, refresher=refresher)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
