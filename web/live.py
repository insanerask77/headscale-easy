"""Live device status over Server-Sent Events.

One background poller (shared by every browser) lists the nodes every few
seconds, diffs them against the previous list and hands the changes to the
subscribers. Each subscriber only gets the devices it may see: everything for
admins and auditors, only their own for members.

The poller runs only while somebody is listening, backs off when Headscale is
unreachable (restarting, upgrading) and never raises into a request thread.
Standard library only; the stream is plain `text/event-stream`, so it needs no
WebSocket handshake or new dependency, and the browser reconnects by itself."""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from typing import Callable

log = logging.getLogger("live")

INTERVAL = 3.0  # seconds between polls while someone listens
MAX_BACKOFF = 30.0
HEARTBEAT = 15.0  # a comment line this often detects dead clients
QUEUE_SIZE = 200  # a stuck client is dropped instead of growing without bound
MAX_PER_SESSION = 4  # open tabs of one session
MAX_TOTAL = 200


def summarize(node: dict) -> dict:
    """What matters to the page from a Headscale node."""
    user = node.get("user") or {}
    return {"id": str(node.get("id")), "name": node.get("givenName") or node.get("name") or "",
            "online": bool(node.get("online")), "user": str(user.get("id", ""))}


def diff(old: dict[str, dict], new: dict[str, dict]) -> list[dict]:
    """Events between two snapshots ({id: summary}): added, removed, renamed,
    online, offline."""
    events = []
    for nid, n in new.items():
        before = old.get(nid)
        if before is None:
            events.append({"type": "added", **n})
            continue
        if before["name"] != n["name"]:
            events.append({"type": "renamed", **n, "was": before["name"]})
        if before["online"] != n["online"]:
            events.append({"type": "online" if n["online"] else "offline", **n})
    for nid, n in old.items():
        if nid not in new:
            events.append({"type": "removed", **n})
    return events


class Subscriber:
    """One open stream. `user` is None for "all devices", else a Headscale user id."""

    def __init__(self, sid: str, user: str | None):
        self.sid, self.user = sid, user
        self.q: queue.Queue = queue.Queue(QUEUE_SIZE)
        self.closed = threading.Event()

    def wants(self, event: dict) -> bool:
        return self.user is None or event.get("user") == self.user

    def push(self, event: dict) -> None:
        try:
            self.q.put_nowait(event)
        except queue.Full:  # too slow: drop it, the browser reconnects and refreshes
            self.close()

    def get(self, timeout: float) -> dict | None:
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self.closed.set()
        try:  # wake a reader blocked in get() so the stream ends now, not at the next heartbeat
            self.q.put_nowait({"type": "closed"})
        except queue.Full:
            pass  # it has events to read anyway


class TooMany(Exception):
    pass


class Hub:
    def __init__(self, fetch: Callable[[], list[dict]], interval: float = INTERVAL):
        self.fetch, self.interval = fetch, interval
        self.lock = threading.Lock()
        self.subs: list[Subscriber] = []
        self.thread: threading.Thread | None = None
        self.snapshot: dict[str, dict] | None = None
        self.stopping = False

    # --- subscribers ---
    def subscribe(self, sid: str, user: str | None) -> Subscriber:
        with self.lock:
            if len(self.subs) >= MAX_TOTAL or sum(1 for s in self.subs if s.sid == sid) >= MAX_PER_SESSION:
                raise TooMany()
            sub = Subscriber(sid, user)
            self.subs.append(sub)
            if self.thread is None or not self.thread.is_alive():
                self.stopping = False
                self.thread = threading.Thread(target=self.run, name="live-poller", daemon=True)
                self.thread.start()
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        with self.lock:
            if sub in self.subs:
                self.subs.remove(sub)
        sub.close()

    def count(self) -> int:
        with self.lock:
            return len(self.subs)

    def close_all(self) -> None:
        """Ends every stream (server shutdown, tests)."""
        self.stopping = True
        with self.lock:
            subs = list(self.subs)
        for sub in subs:
            sub.close()

    # --- events ---
    def publish(self, events: list[dict]) -> None:
        with self.lock:
            subs = list(self.subs)
        for sub in subs:
            for event in events:
                if sub.wants(event):
                    sub.push(event)

    def poll_once(self) -> list[dict]:
        new = {s["id"]: s for s in map(summarize, self.fetch())}
        events = [] if self.snapshot is None else diff(self.snapshot, new)
        self.snapshot = new
        return events

    def run(self) -> None:
        delay, failing = self.interval, False
        self.snapshot = None  # start from a fresh baseline: no event for what already existed
        while not self.stopping:
            with self.lock:
                if not self.subs:  # nobody listening: stop polling, the next subscriber restarts it
                    self.thread = None
                    return
            try:
                events = self.poll_once()
                if failing:
                    log.info("Headscale is back; live updates resume")
                failing, delay = False, self.interval
                if events:
                    self.publish(events)
            except Exception as exc:  # noqa: BLE001 - Headscale restarting or unreachable
                if not failing:
                    log.warning("live updates paused, cannot list the nodes: %s", exc)
                failing = True
                delay = min(delay * 2, MAX_BACKOFF)
            time.sleep(delay)


def format_event(event: dict) -> bytes:
    """One SSE message. Only ids, names and the kind of change: no keys, no IPs."""
    data = {k: event[k] for k in ("type", "id", "name", "online") if k in event}
    return f"event: node\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()
