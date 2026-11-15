"""How many files or items run at once, per account and kind of work (uploads, Free storage,
account copies).

Settings hold a fixed number (1 to MANUAL_MAX) or 0 for dynamic. Dynamic starts at START and grows
by one after as many clean finishes in a row as run at once, up to MAX. When Google says to slow
down (HTTP 429 or a 5xx, or a timeout) every kind of work in that account halves and doesn't grow
again for CALM seconds. Either way, at most BYTES_IN_FLIGHT are moved at once, so big videos run a
few at a time while photos of a few kilobytes run many side by side.
"""

import collections
import threading
import time

AUTO = 0  # the setting's value for dynamic
MIN = 1
START = 4
MAX = 12  # dynamic never goes past this: 16 at once made Google refuse most requests
MANUAL_MAX = 8  # the most a fixed setting allows; past it errors go up for little gain
CALM = 60  # seconds after a slow-down before dynamic grows again
BYTES_IN_FLIGHT = 256 * 1024**2
SAME_SLOWDOWN = 5  # seconds: slow-downs this close together count as one


def clamp(value):
    """A setting's value: 0 (dynamic), or 1 to MANUAL_MAX."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return AUTO
    return AUTO if n <= 0 else min(MANUAL_MAX, n)


def outcome(e):
    """How a finished file went: "ok", "slow" (Google asked to slow down) or "error" (the file)."""
    if e is None:
        return "ok"
    import requests

    if isinstance(e, requests.HTTPError) and e.response is not None:
        code = e.response.status_code
        return "slow" if code == 429 or code >= 500 else "error"
    if isinstance(e, requests.Timeout):
        return "slow"
    return "error"


class Pace:
    """One account's pace for one kind of work."""

    def __init__(self, account=""):
        self.account = account
        self.cond = threading.Condition()
        self.limit = START
        self.running = 0
        self.bytes = 0
        self.streak = 0  # clean finishes in a row
        self.calm_until = 0.0
        self.slowdowns = 0

    def allowed(self, fixed):
        return clamp(fixed) or self.limit

    def can_start(self, size, fixed):
        """One more may start: under the number allowed, and under the bytes in flight (one always may)."""
        with self.cond:
            return self.running < self.allowed(fixed) and (
                self.running == 0 or self.bytes + max(0, size or 0) <= BYTES_IN_FLIGHT
            )

    def slots(self, sizes, fixed, running=None):
        """How many of these (sizes, in order) may start now. `running`: started elsewhere and not
        counted here (Free storage keeps its own list)."""
        with self.cond:
            n_run = self.running if running is None else running
            budget = self.bytes
            n = 0
            for size in sizes:
                if n_run + n >= self.allowed(fixed):
                    break
                if n_run + n > 0 and budget + max(0, size or 0) > BYTES_IN_FLIGHT:
                    break
                budget += max(0, size or 0)
                n += 1
            return n

    def wait(self, size, fixed, stop=None):
        """Wait for a turn, then count this file as running. False if `stop()` turned true first."""
        size = max(0, size or 0)
        with self.cond:
            while not (
                self.running < self.allowed(fixed) and (self.running == 0 or self.bytes + size <= BYTES_IN_FLIGHT)
            ):
                if stop and stop():
                    return False
                self.cond.wait(1)
            self.running += 1
            self.bytes += size
            return True

    def started(self, size):
        with self.cond:
            self.running += 1
            self.bytes += max(0, size or 0)

    def finished(self, size, how, fixed=AUTO):
        """A file is done: "ok", "slow" or "error". Dynamic grows after a clean run; a slow-down
        halves the whole account (see slow_down)."""
        with self.cond:
            self.running = max(0, self.running - 1)
            self.bytes = max(0, self.bytes - max(0, size or 0))
            if how == "ok":
                self.streak += 1
                if (
                    not clamp(fixed)
                    and self.streak >= self.limit
                    and time.monotonic() >= self.calm_until
                    and self.limit < MAX
                ):
                    self.limit += 1
                    self.streak = 0
            self.cond.notify_all()
        if how == "slow":
            slow_down(self.account)

    def _slow(self):
        with self.cond:
            now = time.monotonic()
            if now < self.calm_until - CALM + SAME_SLOWDOWN:
                return  # files that were running together all hear it: halve once
            self.limit = max(MIN, self.limit // 2)
            self.streak = 0
            self.calm_until = time.monotonic() + CALM
            self.slowdowns += 1
            self.cond.notify_all()

    def report(self, fixed):
        with self.cond:
            return {
                "dynamic": not clamp(fixed),
                "now": self.allowed(fixed),
                "running": self.running,
                "calm": max(0, round(self.calm_until - time.monotonic())),
                "slowdowns": self.slowdowns,
            }


_paces = collections.defaultdict(dict)  # account -> kind -> Pace
_lock = threading.Lock()


def get(account, kind):
    with _lock:
        p = _paces[account].get(kind)
        if p is None:
            p = _paces[account][kind] = Pace(account)
        return p


def slow_down(account):
    """Google asked this account to slow down: every kind of work in it halves."""
    with _lock:
        paces = list(_paces[account].values())
    for p in paces:
        p._slow()


def reset():
    """Forget every pace (tests)."""
    with _lock:
        _paces.clear()
