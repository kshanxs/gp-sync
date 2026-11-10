"""How many files run at once: dynamic grows on clean runs, halves when Google says slow down,
and keeps the bytes in flight bounded."""

import threading
import time

import pytest
import requests

import pace
import sync

MB = 1024**2


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    pace.reset()
    yield
    pace.reset()


def http(code):
    r = requests.Response()
    r.status_code = code
    return requests.HTTPError(response=r)


def test_settings_values():
    assert pace.clamp(0) == pace.clamp(None) == pace.clamp("x") == pace.AUTO
    assert pace.clamp(3) == 3 and pace.clamp(99) == pace.MANUAL_MAX


def test_outcomes():
    assert pace.outcome(None) == "ok"
    assert pace.outcome(http(429)) == "slow" and pace.outcome(http(503)) == "slow"
    assert pace.outcome(requests.Timeout()) == "slow"
    assert pace.outcome(http(400)) == "error" and pace.outcome(ValueError()) == "error"


def test_dynamic_grows_on_clean_runs_up_to_the_ceiling():
    p = pace.get("a", "upload")
    assert p.allowed(0) == pace.START
    for _ in range(500):
        p.started(10)
        p.finished(10, "ok")
    assert p.allowed(0) == pace.MAX
    # A fixed number never changes.
    q = pace.get("b", "upload")
    for _ in range(50):
        q.started(10)
        q.finished(10, "ok", fixed=3)
    assert q.allowed(3) == 3 and q.limit == pace.START


def test_slow_down_halves_the_whole_account_once_and_waits_to_grow(monkeypatch):
    up, storage, other = pace.get("a", "upload"), pace.get("a", "storage"), pace.get("b", "upload")
    up.limit = storage.limit = 8
    for _ in range(3):  # three files running together all hear it: halved once
        up.started(1)
        up.finished(1, "slow")
    assert (up.limit, storage.limit, other.limit) == (4, 4, pace.START)
    for _ in range(20):  # calm down first
        up.started(1)
        up.finished(1, "ok")
    assert up.limit == 4
    later = time.monotonic() + pace.CALM + 1
    monkeypatch.setattr(pace.time, "monotonic", lambda: later)
    for _ in range(4):
        up.started(1)
        up.finished(1, "ok")
    assert up.limit == 5
    # Never below one.
    for _ in range(5):
        up.calm_until = 0
        up._slow()
    assert up.limit == pace.MIN


def test_big_files_run_fewer_at_once():
    p = pace.get("a", "storage")
    p.limit = 12
    assert p.slots([10 * 1024] * 20, 0) == 12  # kilobyte photos: all the pace allows
    assert p.slots([100 * MB] * 20, 0) == 2  # 256 MB in flight at most
    assert p.slots([900 * MB], 0) == 1  # one always may, however big
    p.started(900 * MB)
    assert not p.can_start(1, 0)


def test_wait_blocks_until_a_turn_and_stops_when_asked():
    p = pace.get("a", "upload")
    for _ in range(pace.START):
        assert p.wait(1, 0)
    assert not p.wait(1, 0, stop=lambda: True)
    got = []
    t = threading.Thread(target=lambda: got.append(p.wait(1, 0)))
    t.start()
    time.sleep(0.05)
    assert not got
    p.finished(1, "error")
    t.join(2)
    assert got == [True] and p.running == pace.START


def test_old_settings_move_to_dynamic_once(media):
    sync.q("DELETE FROM meta WHERE k='pace_dynamic'")
    sync.update_settings({"upload_workers": 3, "reclaim": {"workers": 4}})
    sync.migrate_pace()
    st = sync.get_settings()
    assert st["upload_workers"] == 0 and st["reclaim"]["workers"] == 0
    sync.update_settings({"upload_workers": 2})  # chosen afterwards: kept
    sync.migrate_pace()
    assert sync.get_settings()["upload_workers"] == 2


def test_dynamic_upload_slows_down_instead_of_stopping(media):
    """Dynamic: Google answers 429 to one file. Fewer go at once, that file goes again, and the round
    carries on. A fixed number stops the round as before."""
    from helpers import FakeClient

    tried = []

    class Busy(FakeClient):
        def _upload_file(self, file_path, *a, **k):
            name = file_path.name
            tried.append(name)
            if name == "a.jpg" and tried.count(name) == 1:
                raise http(429)
            return super()._upload_file(file_path, *a, **k)

    outcomes = {"a.jpg": "uploading", "b.HEIC": "uploading", "c.mp4": "uploading"}
    sync.update_settings({"upload_workers": 0})
    acc = sync.accounts()[0][0]
    pace.get(acc, "upload").limit = 6
    sync.upload_all(Busy(outcomes), sync.scan())
    statuses = dict(sync.q("SELECT path, status FROM files"))
    assert sorted(statuses.values()) == ["uploaded"] * 3 and tried.count("a.jpg") == 2
    assert pace.get(acc, "upload").limit == 3

    sync.q("DELETE FROM files")
    tried.clear()
    sync.update_settings({"upload_workers": 1})
    sync.upload_all(Busy(outcomes), sync.scan())
    assert tried.count("a.jpg") == 1 and "Google is busy" in sync.state["error"]
