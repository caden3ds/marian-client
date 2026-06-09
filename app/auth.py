"""Firebase Authentication for the thin client (REST-based).

Signs in the subscriber, obtains an idToken, and reads the entitlement flag. The refresh
token is persisted via token_store so the session survives restarts.
"""

from __future__ import annotations

from dataclasses import dataclass

from app import firebase_rest as fb
from app import token_store


@dataclass
class Session:
    uid: str
    id_token: str
    refresh_token: str
    email: str | None = None
    subscription_active: bool = False


class AuthClient:
    def __init__(self, api_key: str, project_id: str):
        self.api_key = api_key
        self.project_id = project_id

    # ---- sign-in paths -------------------------------------------------------
    def sign_in(self, email: str, password: str) -> Session:
        data = fb.sign_in(email, password, self.api_key)
        return self._finish(data["localId"], data["idToken"], data["refreshToken"], email)

    def sign_up(self, email: str, password: str) -> Session:
        data = fb.sign_up(email, password, self.api_key)
        return self._finish(data["localId"], data["idToken"], data["refreshToken"], email)

    def resume(self) -> Session | None:
        """Restore a session from the stored refresh token, if any."""
        rt = token_store.load_refresh_token()
        if not rt:
            return None
        try:
            data = fb.refresh_id_token(rt, self.api_key)
        except fb.AuthError:
            token_store.clear_refresh_token()
            return None
        return self._finish(data["user_id"], data["id_token"], data["refresh_token"], None)

    def sign_out(self) -> None:
        token_store.clear_refresh_token()

    def grant_demo(self, session: Session) -> Session:
        """Free-demo entitlement: call grantDemoAccess, then reflect the new status."""
        fb.call_callable(self.project_id, "grantDemoAccess", session.id_token)
        session.subscription_active = self._read_entitlement(session.uid, session.id_token)
        return session

    # ---- helpers -------------------------------------------------------------
    def _finish(self, uid: str, id_token: str, refresh_token: str, email: str | None) -> Session:
        token_store.save_refresh_token(refresh_token)
        active = self._read_entitlement(uid, id_token)
        return Session(
            uid=uid,
            id_token=id_token,
            refresh_token=refresh_token,
            email=email,
            subscription_active=active,
        )

    def _read_entitlement(self, uid: str, id_token: str) -> bool:
        try:
            doc = fb.get_document(self.project_id, f"users/{uid}", id_token)
        except PermissionError:
            return False
        return bool(doc and doc.get("subscriptionActive") is True)
