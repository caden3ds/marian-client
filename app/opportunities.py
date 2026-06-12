"""Opportunity stream — HIGH-RISK earnings IV-crush alerts (informational, manual execution).

Polls `opportunities/{eastern_day}/earnings` over Firestore REST (entitlement-gated by rules),
mirroring SignalStream's transport. These are options-spread playbooks, NOT equity signals — they
never reach the execution engine. A slow cadence (default 5 min) is plenty: the scanner publishes
once per afternoon.
"""

from __future__ import annotations

import threading
from typing import Callable

from app import firebase_rest as fb
from app.signals import eastern_day

Opportunity = dict
OpportunityCallback = Callable[[Opportunity], None]


class OpportunityStream:
    def __init__(self, session, project_id: str, interval: float = 300.0, day: str | None = None,
                 refresher=None):
        self._session = session
        self._project = project_id
        self._interval = interval
        self._day = day
        self._refresher = refresher
        self._seen: set[str] = set()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.last_error: str | None = None

    def poll(self) -> list[Opportunity]:
        """Fetch today's opportunities, dedupe by doc id. 404/empty days yield []."""
        day = self._day or eastern_day()
        path = f"opportunities/{day}/earnings"
        try:
            docs = fb.list_documents(self._project, path, self._session.id_token)
        except fb.AuthError:
            if not self._refresher:
                raise
            self._session.id_token = self._refresher()
            docs = fb.list_documents(self._project, path, self._session.id_token)

        fresh: list[Opportunity] = []
        for doc_id, data in docs:
            if doc_id in self._seen:
                continue
            self._seen.add(doc_id)
            fresh.append(data)
        return fresh

    def start(self, on_opportunity: OpportunityCallback) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def loop():
            while not self._stop.is_set():
                try:
                    for opp in self.poll():
                        on_opportunity(opp)
                    self.last_error = None
                except Exception as e:  # network/permission hiccups must not kill the loop
                    self.last_error = str(e)
                self._stop.wait(self._interval)

        self._thread = threading.Thread(target=loop, name="opportunity-stream", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
