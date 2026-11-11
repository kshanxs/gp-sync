"""Several Google accounts: a folder can be backed up to up to five accounts at once."""

import json

import pytest
from helpers import FakeClient

import sync

SECOND = "androidId=abc&app=com.google.android.apps.photos&Email=two%40example.com&lang=en_US&Token=y"


def auth_for(n):
    return f"androidId=abc&app=com.google.android.apps.photos&Email=a{n}%40example.com&lang=en_US&Token=z"


@pytest.fixture
def two(media):
    sync.q("DELETE FROM copies")
    sync.save_account(SECOND)
    yield ["test@example.com", "two@example.com"]
    sync.q("DELETE FROM copies")


def test_folder_to_several_accounts(two):
    sync.update_settings({"folders": {"Photos": two, "Videos": ["test@example.com"]}})
    assert sync.folder_targets()["Photos"] == two
    assert sync.folder_map()["Photos"] == "test@example.com"  # the first is the main account
    todo = sync.scan()
    main = [t for t in todo if len(t) == 4]
    copies = [t for t in todo if len(t) == 5]
    assert {t[3] for t in main} == {"test@example.com"} and len(main) == 3
    assert {t[3] for t in copies} == {"two@example.com"} and len(copies) == 2  # the two photos only
    assert main == todo[: len(main)]  # main backups go first


def test_copies_are_recorded_per_account(two):
    sync.update_settings({"folders": {"Photos": two}})
    todo = sync.scan()
    ok = FakeClient({"a.jpg": "uploading", "b.HEIC": "skipped"})
    sync.upload_all(ok, [t for t in todo if t[3] == "test@example.com"], "test@example.com")
    sync.upload_all(ok, [t for t in todo if t[3] == "two@example.com"], "two@example.com")
    assert (
        sync.q("SELECT COUNT(*) FROM copies WHERE account='two@example.com' AND status IN ('uploaded','exists')")[0][0]
        == 2
    )
    assert sync.scan() == []  # everything is in both accounts
    f = sync.state["folders"]["Photos"]
    assert f["accounts"] == two and f["copies"]["two@example.com"] == 2
    s = json.loads(sync.status_body())
    assert s["copies"]["two@example.com"]["done"] == 2 and s["max_accounts"] == 5


def test_failed_copy_is_retried_alone(two):
    sync.update_settings({"folders": {"Photos": two}})
    todo = sync.scan()
    sync.upload_all(FakeClient({"a.jpg": "uploading", "b.HEIC": "uploading"}), [t for t in todo if len(t) == 4])
    sync.upload_all(FakeClient({"a.jpg": KeyError("bad"), "b.HEIC": "uploading"}), [t for t in todo if len(t) == 5])
    again = sync.scan()
    assert [(t[0].rsplit("/", 1)[-1], t[3]) for t in again] == [("a.jpg", "two@example.com")]


def test_old_single_account_settings_still_work(two):
    sync.meta_set("settings", json.dumps({**sync.get_settings(), "folders": {"Photos": "two@example.com"}}))
    assert sync.folder_targets()["Photos"] == ["two@example.com"]


def test_at_most_five_accounts(media):
    for f in sync.ACCOUNTS_DIR.glob("*.auth"):
        f.unlink()
    sync.meta_set("account_order", "[]")
    for n in range(5):
        assert sync.save_account(auth_for(n))
    with pytest.raises(ValueError, match="Up to 5"):
        sync.save_account(auth_for(9))
    assert sync.save_account(auth_for(0))  # replacing a login is always fine
    sync.update_settings({"folders": {"Photos": [f"a{n}@example.com" for n in range(5)] + ["extra@example.com"]}})
    assert len(sync.get_settings()["folders"]["Photos"]) == 5


def test_accounts_upload_side_by_side(two, monkeypatch):
    """Each account uploads on its own threads at the same time; the same file can go to two
    accounts at once without one hiding the other, and the copies count down as they finish."""
    import threading

    sync.update_settings({"folders": {"Photos": two}, "upload_workers": 1})
    together = threading.Barrier(2, timeout=5)
    seen = {}

    class Both(FakeClient):
        def _upload_file(self, file_path, *a, **k):
            if file_path.name == "a.jpg":
                together.wait()  # both accounts are sending a.jpg at this moment
                seen.setdefault("uploading", len(sync.state["uploading"]))
                seen.setdefault("lanes", sync.uploading_lanes())
                together.wait()
            return super()._upload_file(file_path, *a, **k)

    client = Both({"a.jpg": "uploading", "b.HEIC": "uploading"})
    monkeypatch.setattr(sync, "_reclaim_client", lambda account: client)
    sync.scan()
    assert sync.state["copies_pending_by"] == {"two@example.com": 2}
    sync.run_round()
    assert seen == {"uploading": 2, "lanes": 2}  # the speed limit is shared by the two
    lanes = sync.state["lanes"]
    assert lanes["test@example.com"] == {"phase": "done", "done": 2, "total": 2, "copies": 0, "copies_done": 0}
    assert lanes["two@example.com"] == {"phase": "done", "done": 2, "total": 2, "copies": 2, "copies_done": 2}
    assert sync.state["done"] == sync.state["total"] == 4 and sync.state["uploading"] == {}
    assert sync.state["copies_pending"] == 0 and sync.state["folders"]["Photos"]["copies"]["two@example.com"] == 2
    s = json.loads(sync.status_body())
    assert s["lanes"]["two@example.com"]["copies_done"] == 2 and s["copies_pending"] == 0
    assert s["copies_pending_by"] == {}


def test_one_account_trouble_does_not_stop_another(two, monkeypatch):
    sync.update_settings({"folders": {"Photos": two}})
    ok = FakeClient({"a.jpg": "uploading", "b.HEIC": "uploading"})

    def client(account):
        if account == "two@example.com":
            raise RuntimeError("no login")
        return ok

    monkeypatch.setattr(sync, "_reclaim_client", client)
    sync.run_round()
    assert sync.state["lanes"]["test@example.com"]["phase"] == "done"
    assert sync.state["lanes"]["two@example.com"]["phase"] == "stopped"
    assert sync.q("SELECT COUNT(*) FROM files WHERE status='uploaded'")[0][0] == 2


def test_shared_speed_limit_follows_the_accounts(monkeypatch):
    slept = []
    clock = iter([100.0, 100.5, 101.0])
    monkeypatch.setattr(sync.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(sync.time, "sleep", slept.append)
    share = [8.0]
    t = sync.Throttle(lambda: share[0])
    t(0)
    t(500_000)  # 0.5 MB at 1 MB/s by 100.5: on time
    share[0] = 4.0  # a second account started: half the rate from here
    t(1_000_000)  # 0.5 MB more at 0.5 MB/s is due at 101.5; it is 101.0
    assert slept == [pytest.approx(0.5)]


def test_folder_report_shows_each_account(two):
    sync.update_settings({"folders": {"Photos": two}})
    todo = sync.scan()
    sync.upload_all(FakeClient({"a.jpg": "uploading", "b.HEIC": KeyError("bad")}), [t for t in todo if len(t) == 4])
    sync.upload_all(FakeClient({"a.jpg": "uploading", "b.HEIC": "uploading"}), [t for t in todo if len(t) == 5])
    sync.scan()
    photos = {c["name"]: c for c in sync.folder_report("", "folders", 30)["children"]}["Photos"]
    assert photos["accounts"] == [
        {"account": "test@example.com", "main": True, "files": 2, "done": 1},
        {"account": "two@example.com", "main": False, "files": 2, "done": 2},
    ]
    assert photos["failed"] == 1 and photos["synced"] == 1  # failed is not counted as waiting
    assert sync.folder_report("", "folders", 30)["total"]["failed"] == 1
