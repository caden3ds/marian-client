"""Signal stream — delivers the cloud's impersonal signals to the client.

Transport (Chunk 3 decision): short-poll the Firestore REST collection
`signals/{eastern_day}/live` with the user's idToken. Reads are entitlement-gated by
security rules, so an unsubscribed user gets PermissionError. The `poll()` method is pure
and directly testable; `start()` wraps it in a background thread.

Each delivered dict conforms to shared/signal_schema.json.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from app import firebase_rest as fb


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

Signal = dict
SignalCallback = Callable[[Signal], None]


def eastern_day(now: datetime | None = None) -> str:
    now = now or datetime.now(ZoneInfo("America/New_York"))
    return now.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


class SignalStream:
    def __init__(
        self,
        session,
        project_id: str,
        interval: float = 5.0,
        day: str | None = None,
        refresher=None,
        since: str | None = None,
    ):
        self._session = session
        self._project = project_id
        self._interval = interval
        self._day = day
        # refresher() -> fresh id_token, called when the current token expires (401).
        self._refresher = refresher
        # only fetch signals issued after this (defaults to now → no replay of the day's backlog)
        self._since = since or _utcnow_iso()
        self._seen: set[str] = set()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.last_error: str | None = None

    def _collection(self) -> str:
        return f"signals/{self._day or eastern_day()}/live"

    def poll(self) -> list[Signal]:
        """Incrementally fetch signals issued since the last poll (cheap), dedupe by signal_id,
        and advance the watermark. Transparently refreshes an expired idToken once and retries."""
        day = self._day or eastern_day()
        try:
            docs = fb.query_signals_after(self._project, day, self._since, self._session.id_token)
        except fb.AuthError:
            if not self._refresher:
                raise
            self._session.id_token = self._refresher()  # refresh + retry once
            docs = fb.query_signals_after(self._project, day, self._since, self._session.id_token)

        fresh: list[Signal] = []
        for _doc_id, data in docs:
            issued = data.get("issued_at")
            if issued and issued > self._since:
                self._since = issued  # advance watermark
            sid = data.get("signal_id")
            if sid and sid in self._seen:
                continue
            if sid:
                self._seen.add(sid)
            fresh.append(data)
        return fresh

    def start(self, on_signal: SignalCallback) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def loop():
            while not self._stop.is_set():
                try:
                    for sig in self.poll():
                        on_signal(sig)
                    self.last_error = None
                except Exception as e:  # network/permission hiccups shouldn't kill the loop
                    self.last_error = str(e)
                self._stop.wait(self._interval)

        self._thread = threading.Thread(target=loop, name="signal-stream", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
