"""Each process (uploads, freed storage, copies, date fixes) keeps its own history."""

import time

import sync


def test_each_process_has_its_own_days_hours_and_weekdays(clean_stats):
    sync.q("DELETE FROM reclaim")
    sync.q("DELETE FROM copyjobs")
    sync.sq("DELETE FROM events")
    sync.sq("DELETE FROM uploads")
    now = time.time()
    sync.add_stats(now, photos=2, photo_bytes=300)
    sync.sq("INSERT INTO uploads (time, name, kind, bytes, ms, result, account) VALUES (?,?,?,?,?,?,?)",
            (now, "a.jpg", "photo", 300, 10, "uploaded", "a"))  # fmt: skip
    sync.q("""INSERT INTO reclaim (account, media_key, state, quota, finished, updated)
              VALUES ('a', 'K', 'done', 500, ?, ?)""", (now, now))  # fmt: skip
    sync.q("""INSERT INTO copyjobs (src, dst, media_key, state, size, finished, updated)
              VALUES ('a', 'b', 'C', 'done', 70, ?, ?)""", (now - 86400, now - 86400))  # fmt: skip
    sync.sq("INSERT INTO events (time, kind, text, name) VALUES (?, 'fixed', 'Fixed the date of x', 'x')", (now,))
    p = sync.process_activity(30, 0)["processes"]
    assert p["uploads"]["count"] == 2 and p["uploads"]["bytes"] == 300 and sum(p["uploads"]["hours"]) == 1
    assert p["freed"]["count"] == 1 and p["freed"]["bytes"] == 500 and sum(p["freed"]["weekdays"]) == 1
    assert p["copied"]["days"][0]["count"] == 1 and p["copied"]["bytes"] == 70
    assert p["fixed"]["count"] == 1
    hour = int(now // 3600) % 24
    assert p["freed"]["hours"][hour] == 1


def test_backend_has_its_own_version():
    assert sync.BACKEND_VERSION and sync.about()["backend_version"] == sync.BACKEND_VERSION
