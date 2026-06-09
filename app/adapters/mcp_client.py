"""MCP client for the Robinhood Trading MCP (Streamable HTTP + OAuth 2.0 / PKCE).

Responsibilities:
  - per-user OAuth via the system browser + a localhost redirect catcher
  - token persistence in the OS keychain (so auth happens once)
  - a synchronous `call_tool(url, name, args) -> dict` facade over the async MCP SDK

NOTE: the live OAuth handshake requires a human in a browser and the user's real Robinhood
login, so it cannot be exercised in CI. The request/response MAPPING is unit-tested offline
(see tests/test_mcp_adapter.py); this module is the transport that carries it.
"""

from __future__ import annotations

import asyncio
import json
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

from app.adapters import secure_store

CALLBACK_PORT = 41994
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/callback"


class KeyringTokenStorage(TokenStorage):
    """Persists OAuth tokens + client registration via secure_store (keychain w/ encrypted
    file fallback for blobs too large for the OS vault)."""

    def __init__(self, account: str = "default"):
        self._t = f"{account}:tokens"
        self._c = f"{account}:client"

    async def get_tokens(self) -> OAuthToken | None:
        v = secure_store.get_secret(self._t)
        return OAuthToken.model_validate_json(v) if v else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        secure_store.set_secret(self._t, tokens.model_dump_json())

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        v = secure_store.get_secret(self._c)
        return OAuthClientInformationFull.model_validate_json(v) if v else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        secure_store.set_secret(self._c, client_info.model_dump_json())


def is_authenticated(account: str = "default") -> bool:
    """True if we already hold tokens (no browser needed)."""
    return secure_store.get_secret(f"{account}:tokens") is not None


def _client_metadata() -> OAuthClientMetadata:
    return OAuthClientMetadata(
        client_name="Hardspace Finance",
        redirect_uris=[REDIRECT_URI],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",  # public client; security via PKCE
    )


async def _redirect_handler(authorization_url: str) -> None:
    print(f"\nOpening browser to authorize Robinhood...\nIf it doesn't open, visit:\n{authorization_url}\n")
    webbrowser.open(authorization_url)


def _wait_for_callback(timeout: int = 300) -> tuple[str | None, str | None]:
    captured: dict[str, str | None] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = parse_qs(urlparse(self.path).query)
            captured["code"] = q.get("code", [None])[0]
            captured["state"] = q.get("state", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body style='font-family:sans-serif'>"
                b"<h2>Hardspace Finance</h2><p>Authentication complete. You can close this tab.</p>"
                b"</body></html>"
            )

        def log_message(self, *_):  # silence
            return

    server = HTTPServer(("localhost", CALLBACK_PORT), Handler)
    server.timeout = timeout
    server.handle_request()  # blocks until the single redirect request arrives
    server.server_close()
    return captured.get("code"), captured.get("state")


async def _callback_handler() -> tuple[str, str | None]:
    code, state = await asyncio.to_thread(_wait_for_callback)
    if not code:
        raise RuntimeError("OAuth callback did not return an authorization code")
    return code, state


def _provider(server_url: str, account: str) -> OAuthClientProvider:
    return OAuthClientProvider(
        server_url=server_url,
        client_metadata=_client_metadata(),
        storage=KeyringTokenStorage(account),
        redirect_handler=_redirect_handler,
        callback_handler=_callback_handler,
    )


def _extract_json(result) -> dict:
    sc = getattr(result, "structuredContent", None)
    if sc:
        return sc
    parts = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    body = "\n".join(parts)
    if getattr(result, "isError", False):
        raise RuntimeError(f"MCP tool error: {body}")
    try:
        return json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return {"raw_text": body}


async def _call_async(server_url: str, name: str, args: dict | None, account: str) -> dict:
    provider = _provider(server_url, account)
    async with streamablehttp_client(server_url, auth=provider) as (read, write, _sid):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, args or {})
            return _extract_json(result)


def call_tool(server_url: str, name: str, args: dict | None = None, account: str = "default") -> dict:
    """Synchronous tool call. Opens an authenticated MCP connection (using stored tokens)."""
    return asyncio.run(_call_async(server_url, name, args, account))


def authenticate(server_url: str, account: str = "default") -> dict:
    """Trigger the OAuth browser flow (if needed) and return the user's accounts as confirmation."""
    return asyncio.run(_call_async(server_url, "get_accounts", {}, account))
