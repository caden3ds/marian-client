"""Thin wrappers over the Firebase Auth + Firestore REST APIs.

Emulator-aware: if FIREBASE_AUTH_EMULATOR_HOST / FIRESTORE_EMULATOR_HOST are set, requests
go to the local emulators (used by the integration tests); otherwise to production.

Firestore's REST API encodes every field as a typed value (e.g. {"stringValue": "x"}). The
converters here translate to/from plain Python dicts so the rest of the app never sees that.
"""

from __future__ import annotations

import os
from typing import Any

import requests


# ---- base URLs (emulator-aware) ----------------------------------------------
def _auth_host() -> str | None:
    return os.environ.get("FIREBASE_AUTH_EMULATOR_HOST")


def identity_base() -> str:
    h = _auth_host()
    return f"http://{h}/identitytoolkit.googleapis.com/v1" if h else "https://identitytoolkit.googleapis.com/v1"


def securetoken_base() -> str:
    h = _auth_host()
    return f"http://{h}/securetoken.googleapis.com/v1" if h else "https://securetoken.googleapis.com/v1"


def firestore_base() -> str:
    h = os.environ.get("FIRESTORE_EMULATOR_HOST")
    return f"http://{h}/v1" if h else "https://firestore.googleapis.com/v1"


def functions_base(project: str) -> str:
    region = os.environ.get("FUNCTIONS_REGION", "us-central1")
    # Use the emulator when running locally (same env that turns on the Firestore emulator).
    if os.environ.get("FIRESTORE_EMULATOR_HOST"):
        host = os.environ.get("FUNCTIONS_EMULATOR_HOST", "127.0.0.1:5001")
        return f"http://{host}/{project}/{region}"
    return f"https://{region}-{project}.cloudfunctions.net"


def call_callable(project: str, name: str, id_token: str, data: dict | None = None) -> dict | None:
    """Invoke a Firebase callable function (onCall) over its HTTPS endpoint."""
    r = requests.post(
        f"{functions_base(project)}/{name}",
        headers={"Authorization": f"Bearer {id_token}", "Content-Type": "application/json"},
        json={"data": data or {}},
        timeout=20,
    )
    r.raise_for_status()
    return r.json().get("result")


class AuthError(Exception):
    pass


# ---- Auth --------------------------------------------------------------------
def _identity_call(path: str, api_key: str, payload: dict) -> dict:
    r = requests.post(f"{identity_base()}/{path}?key={api_key}", json=payload, timeout=20)
    if not r.ok:
        msg = r.json().get("error", {}).get("message", r.text) if r.content else r.reason
        raise AuthError(msg)
    return r.json()


def sign_in(email: str, password: str, api_key: str) -> dict:
    return _identity_call(
        "accounts:signInWithPassword", api_key,
        {"email": email, "password": password, "returnSecureToken": True},
    )


def sign_up(email: str, password: str, api_key: str) -> dict:
    return _identity_call(
        "accounts:signUp", api_key,
        {"email": email, "password": password, "returnSecureToken": True},
    )


def refresh_id_token(refresh_token: str, api_key: str) -> dict:
    r = requests.post(
        f"{securetoken_base()}/token?key={api_key}",
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=20,
    )
    if not r.ok:
        raise AuthError(r.text)
    return r.json()  # snake_case: id_token, refresh_token, user_id, expires_in


# ---- Firestore value conversion ----------------------------------------------
def from_value(v: dict) -> Any:
    if "nullValue" in v:
        return None
    if "stringValue" in v:
        return v["stringValue"]
    if "booleanValue" in v:
        return v["booleanValue"]
    if "integerValue" in v:
        return int(v["integerValue"])
    if "doubleValue" in v:
        return float(v["doubleValue"])
    if "timestampValue" in v:
        return v["timestampValue"]
    if "mapValue" in v:
        return {k: from_value(x) for k, x in v["mapValue"].get("fields", {}).items()}
    if "arrayValue" in v:
        return [from_value(x) for x in v["arrayValue"].get("values", [])]
    return next(iter(v.values()), None)  # geoPoint/reference/bytes: pass through raw


def to_value(x: Any) -> dict:
    if x is None:
        return {"nullValue": None}
    if isinstance(x, bool):
        return {"booleanValue": x}
    if isinstance(x, int):
        return {"integerValue": str(x)}
    if isinstance(x, float):
        return {"doubleValue": x}
    if isinstance(x, str):
        return {"stringValue": x}
    if isinstance(x, dict):
        return {"mapValue": {"fields": {k: to_value(v) for k, v in x.items()}}}
    if isinstance(x, (list, tuple)):
        return {"arrayValue": {"values": [to_value(v) for v in x]}}
    raise TypeError(f"unsupported Firestore value: {type(x)}")


def doc_to_dict(doc: dict) -> dict:
    return {k: from_value(v) for k, v in doc.get("fields", {}).items()}


# ---- Firestore reads ---------------------------------------------------------
def _docs_url(project: str, path: str) -> str:
    return f"{firestore_base()}/projects/{project}/databases/(default)/documents/{path}"


def get_document(project: str, path: str, id_token: str) -> dict | None:
    r = requests.get(
        _docs_url(project, path),
        headers={"Authorization": f"Bearer {id_token}"},
        timeout=20,
    )
    if r.status_code == 404:
        return None
    if r.status_code == 401:
        raise AuthError("token expired")
    if r.status_code == 403:
        raise PermissionError(f"denied reading {path}")
    r.raise_for_status()
    return doc_to_dict(r.json())


def list_documents(project: str, collection_path: str, id_token: str) -> list[tuple[str, dict]]:
    """Returns [(doc_id, fields_dict), ...] for a collection. Empty list if none."""
    r = requests.get(
        _docs_url(project, collection_path),
        headers={"Authorization": f"Bearer {id_token}"},
        timeout=20,
    )
    if r.status_code == 401:
        raise AuthError("token expired")
    if r.status_code == 403:
        raise PermissionError(f"denied listing {collection_path}")
    r.raise_for_status()
    out = []
    for doc in r.json().get("documents", []):
        doc_id = doc["name"].rsplit("/", 1)[-1]
        out.append((doc_id, doc_to_dict(doc)))
    return out


def query_signals_after(project: str, day: str, after_iso: str, id_token: str) -> list[tuple[str, dict]]:
    """Return only signals in signals/{day}/live with issued_at > after_iso (cheap incremental
    poll — avoids re-reading the whole collection each tick). Sorted ascending by issued_at."""
    parent = f"signals/{day}"
    url = f"{firestore_base()}/projects/{project}/databases/(default)/documents/{parent}:runQuery"
    body = {
        "structuredQuery": {
            "from": [{"collectionId": "live"}],
            "where": {
                "fieldFilter": {
                    "field": {"fieldPath": "issued_at"},
                    "op": "GREATER_THAN",
                    "value": {"stringValue": after_iso},
                }
            },
            "orderBy": [{"field": {"fieldPath": "issued_at"}, "direction": "ASCENDING"}],
        }
    }
    r = requests.post(url, headers={"Authorization": f"Bearer {id_token}"}, json=body, timeout=20)
    if r.status_code == 401:
        raise AuthError("token expired")
    if r.status_code == 403:
        raise PermissionError(f"denied querying {parent}")
    r.raise_for_status()
    out = []
    for row in r.json():
        doc = row.get("document")
        if not doc:
            continue  # readTime-only rows when empty
        out.append((doc["name"].rsplit("/", 1)[-1], doc_to_dict(doc)))
    return out


def set_document(project: str, path: str, fields: dict, id_token: str = "owner") -> None:
    """Write a document. Defaults to the emulator's admin bypass token ('owner').
    Used only by tests/seeding — production writes happen via the Admin SDK server-side."""
    r = requests.patch(
        _docs_url(project, path),
        headers={"Authorization": f"Bearer {id_token}"},
        json={"fields": {k: to_value(v) for k, v in fields.items()}},
        timeout=20,
    )
    r.raise_for_status()
