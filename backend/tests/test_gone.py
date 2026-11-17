"""Deleted in Google Photos: files sent there that the user deleted are listed, never uploaded again,
and can be deleted here and in the other accounts that still have them."""

import hashlib
import json
import os
import time
import urllib.request

import pytest
from gpmc.db import Storage
from helpers import post
from test_free_storage import item

import sync

MAIN, TWO = "test@example.com", "two@example.com"
SECOND = "androidId=abc&app=com.google.android.apps.photos&Email=two%40example.com&lang=en_US&Token=y"
A, B, C = "Photos/trip/a.jpg", "Photos/b.HEIC", "Videos/c.mp4"
DATA = {A: b"x" * 100, B: b"y" * 200, C: b"z" * 300}


def dedup(name):
    return sync._b64_hash(hashlib.sha1(DATA[name]).digest())


def sent(name, account=MAIN, key=None, when=1000.0, table="files"):
    p = str(sync.MEDIA / name)
    st = os.stat(p)
    if table == "files":
        sync.q(
            "INSERT OR REPLACE INTO files (path, size, mtime, status, media_key, account, sha1, updated) "
            "VALUES (?,?,?,'uploaded',?,?,?,?)",
            (p, st.st_size, st.st_mtime, key or f"K-{name}", account, hashlib.sha1(DATA[name]).hexdigest(), when),
        )
    else:
        sync.q(
            "INSERT OR REPLACE INTO copies (path, account, size, mtime, status, media_key, updated) "
            "VALUES (?,?,?,?,'uploaded',?,?)",
            (p, account, st.st_size, st.st_mtime, key or f"K2-{name}", when),
        )


def library(account, *items, read=2000.0):
    db = sync._library_db(account)
    db.parent.mkdir(parents=True, exist_ok=True)
    db.unlink(missing_ok=True)
    with Storage(db) as st:
        st.update(list(items))
    sync.meta_set(f"library_refreshed:{account}", str(read))


def everything_but(*gone, account=MAIN, emptied=()):
    """The account's library: the files `gone` in Google's Trash (deleted by the user), the files
    `emptied` gone for good, the rest there."""
    library(
        account,
        *[item(f"K-{n}", data=DATA[n], **({"trash_timestamp": TRASHED_MS} if n in gone else {}))
          for n in DATA if n not in emptied],
    )  # fmt: skip


TRASHED_MS = 1_700_000_000_000


def names():
    return sorted(i["path"] for i in sync.gone_items())


def states(path):
    return {a["account"]: a["state"] for i in sync.gone_items() if i["path"] == path for a in i["accounts"]}


class Api:
    def __init__(self):
        self.trashed = []

    def move_remote_media_to_trash(self, dedups):
        self.trashed.append(list(dedups))


class Client:
    def __init__(self):
        self.api = Api()


@pytest.fixture
def gone(media, tmp_path, monkeypatch):
    monkeypatch.setattr(sync, "GPMC_HOME", tmp_path / "gpmc")
    tables = ("copies", "gone_kept", "gone_seen", "gone_removed", "reclaim", "live_fix", "snap_items", "snap_copies")
    for t in tables:
        sync.q(f"DELETE FROM {t}")
    sync.sq("DELETE FROM media_dates")
    sync.sq("DELETE FROM events")
    sync.update_settings({"folders": {"Photos": [MAIN], "Videos": [MAIN]}})
    for name in DATA:
        sent(name)
    yield
    for t in tables:
        sync.q(f"DELETE FROM {t}")
    for f in sync.ACCOUNTS_DIR.glob("*.auth"):
        if "two" in f.name:
            f.unlink()


def test_nothing_is_listed_before_the_library_was_read(gone):
    assert names() == []


def test_only_what_went_through_googles_trash_is_the_users(gone):
    library(
        MAIN,
        item(f"K-{A}", data=DATA[A]),  # still there
        item(f"K-{B}", data=DATA[B], trash_timestamp=TRASHED_MS),  # in the Trash: the user deleted it
    )  # c.mp4 vanished without the Trash: Google Photos never deletes that way, so not the user
    got = {i["path"]: i for i in sync.gone_items()}
    assert sorted(got) == [B]
    assert got[B]["trashed"] == 1_700_000_000 and got[B]["accounts"][0]["state"] == "trash"
    assert got[B]["kind"] == "photo" and got[B]["size"] == 200 and got[B]["everywhere"] and got[B]["writable"]
    # Emptied from the Trash afterwards: still the user's, now gone for good.
    library(MAIN, item(f"K-{A}", data=DATA[A]))
    assert states(B) == {MAIN: "deleted"} and names() == [B]


def test_found_by_its_fingerprint_when_google_gave_it_a_new_key(gone):
    library(MAIN, item("NEW-A", data=DATA[A]), item("NEW-B", data=DATA[B]), item("NEW-C", data=DATA[C]))
    assert names() == []


def test_the_locked_folder_counts_as_still_in_google(gone):
    library(MAIN, item(f"K-{A}", data=DATA[A], is_locked=True), item("B", data=DATA[B]), item("C", data=DATA[C]))
    assert names() == []


def test_sent_after_the_library_was_read_is_not_judged(gone):
    sent(C, when=3000.0)
    everything_but(C)
    assert names() == []


def test_deleted_in_one_account_is_listed_with_the_accounts_that_still_have_it(gone):
    sync.save_account(SECOND)
    sent(C, account=TWO, table="copies")
    everything_but(C)
    library(TWO, item("C2", data=DATA[C]))  # the second account still has c.mp4
    [i] = sync.gone_items()
    assert i["path"] == C and not i["everywhere"] and i["in_google"] == [TWO]
    assert states(C) == {MAIN: "trash", TWO: "present"}
    everything_but(C, account=TWO)
    library(TWO, item("C2", data=DATA[C], trash_timestamp=TRASHED_MS))
    assert states(C) == {MAIN: "trash", TWO: "trash"} and sync.gone_items()[0]["everywhere"]


def test_files_being_replaced_in_google_are_not_listed(gone):
    library(MAIN, item("A", data=DATA[A]))
    sync.sq("INSERT INTO media_dates (path, fix) VALUES (?, 'in_google')", (str(sync.MEDIA / B),))
    sync.q("INSERT INTO reclaim (account, media_key, dedup_key, state) VALUES (?, 'X', ?, 'downloading')",
           (MAIN, dedup(C)))  # fmt: skip
    assert names() == []


def test_live_photo_halves_follow_their_motion_photo(gone):
    still, mov = str(sync.MEDIA / B), str(sync.MEDIA / C)
    sync.q("INSERT INTO live_fix (still, account, mov, state, new_key, new_dedup, old_still, old_mov) "
           "VALUES (?,?,?,'done','M','MD',?,?)", (still, MAIN, mov, dedup(B), dedup(C)))  # fmt: skip
    # GP Sync trashed the old separate items itself; the motion photo is there: not listed.
    library(MAIN, item("A", data=DATA[A]), item("M", data=b"motion"))
    assert names() == []
    library(MAIN, item("A", data=DATA[A]), item("M", data=b"motion", trash_timestamp=TRASHED_MS))
    assert names() == [B, C]  # the user deleted the motion photo


def test_items_gp_sync_removed_itself_never_count(gone):
    everything_but(C)
    sync._note_removed(MAIN, [dedup(C)])
    assert names() == []


def test_items_the_live_photo_fix_trashed_before_it_kept_a_record_are_recognised(gone):
    trashed_at = 1_794_836_101.68
    library(MAIN, item("A", data=DATA[A]), item("B", data=DATA[B]),
            item(f"K-{C}", data=DATA[C], trash_timestamp=int(trashed_at * 1000)))  # fmt: skip
    sync.sq("INSERT INTO events (time, kind, text, name) VALUES (?, 'info', ?, '')",
            (trashed_at + 1, f"Live Photo Photos/x.heic is a motion photo now; its old still and video went to "
                             f"Google's Trash ({MAIN})"))  # fmt: skip
    assert names() == []


def test_a_snapchat_memory_is_found_by_any_copy_google_keeps(gone):
    p = str(sync.MEDIA / C)
    everything_but(C)
    library(MAIN, item("A", data=DATA[A]), item("B", data=DATA[B]), item("KEPT", data=b"kept copy"))
    sync.q("INSERT INTO snap_items (path, state, google_key) VALUES (?, 'done', 'KEPT')", (p,))
    assert names() == []
    # The file's own match was a copy GP Sync deleted itself: not deleted by the user.
    sync.q("DELETE FROM snap_items")
    sent(C, key="COPY")
    sync.q("INSERT INTO snap_copies (path, media_key, dedup, state) VALUES (?, 'COPY', 'x', 'deleted')", (p,))
    assert names() == []


def test_never_uploaded_again_by_any_sync(gone):
    everything_but(C)
    sync.gone_check()
    assert sync.q("SELECT account FROM gone_seen WHERE path=?", (str(sync.MEDIA / C),)) == [(MAIN,)]
    sync.q("DELETE FROM files WHERE path=?", (str(sync.MEDIA / C),))  # even with no upload record
    sync.save_account(SECOND)
    sync.update_settings({"folders": {"Photos": [MAIN, TWO], "Videos": [MAIN, TWO]}})  # a new account too
    todo = sync.scan()
    assert not [t for t in todo if t[0].endswith("c.mp4")]
    assert sync.state["folders"]["Videos"]["synced_files"] == 1


def test_noticed_once_logged_and_forgotten_when_restored(gone, monkeypatch):
    logged = []
    monkeypatch.setattr(sync, "log", lambda msg, kind="info", name=None: logged.append((kind, msg)))
    everything_but(C)
    sync.gone_check()
    sync.gone_check()
    assert logged == [("gone", f"{C} was deleted in Google Photos ({MAIN})")]
    assert sync.state["gone"] == 1
    (sync.MEDIA / B).unlink()  # gone here and in Google: nothing to note
    everything_but(B, C)
    sync.gone_check()
    assert [m for _, m in logged if "b.HEIC" in m] == []
    everything_but()  # restored from Google's Trash
    sync.gone_check()
    assert not sync.q("SELECT 1 FROM gone_seen") and sync.state["gone"] == 0


def test_many_at_once_are_logged_as_one_line(gone, monkeypatch):
    logged = []
    monkeypatch.setattr(sync, "log", lambda msg, kind="info", name=None: logged.append(msg))
    monkeypatch.setattr(sync, "GONE_LOG_EACH", 1)
    everything_but(A, B, C)
    sync.gone_check()
    assert logged == [f"3 files were deleted in Google Photos ({MAIN}): see Deleted in Google"]


def test_delete_removes_the_file_and_forgets_it(gone, server):
    everything_but(C)
    status, body = post(server + "/api/gone/delete", json.dumps({"paths": [C, A]}).encode())
    r = json.loads(body)
    assert status == 200 and r["deleted"] == 1 and r["bytes"] == 300
    assert [f["path"] for f in r["failed"]] == [A]  # Google still has it: never deleted
    assert not (sync.MEDIA / C).exists() and (sync.MEDIA / A).exists()
    assert not sync.q("SELECT 1 FROM files WHERE path=?", (str(sync.MEDIA / C),))
    assert not sync.q("SELECT 1 FROM gone_seen WHERE path=?", (str(sync.MEDIA / C),))


def test_delete_in_the_other_accounts_too(gone, monkeypatch):
    sync.save_account(SECOND)
    sent(C, account=TWO, table="copies")
    everything_but(C)
    library(TWO, item("C2", data=DATA[C]))
    client = Client()
    r = sync.gone_delete([C], local=False, google=True, make_client=lambda a: client)
    assert r["trashed"] == 1 and r["deleted"] == 0 and not r["failed"]
    assert client.api.trashed == [[dedup(C)]]
    assert (sync.MEDIA / C).exists()
    assert sync.q("SELECT account, state, by_gp FROM gone_seen WHERE path=? ORDER BY account",
                  (str(sync.MEDIA / C),)) == [(MAIN, "trash", 0), (TWO, "trash", 1)]  # fmt: skip
    # The next library read shows it in the Trash there: noted, not logged as deleted by the user.
    logged = []
    monkeypatch.setattr(sync, "log", lambda msg, kind="info", name=None: logged.append(msg))
    library(TWO, item("C2", data=DATA[C], trash_timestamp=5_000_000))
    assert states(C) == {MAIN: "trash", TWO: "trash"} and logged == []


def test_a_google_error_leaves_the_file_here(gone):
    sync.save_account(SECOND)
    sent(C, account=TWO, table="copies")
    everything_but(C)
    library(TWO, item("C2", data=DATA[C]))

    def broken(a):
        raise RuntimeError("Google said no")

    r = sync.gone_delete([C], local=True, google=True, make_client=broken)
    assert r["deleted"] == 0 and "Google said no" in r["failed"][0]["error"]
    assert (sync.MEDIA / C).exists()


def test_a_read_only_folder_is_reported_and_left_alone(gone, monkeypatch):
    everything_but(C)
    real = os.access
    monkeypatch.setattr(sync.os, "access", lambda p, mode, **k: False if mode == os.W_OK else real(p, mode, **k))
    [i] = sync.gone_items()
    assert not i["writable"]
    r = sync.gone_delete([C])
    assert r["deleted"] == 0 and "read-only" in r["failed"][0]["error"]
    assert (sync.MEDIA / C).exists()


def test_the_upload_folder_is_used_when_the_photo_folder_is_read_only(gone, tmp_path, monkeypatch):
    # As on Umbrel: the media folder is read-only, the same upload folder is mounted writable.
    up = tmp_path / "uploads"
    up.mkdir()
    (sync.MEDIA / "Photos" / "Uploads").mkdir(parents=True, exist_ok=True)
    f = sync.MEDIA / "Photos" / "Uploads" / "d.jpg"
    f.write_bytes(b"d")
    os.link(f, up / "d.jpg")
    monkeypatch.setattr(sync, "UPLOAD_DIR", up)
    monkeypatch.setattr(sync, "upload_folder_name", lambda: "Photos/Uploads")
    real = os.access
    monkeypatch.setattr(
        sync.os, "access",
        lambda p, mode, **k: False if mode == os.W_OK and str(sync.MEDIA) in str(p) else real(p, mode, **k),
    )  # fmt: skip
    assert sync._gone_target(str(f)) == up / "d.jpg"
    assert sync._gone_target(str(sync.MEDIA / C)) is None


def test_keep_hides_it_still_never_uploads_and_can_be_undone(gone, server):
    everything_but(C)
    post(server + "/api/gone/keep", json.dumps({"paths": [C]}).encode())
    with urllib.request.urlopen(server + "/api/gone") as r:
        report = json.loads(r.read())
    assert report["total"] == 0 and report["kept"] == 1
    assert not [t for t in sync.scan() if t[0].endswith("c.mp4")]
    post(server + "/api/gone/keep", json.dumps({"all": True, "keep": False}).encode())
    assert names() == [C]


def test_report_status_and_refresh(gone, server):
    everything_but(B, C)
    sync.meta_set(f"library_refreshed:{MAIN}", str(time.time() - 10))
    with urllib.request.urlopen(server + "/api/gone") as r:
        report = json.loads(r.read())
    assert report["total"] == 2 and report["bytes"] == 500 and report["everywhere"] == 2
    assert report["in_google"] == 0 and report["writable"] == 2 and set(report["read"]) == {MAIN}
    assert not any(k.startswith("_") for k in report["items"][0])
    assert json.loads(sync.status_body())["gone"] == 2
    sync._refresh_requests.clear()
    status, _ = post(server + "/api/gone/refresh", b"{}")
    assert status == 200 and list(sync._refresh_requests) == [MAIN]
    sync._refresh_requests.clear()


def test_library_read_again_every_few_hours(gone):
    assert not sync.gone_due(MAIN)  # never read: the user reads it the first time
    library(MAIN, read=time.time() - sync.GONE_REFRESH - 5)
    assert sync.gone_due(MAIN)
    sync.meta_set(f"library_refreshed:{MAIN}", str(time.time()))
    assert not sync.gone_due(MAIN)


def test_free_storage_replacements_are_not_the_users(gone):
    # Free storage swapped the item for a free copy (other bytes, a new key); the old one is gone.
    sync.q("INSERT INTO reclaim (account, media_key, dedup_key, state, new_key) VALUES (?,?,?,'done','FREE')",
           (MAIN, f"K-{C}", dedup(C)))  # fmt: skip
    library(MAIN, item(f"K-{A}", data=DATA[A]), item(f"K-{B}", data=DATA[B]), item("FREE", data=b"free copy"))
    assert names() == []
    # The user deletes the free copy: now it is theirs.
    library(MAIN, item(f"K-{A}", data=DATA[A]), item(f"K-{B}", data=DATA[B]),
            item("FREE", data=b"free copy", trash_timestamp=TRASHED_MS))  # fmt: skip
    assert names() == [C]


def test_trashed_right_before_gp_syncs_own_deleted_line_is_gp_syncs(gone):
    trashed_at = 1_794_000_000.0
    library(MAIN, item(f"K-{A}", data=DATA[A]), item(f"K-{B}", data=DATA[B]),
            item(f"K-{C}", data=DATA[C], trash_timestamp=int(trashed_at * 1000)))  # fmt: skip
    sync.sq("INSERT INTO events (time, kind, text, name) VALUES (?, 'deleted', ?, '')",
            (trashed_at + 20, f"Deleted c.mp4 in Google Photos ({MAIN}) to upload it again for free"))  # fmt: skip
    assert names() == []


def test_a_copy_of_a_joined_live_photo_is_gp_syncs(gone):
    # b.HEIC is one Live Photo GP Sync joined; c.mp4 carries the same Apple content ID (a second copy).
    still = str(sync.MEDIA / B)
    sync.q("INSERT INTO live_fix (still, account, mov, state, new_key, new_dedup) VALUES (?,?,?,'done','M','MD')",
           (still, MAIN, str(sync.MEDIA / "x.mov")))  # fmt: skip
    sync.q("INSERT OR REPLACE INTO live_ids (path, cid) VALUES (?, 'CID'), (?, 'CID')", (still, str(sync.MEDIA / C)))
    library(MAIN, item(f"K-{A}", data=DATA[A]), item("M", data=b"motion"),
            item(f"K-{C}", data=DATA[C], trash_timestamp=TRASHED_MS))  # fmt: skip
    assert names() == []
    sync.q("DELETE FROM live_ids")


def test_notes_from_before_that_never_saw_the_trash_are_dropped(gone):
    sync.q("INSERT INTO gone_seen (path, account, state, trash, seen, by_gp) VALUES (?,?,'deleted',0,1,0)",
           (str(sync.MEDIA / C), MAIN))  # fmt: skip
    sync.meta_set("gone_trash_only", "")
    sync.gone_migrate()
    everything_but(emptied=(C,))
    assert names() == [] and not sync.q("SELECT 1 FROM gone_seen")
    assert sync.meta_get("gone_trash_only") == "1"
