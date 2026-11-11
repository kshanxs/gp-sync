"""Freeing Google storage: download, prove it's the original, wait for the user, upload, check."""

import hashlib
import json
import urllib.error
import urllib.request

import pytest
from gpmc.db import Storage
from gpmc.models import MediaItem

import sync

ACC = "test@example.com"
DATA = b"original photo bytes" * 1000


def b64(data):
    return sync._b64_hash(hashlib.sha1(data).digest())


def item(key, data=DATA, quota=None, **kw):
    return MediaItem(
        media_key=key, file_name=f"{key}.jpg", dedup_key=b64(data), is_canonical=True, type=kw.pop("type", 1),
        caption=kw.pop("caption", None),
        collection_id="", size_bytes=len(data), quota_charged_bytes=len(data) if quota is None else quota,
        origin=kw.pop("origin", "self"), content_version=1, utc_timestamp=kw.pop("utc_timestamp", 1_700_000_000_000),
        server_creation_timestamp=kw.pop("server_creation_timestamp", 0), **kw,
    )  # fmt: skip


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, n):
        yield self.data


class FakeApi:
    bearer_token = "token"
    user_agent = "test"

    def __init__(self, google):
        self.google = google  # dedup keys still in Google
        self.calls = []

    def commit_upload(self, upload_timestamp=None, **kw):
        return upload_timestamp

    def get_download_urls(self, key):
        return {"1": {"5": {"2": {"6": f"https://video-downloads.googleusercontent.com/{key}"}}}}

    def find_remote_media_by_hash(self, digest):
        return "OLD" if sync._b64_hash(digest) in self.google else None

    def move_remote_media_to_trash(self, dedups):
        self.calls.append(("trash", list(dedups)))

    def delete_remote_media_permanently(self, dedups):
        self.calls.append(("delete", list(dedups)))
        self.google.difference_update(dedups)

    def set_favorite(self, dedup, on):
        self.calls.append(("favorite", dedup, on))

    def set_archived(self, dedups, on):
        self.calls.append(("archived", dedups, on))

    def set_item_caption(self, dedup_key="", caption=""):
        self.calls.append(("caption", dedup_key, caption))


class FakeClient:
    def __init__(self, db, google, new_quota=0):
        self.api = FakeApi(google)
        self.db = db
        self.new_quota = new_quota
        self.uploads = []

    def _upload_file(self, path, digest, bar, force_upload, use_quota, saver, progress_callback=None):
        # Like gpmc: the date sent with the upload is the file's time, unless the caller overrides it.
        sent = self.api.commit_upload(upload_timestamp=int(path.stat().st_mtime))
        self.uploads.append((path.read_bytes(), use_quota, saver, sent))
        progress_callback({"phase": "uploading", "bytes_completed": 10})
        return {str(path): "NEW"}

    def update_cache(self, show_progress=True):
        with Storage(self.db) as st:
            st.update([item("NEW", quota=self.new_quota)])


@pytest.fixture
def library(tmp_path, monkeypatch, media):
    monkeypatch.setattr(sync, "GPMC_HOME", tmp_path / "gpmc")
    monkeypatch.setattr(sync, "RECLAIM_DIR", tmp_path / "reclaim")
    sync.reclaim_state.clear()
    sync.q("DELETE FROM reclaim")
    db = sync._library_db(ACC)
    db.parent.mkdir(parents=True)
    with Storage(db) as st:
        st.update(
            [
                item("BIG", is_favorite=True, caption="Goa", width=4032, height=3024),
                item("SMALL", data=b"small"),
                item("FREE", data=b"free", quota=0),
                item("PARTNER", data=b"partner", origin="partner"),
                item("TRASHED", data=b"trash", trash_timestamp=5),
            ]
        )
    monkeypatch.setattr("requests.get", lambda url, **kw: FakeResponse(DATA if "BIG" in url else b"tampered"))
    return db


def step(client):
    """One step, as if the waits between asking Google had passed."""
    sync._reclaim_checked.clear()
    checked = sync.check_step(ACC, lambda a: client, force=True)
    return sync.reclaim_step(lambda a: client) or checked


def test_report_lists_what_counts_biggest_first(library):
    sync.q("INSERT INTO files (path, status, account, sha1) VALUES ('x', 'uploaded', ?, ?)",
           (ACC, hashlib.sha1(b"free").hexdigest()))  # fmt: skip
    r = sync.storage_report(ACC)
    assert [i["key"] for i in r["items"]] == ["BIG", "SMALL"]  # not free, partner or trashed ones
    assert r["counted"] == 2 and r["counted_bytes"] == len(DATA) + 5 and r["total"] == 3
    # All of yours in Google, each file once at full size: BIG and SMALL count, FREE is stored free.
    assert r["stored"] == {
        "count": 3,
        "bytes": len(DATA) + 5 + 4,
        "free_count": 1,
        "free_bytes": 4,
        "photo": {"count": 3, "bytes": len(DATA) + 9},
        "video": {"count": 0, "bytes": 0},
    }
    assert r["ours"] == {"seen": 1, "counted": 0, "uploaded": 1}  # GP Sync's own upload doesn't count
    assert r["items"][0]["taken"] == 1_700_000_000
    # Its shape, for rows at real proportions; unknown when Google's list has none.
    assert (r["items"][0]["width"], r["items"][0]["height"]) == (4032, 3024)
    assert r["items"][1]["width"] is None


def test_whole_round_trip(library):
    google = {b64(DATA)}
    client = FakeClient(library, google)
    sync.reclaim_queue(ACC, ["BIG"])
    assert step(client)
    row = sync.q("SELECT state, local FROM reclaim WHERE media_key='BIG'")[0]
    assert row[0] == "downloaded" and sync.Path(row[1]).read_bytes() == DATA
    assert client.api.calls == []  # nothing deleted before the original is safe here

    # Next step: Google's copy goes (Trash, then out of it) and the original is uploaded again free.
    step(client)
    assert client.api.calls[:2] == [("trash", [b64(DATA)]), ("delete", [b64(DATA)])]
    assert client.uploads == [(DATA, False, False, 1_700_000_000)]  # free upload, original date
    assert ("favorite", b64(DATA), True) in client.api.calls and ("caption", b64(DATA), "Goa") in client.api.calls
    assert sync.q("SELECT state FROM reclaim WHERE media_key='BIG'")[0][0] == "checking"

    step(client)
    assert sync.q("SELECT state, local FROM reclaim WHERE media_key='BIG'")[0] == ("done", "")
    assert not (sync.RECLAIM_DIR / ACC / "BIG").exists()
    assert sync.storage_report(ACC)["freed"] == {"count": 1, "bytes": len(DATA)}


def test_nothing_is_deleted_where_our_own_uploads_count(library):
    with Storage(library) as st:
        st.update([item("OURS", data=b"ours")])  # counts against storage
    sync.q("INSERT INTO files (path, status, account, sha1) VALUES ('x', 'uploaded', ?, ?)",
           (ACC, hashlib.sha1(b"ours").hexdigest()))  # fmt: skip
    client = FakeClient(library, {b64(DATA)})
    sync.reclaim_queue(ACC, ["BIG"])
    step(client)
    step(client)
    assert client.api.calls == [] and client.uploads == []
    # Nothing is even downloaded: it couldn't be replaced, so downloads would only pile up.
    assert sync.q("SELECT state FROM reclaim WHERE media_key='BIG'")[0][0] == "queued"
    assert "own uploads count" in sync.reclaim_state[ACC]["error"]


def test_the_hold_note_clears_once_our_uploads_no_longer_count(library):
    """Items held while GP Sync's uploads counted keep no stale note once they don't: the check
    passing clears it, before the item's turn comes."""
    with Storage(library) as st:
        st.update([item("OURS", data=b"ours")])
    sha = hashlib.sha1(b"ours").hexdigest()
    sync.q("INSERT INTO files (path, status, account, sha1) VALUES ('x', 'uploaded', ?, ?)", (ACC, sha))
    sync.reclaim_queue(ACC, ["BIG"])
    sync.q("UPDATE reclaim SET state='downloaded', error=? WHERE media_key='BIG'", (sync.OURS_COUNT,))
    assert sync._own_uploads_count(ACC)
    assert sync.q("SELECT error FROM reclaim WHERE media_key='BIG'")[0][0] == sync.OURS_COUNT
    sync.q("UPDATE files SET status='exists' WHERE sha1=?", (sha,))  # it turns out not to be ours
    assert not sync._own_uploads_count(ACC)
    assert sync.q("SELECT state, error FROM reclaim WHERE media_key='BIG'")[0] == ("downloaded", "")


def test_a_phone_backup_found_in_google_is_not_one_of_our_uploads(library):
    """A file the phone backed up (counted against storage) that GP Sync only found already in
    Google ('exists') says nothing about GP Sync's own uploads: freeing goes on."""
    with Storage(library) as st:
        st.update([item("PHONE", data=b"phone")])  # counts against storage
    sync.q("INSERT INTO files (path, status, account, sha1) VALUES ('x', 'exists', ?, ?)",
           (ACC, hashlib.sha1(b"phone").hexdigest()))  # fmt: skip
    assert not sync._own_uploads_count(ACC)
    assert sync.storage_report(ACC)["ours"]["counted"] == 0
    client = FakeClient(library, {b64(DATA)})
    sync.reclaim_queue(ACC, ["BIG"])
    step(client)
    step(client)
    assert client.uploads and sync.q("SELECT state FROM reclaim WHERE media_key='BIG'")[0][0] == "checking"


def test_a_broken_checksum_from_google_is_skipped(library):
    """Google's list sometimes has a structure written out as text instead of an item's checksum.
    Asking Google about that fails every time (400, invalid base64): the downloaded file's own
    checksum is used instead, and an item with no usable checksum fails once, clearly."""
    client = FakeClient(library, {b64(DATA)})
    sync.reclaim_queue(ACC, ["BIG"])
    step(client)  # downloaded
    sync.q(
        "UPDATE reclaim SET dedup_key=?, local_hash=? WHERE media_key='BIG'",
        ("{'14': 1885960308, '9': 101}", b64(DATA)),
    )
    step(client)
    assert ("delete", [b64(DATA)]) in client.api.calls and client.uploads
    assert sync.q("SELECT state FROM reclaim WHERE media_key='BIG'")[0][0] == "checking"
    assert not sync._is_hash("{'14': 1}") and sync._is_hash(b64(DATA))


class StubbornApi(FakeApi):
    """Google that says yes to deleting but keeps the item."""

    def delete_remote_media_permanently(self, dedups):
        self.calls.append(("delete", list(dedups)))


def test_retries_when_google_keeps_it(library, monkeypatch):
    monkeypatch.setattr(sync.time, "sleep", lambda s: None)
    client = FakeClient(library, {b64(DATA)})
    client.api = StubbornApi(client.api.google)
    sync.reclaim_queue(ACC, ["BIG"])
    step(client)
    step(client)
    assert client.uploads == []
    assert sync.q("SELECT state FROM reclaim WHERE media_key='BIG'")[0][0] == "in_google"
    sync.reclaim_step(lambda a: client)  # not again within 2 minutes
    assert [c for c in client.api.calls if c[0] == "delete"] == [("delete", [b64(DATA)])]


def test_refuses_a_download_that_is_not_the_original(library):
    sync.reclaim_queue(ACC, ["SMALL"])  # the fake serves other bytes for it
    step(FakeClient(library, set()))
    state, error, local = sync.q("SELECT state, error, local FROM reclaim WHERE media_key='SMALL'")[0]
    assert state == "failed" and "changed copy" in error and not local
    assert not list((sync.RECLAIM_DIR / ACC / "SMALL").glob("*.jpg"))


def test_stops_when_google_counts_the_new_upload(library):
    client = FakeClient(library, set(), new_quota=123)
    sync.reclaim_queue(ACC, ["BIG"])
    for _ in range(3):
        step(client)
    assert sync.q("SELECT state FROM reclaim WHERE media_key='BIG'")[0][0] == "counts"
    assert sync.reclaim_blocked(ACC) and sync.storage_report(ACC)["blocked"]
    assert (sync.RECLAIM_DIR / ACC / "BIG").exists()  # the downloaded copy is kept


def test_cancel_removes_the_downloaded_copy(library):
    sync.reclaim_queue(ACC, ["BIG"])
    step(FakeClient(library, {b64(DATA)}))
    assert sync.reclaim_cancel(ACC, ["BIG"]) == {"cancelled": 1}
    assert not (sync.RECLAIM_DIR / ACC / "BIG").exists()


def test_storage_api(library, server):
    with urllib.request.urlopen(f"{server}/api/storage?account={ACC}") as r:
        assert [i["key"] for i in json.load(r)["items"]] == ["BIG", "SMALL"]
    req = urllib.request.Request(
        f"{server}/api/storage/download", json.dumps({"account": "nobody", "keys": []}).encode()
    )
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(req)


class CountingApi(StubbornApi):
    asked = 0

    def find_remote_media_by_hash(self, digest):
        CountingApi.asked += 1
        return super().find_remote_media_by_hash(digest)


def test_items_google_keeps_are_not_retried_every_second(library, monkeypatch):
    monkeypatch.setattr(sync.time, "sleep", lambda s: None)
    client = FakeClient(library, {b64(DATA)})
    client.api = CountingApi(client.api.google)
    sync.reclaim_queue(ACC, ["BIG", "SMALL"])
    step(client)  # downloads BIG
    sync.reclaim_step(lambda a: client)  # tries to replace BIG once; Google keeps it
    CountingApi.asked = 0
    for _ in range(5):
        sync.reclaim_step(lambda a: client)  # SMALL's download fails; BIG waits 2 minutes
    assert CountingApi.asked == 0


def test_download_waits_during_an_outage(library, monkeypatch):
    import requests

    def offline(url, **kw):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr("requests.get", offline)
    sync.reclaim_queue(ACC, ["BIG"])
    assert not step(FakeClient(library, set()))
    assert sync.q("SELECT state FROM reclaim WHERE media_key='BIG'")[0][0] == "queued"
    assert "No connection" in sync.storage_report(ACC)["error"]
    sync.reclaim_state.clear()


def test_one_login_per_account(monkeypatch):
    made = []

    class Client:
        def __init__(self, auth_data, log_level):
            made.append(auth_data)

    import gpmc

    monkeypatch.setattr(gpmc, "Client", Client)
    monkeypatch.setattr(sync, "_clients", {})
    assert sync._reclaim_client(ACC) is sync._reclaim_client(ACC)
    assert len(made) == 1


def test_reupload_keeps_googles_date_even_if_the_file_time_is_lost(library, monkeypatch):
    real_utime = sync.os.utime
    monkeypatch.setattr(sync.os, "utime", lambda *a, **k: None)  # e.g. a filesystem that ignores it
    client = FakeClient(library, {b64(DATA)})
    sync.reclaim_queue(ACC, ["BIG"])
    step(client)
    step(client)
    monkeypatch.setattr(sync.os, "utime", real_utime)
    assert client.uploads[0][3] == 1_700_000_000  # Google's date for it, not today


@pytest.mark.parametrize("damage", ["deleted", "changed"])
def test_nothing_is_deleted_when_the_copy_here_is_gone(library, damage):
    google = {b64(DATA)}
    client = FakeClient(library, google)
    sync.reclaim_queue(ACC, ["BIG"])
    step(client)
    local = sync.Path(sync.q("SELECT local FROM reclaim WHERE media_key='BIG'")[0][0])
    if damage == "deleted":
        local.unlink()
    else:
        local.write_bytes(b"not the original any more")
    step(client)
    assert client.api.calls == []  # Google's copy is left alone
    assert sync.q("SELECT state, local FROM reclaim WHERE media_key='BIG'")[0] == ("queued", "")
    step(client)  # downloaded again, then replaced as usual
    step(client)
    assert client.api.calls[:2] == [("trash", [b64(DATA)]), ("delete", [b64(DATA)])]


def test_a_broken_download_leaves_no_part_file(library, monkeypatch):
    import requests

    class Broken(FakeResponse):
        def iter_content(self, n):
            yield DATA[:100]
            raise requests.ConnectionError("cut off")

    monkeypatch.setattr("requests.get", lambda url, **kw: Broken(DATA))
    sync.reclaim_queue(ACC, ["BIG"])
    step(FakeClient(library, set()))
    assert not list((sync.RECLAIM_DIR / ACC / "BIG").glob("*.part"))
    sync.reclaim_state.clear()


def test_free_only_videos_only_photos_or_both(library, server):
    with Storage(library) as st:
        st.update([item("CLIP", data=b"clip video", type=2), item("LOCKED", data=b"locked", is_locked=True)])
    r = sync.storage_report(ACC)
    assert r["kinds"] == {"photo": {"count": 2, "bytes": len(DATA) + 5}, "video": {"count": 1, "bytes": 10}}

    def queued():
        return sorted(k for (k,) in sync.q("SELECT media_key FROM reclaim WHERE state='queued'"))

    assert sync.reclaim_queue_all(ACC, "video") == {"queued": 1}
    assert queued() == ["CLIP"]
    assert sync.reclaim_queue_all(ACC, "photo") == {"queued": 2}
    assert queued() == ["BIG", "CLIP", "SMALL"]  # never shared, trashed, locked or free items
    assert sync.reclaim_queue_all(ACC, "all") == {"queued": 0}  # nothing queued twice

    req = urllib.request.Request(
        f"{server}/api/storage/download", json.dumps({"account": ACC, "kind": "sideways"}).encode()
    )
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(req)


def test_items_from_others_are_never_queued(library):
    assert sync.reclaim_queue(ACC, ["PARTNER", "TRASHED", "FREE"]) == {"queued": 0}


def test_activity_names_each_step(library):
    sync.sq("DELETE FROM events")
    client = FakeClient(library, {b64(DATA)})
    sync.reclaim_queue(ACC, ["BIG"])
    for _ in range(3):
        step(client)
    kinds = [k for (k,) in sync.sq("SELECT kind FROM events ORDER BY time")]
    assert kinds == ["downloaded", "deleted", "freed"]
    assert [i["kind"] for i in sync.activity("google")["items"]] == ["freed", "deleted", "downloaded"]
    assert not sync.activity("uploaded")["items"]  # not counted as new uploads


def test_keep_the_downloaded_copy(library, tmp_path, monkeypatch):
    monkeypatch.setattr(sync, "DOWNLOADS_DIR", tmp_path / "downloads")
    sync.q("DELETE FROM downloads")
    client = FakeClient(library, {b64(DATA)})
    sync.reclaim_queue(ACC, ["BIG"], keep=True)
    for _ in range(3):
        step(client)
    assert sync.q("SELECT state FROM reclaim WHERE media_key='BIG'")[0][0] == "done"
    r = sync.downloads_report()
    assert r["accounts"] == {ACC: {"photos": 1, "videos": 0, "bytes": len(DATA)}}
    item = r["items"][0]
    kept = sync.download_path(item["id"])
    assert kept.read_bytes() == DATA and kept.parent.parent.name == ACC
    assert not (sync.RECLAIM_DIR / ACC / "BIG").exists()  # the working copy is gone
    assert sync.downloads_delete(account=ACC) == {"deleted": 1}
    assert not kept.exists() and sync.downloads_report()["items"] == []


def test_copies_of_a_file_each_count_but_are_listed_once(library):
    # The same bytes three times, as separate items (same dedup, each charged): Google One counts
    # all three, and freeing deletes them together (by dedup key).
    same = b"album photo bytes" * 100
    dedup = b64(same)

    def copy(key, canonical, album):
        return MediaItem(
            media_key=key, file_name="LIB.jpg", dedup_key=dedup, is_canonical=canonical, type=1, caption=None,
            collection_id=album, size_bytes=len(same), quota_charged_bytes=len(same), origin="self",
            content_version=1, utc_timestamp=1_700_000_000_000, server_creation_timestamp=0,
        )  # fmt: skip

    with Storage(library) as st:
        st.update([copy("LIB", True, ""), copy("ALB1", False, "A1"), copy("ALB2", False, "A2")])
    r = sync.storage_report(ACC)
    # BIG + SMALL (from the fixture) + the photo, listed once with what all three copies cost.
    assert r["counted"] == 3
    assert r["counted_bytes"] == len(DATA) + 5 + 3 * len(same)
    lib = [i for i in r["items"] if i["name"] == "LIB.jpg"]
    assert [i["key"] for i in lib] == ["LIB"] and lib[0]["quota"] == 3 * len(same)  # the canonical copy
    assert "ALB1" not in [i["key"] for i in r["items"]]
    # Free all queues the photo once, by its canonical key.
    sync.q("DELETE FROM reclaim")
    assert sync.reclaim_queue_all(ACC, "all")["queued"] == 3
    queued = {k for (k,) in sync.q("SELECT media_key FROM reclaim")}
    assert "LIB" in queued and "ALB1" not in queued and "ALB2" not in queued
    assert sync.q("SELECT quota FROM reclaim WHERE media_key='LIB'")[0][0] == 3 * len(same)  # freed: all three


def test_an_item_gone_from_google_is_skipped_not_failed(library):
    import requests

    def gone(key):
        r = requests.Response()
        r.status_code = 404
        raise requests.HTTPError("404 Client Error: Not Found", response=r)

    sync.q("DELETE FROM reclaim")

    class Client:
        class api:
            get_download_urls = staticmethod(gone)
            bearer_token = "t"
            user_agent = "ua"

    sync.reclaim_queue(ACC, ["BIG"])
    assert step(Client())  # did work (handled the 404), no crash
    # The item is dropped from the queue, not left as a failure.
    assert sync.q("SELECT state FROM reclaim WHERE media_key='BIG'") == []


def test_filtering_by_kind_fills_the_list(library):
    # A video, plus the fixture's two photos (BIG, SMALL).
    with Storage(library) as st:
        st.update([item("CLIP", data=b"a clip", type=2)])
    photos = sync.storage_report(ACC, kind="photo")
    videos = sync.storage_report(ACC, kind="video")
    assert sorted(i["key"] for i in photos["items"]) == ["BIG", "SMALL"]
    assert [i["key"] for i in videos["items"]] == ["CLIP"]
    # Totals stay across all kinds, whichever filter is on.
    for r in (photos, videos):
        assert r["counted"] == 3
        assert r["kinds"]["photo"]["count"] == 2 and r["kinds"]["video"]["count"] == 1


def test_googles_edited_copy_is_accepted_and_replaced_with_its_own_checksum(library, monkeypatch):
    # Some videos download as a copy Google changed (same size, another SHA-1). Google lists that
    # copy's checksum next to the dedup key; it is accepted, and the re-upload uses its checksum.
    edited = b"google's edited copy" * 1000
    client = FakeClient(library, {b64(DATA)})
    digests = []
    upload = client._upload_file

    def record(path, digest, *a, **k):
        digests.append(digest)
        return upload(path, digest, *a, **k)

    client._upload_file = record
    client.api.get_download_urls = lambda key: {
        "1": {
            "2": {"13": {"1": hashlib.sha1(DATA).digest(), "2": hashlib.sha1(edited).digest()}},
            "5": {"3": {"5": f"https://video-downloads.googleusercontent.com/{key}"}},
        }
    }
    monkeypatch.setattr("requests.get", lambda url, **kw: FakeResponse(edited))
    sync.reclaim_queue(ACC, ["BIG"])
    step(client)
    state, local_hash = sync.q("SELECT state, local_hash FROM reclaim WHERE media_key='BIG'")[0]
    assert state == "downloaded" and local_hash == b64(edited)
    step(client)
    assert ("trash", [b64(DATA)]) in client.api.calls  # Google's item goes by its dedup key
    assert digests == [hashlib.sha1(edited).digest()]  # the file here goes up with its own checksum
    assert ("caption", b64(edited), "Goa") in client.api.calls  # details land on the new item
    assert sync.q("SELECT state FROM reclaim WHERE media_key='BIG'")[0][0] == "checking"


def test_a_download_matching_no_checksum_google_lists_is_still_refused(library, monkeypatch):
    client = FakeClient(library, set())
    client.api.get_download_urls = lambda key: {
        "1": {
            "2": {"13": {"1": hashlib.sha1(b"small").digest(), "2": hashlib.sha1(b"other").digest()}},
            "5": {"2": {"6": f"https://video-downloads.googleusercontent.com/{key}"}},
        }
    }
    sync.reclaim_queue(ACC, ["SMALL"])  # the fixture serves "tampered" bytes for it
    step(client)
    assert sync.q("SELECT state FROM reclaim WHERE media_key='SMALL'")[0][0] == "failed"


def test_several_items_move_at_once(library, monkeypatch):
    monkeypatch.setattr("requests.get", lambda url, **kw: FakeResponse(b"small" if "SMALL" in url else DATA))
    sync.update_settings({"reclaim": {"workers": 2}})
    try:
        sync.reclaim_queue(ACC, ["BIG", "SMALL"])
        step(FakeClient(library, {b64(DATA), b64(b"small")}))
        assert sorted(s for (s,) in sync.q("SELECT state FROM reclaim")) == ["downloaded", "downloaded"]
    finally:
        sync.update_settings({"reclaim": {"workers": 4}})


def test_workers_setting_is_kept_in_range():
    assert sync.update_settings({"reclaim": {"workers": 99}})["reclaim"]["workers"] == sync.RECLAIM_MAX_WORKERS
    assert sync.update_settings({"reclaim": {"workers": 0}})["reclaim"]["workers"] == 0  # dynamic
    assert sync.update_settings({"reclaim": {"workers": -3}})["reclaim"]["workers"] == 0
    assert sync.update_settings({"reclaim": {"workers": 4}})["reclaim"]["workers"] == 4


def test_paused_account_starts_nothing(library):
    sync.reclaim_queue(ACC, ["BIG"])
    sync.meta_set(f"reclaim_paused:{ACC}", "1")
    try:
        assert not step(FakeClient(library, {b64(DATA)}))
        assert sync.q("SELECT state FROM reclaim WHERE media_key='BIG'")[0][0] == "queued"
        assert sync.storage_report(ACC)["paused"] and sync.reclaim_summary()[ACC]["paused"]
    finally:
        sync.meta_set(f"reclaim_paused:{ACC}", "0")


def test_pause_everything_sets_each_jobs_pause(library):
    # Overview's Pause everything pauses every job on its own; the uploads pause alone doesn't.
    sync.reclaim_queue(ACC, ["BIG"])
    sync.PAUSE_FILE.touch()
    try:
        assert not sync.all_paused()
        sync.pause_all(True)
        assert sync.all_paused() and sync.reclaim_paused(ACC) and sync.meta_get("copy_paused") == "1"
        assert not step(FakeClient(library, {b64(DATA)}))
        assert sync.q("SELECT state FROM reclaim WHERE media_key='BIG'")[0][0] == "queued"
    finally:
        sync.pause_all(False)
    assert not sync.PAUSE_FILE.exists() and not sync.reclaim_paused(ACC)
    assert step(FakeClient(library, {b64(DATA)}))


def test_pause_from_1_5_8_is_kept_per_job(library):
    sync.q("DELETE FROM meta WHERE k='pause_split'")
    sync.PAUSE_FILE.touch()
    try:
        sync.split_pause()
        assert sync.reclaim_paused(ACC) and sync.meta_get("copy_paused") == "1"
    finally:
        sync.pause_all(False)
    sync.PAUSE_FILE.touch()
    sync.split_pause()  # only once
    assert not sync.reclaim_paused(ACC)
    sync.PAUSE_FILE.unlink()


def test_no_caption_is_not_copied_as_braces(library):
    # gpmc lists an item without a caption as "{}": it must never become a caption in Google.
    with Storage(library) as st:
        st.update([item("BIG", is_favorite=True, caption="{}")])
    client = FakeClient(library, {b64(DATA)})
    sync.reclaim_queue(ACC, ["BIG"])
    assert sync.q("SELECT caption FROM reclaim WHERE media_key='BIG'")[0][0] == ""
    step(client)
    step(client)
    assert not [c for c in client.api.calls if c[0] == "caption"]


def test_braces_set_by_older_versions_are_cleared_once(library):
    sync.reclaim_queue(ACC, ["BIG"])
    sync.q("UPDATE reclaim SET state='done', caption='{}' WHERE media_key='BIG'")
    client = FakeClient(library, set())
    assert sync.caption_repair_step(ACC, lambda a: client)
    assert ("caption", b64(DATA), "") in client.api.calls
    assert not sync.caption_repair_step(ACC, lambda a: client)  # only once


def test_freed_report_counts_time_and_days(library):
    client = FakeClient(library, {b64(DATA)})
    sync.reclaim_queue(ACC, ["BIG"])
    for _ in range(3):
        step(client)
    r = sync.freed_report(ACC)
    assert r["count"] == 1 and r["bytes"] == len(DATA)
    assert r["kinds"]["photo"] == {"count": 1, "bytes": len(DATA)}
    assert r["timed"] == 1 and r["seconds"] >= 0
    assert len(r["days"]) == 1 and r["days"][0]["bytes"] == len(DATA)
    assert r["items"][0]["key"] == "NEW" and r["items"][0]["old_key"] == "BIG"


def test_pause_stops_a_download_in_progress(library, monkeypatch):
    # Pausing doesn't wait for big downloads to finish: they stop, and the item stays queued.
    clock = iter(range(0, 10**6, 2))
    monkeypatch.setattr(sync.time, "monotonic", lambda: next(clock))

    class Halfway(FakeResponse):
        def iter_content(self, n):
            yield DATA[:100]
            sync.meta_set(f"reclaim_paused:{ACC}", "1")  # the user presses pause now
            yield DATA[100:]

    monkeypatch.setattr("requests.get", lambda url, **kw: Halfway(DATA))
    client = FakeClient(library, {b64(DATA)})
    sync.reclaim_queue(ACC, ["BIG"])
    try:
        assert not step(client)
        assert sync.q("SELECT state, local FROM reclaim WHERE media_key='BIG'")[0] == ("queued", None)
        assert not list((sync.RECLAIM_DIR / ACC / "BIG").glob("*"))  # no half-downloaded file left
        assert client.api.calls == []  # nothing touched in Google
        assert sync.reclaim_state[ACC]["jobs"] == {}
    finally:
        sync.meta_set(f"reclaim_paused:{ACC}", "0")


def test_diagnostics_list_items_being_moved(library):
    sync.reclaim_state[ACC]["jobs"]["K"] = {"current": {"key": "K", "step": "downloading", "done": 1, "total": 2},
                                            "stop": lambda: False}  # fmt: skip
    try:
        d = sync.diagnostics(1)
        json.dumps(d)  # nothing in it that can't be written out
        assert d["workers"]["free_storage"][ACC]["working"][0]["key"] == "K"
    finally:
        sync.reclaim_state.clear()


def test_report_says_how_many_gp_sync_uploads_went_to_the_account(library):
    sync.q("DELETE FROM files")
    assert sync.storage_report(ACC)["ours"]["uploaded"] == 0
    sync.q("INSERT INTO files (path, status, account, sha1) VALUES ('x', 'uploaded', ?, 'ab')", (ACC,))
    assert sync.storage_report(ACC)["ours"]["uploaded"] == 1


def test_report_shows_what_is_left_and_the_speed(library):
    sync.reclaim_queue(ACC, ["BIG", "SMALL"])
    left = sync.storage_report(ACC)["left"]
    assert left["count"] == 2 and left["bytes"] == len(DATA) + 5 and left["rate"] == 0
    now = sync.time.time()
    sync.q("UPDATE reclaim SET state='done', started=?, finished=? WHERE media_key='SMALL'", (now - 10, now))
    left = sync.storage_report(ACC)["left"]
    assert left["count"] == 1 and left["rate"] == pytest.approx(0.5)  # 5 bytes in 10 seconds


def test_time_taken_counts_items_moving_at_once_only_once():
    # Two items side by side for 60 s, then a gap, then one for 30 s: 90 s of running, not 150.
    assert sync._active_seconds([(0, 60), (0, 60), (100, 130)]) == 90
    assert sync._active_seconds([(0, 60), (30, 90)]) == 90
    assert sync._active_seconds([]) == 0


def test_freed_report_times_each_step(library):
    client = FakeClient(library, {b64(DATA)})
    sync.reclaim_queue(ACC, ["BIG"])
    for _ in range(3):
        step(client)
    got, sent, started, finished = sync.q(
        "SELECT downloaded_at, uploaded_at, started, finished FROM reclaim WHERE media_key='BIG'"
    )[0]
    assert started <= got <= sent <= finished
    t = sync.freed_report(ACC)
    assert t["active"] == pytest.approx(finished - started)
    assert t["timing"]["phased"] == 1 and t["timing"]["phases"]["download"] is not None
    assert t["timing"]["slowest"][0]["name"] == "BIG.jpg"


def test_more_than_eight_at_once_saved_earlier_is_kept_within_eight():
    sync.meta_set("settings", '{"reclaim": {"workers": 16}, "upload_workers": 9}')
    st = sync.get_settings()
    assert st["reclaim"]["workers"] == 8 and st["upload_workers"] == 8
    sync.q("DELETE FROM meta WHERE k='settings'")


# ---- the checker runs apart, and copies already on this server are used first


def test_new_items_start_while_the_checker_waits_for_google(library):
    client = FakeClient(library, {b64(DATA)})
    sync.reclaim_queue(ACC, ["BIG"])
    for _ in range(2):
        step(client)  # downloaded, then uploaded again: waits to be confirmed
    assert sync.q("SELECT state FROM reclaim WHERE media_key='BIG'")[0][0] == "checking"

    def slow_read(show_progress=True):
        raise AssertionError("starting items must not read the library")

    client.update_cache = slow_read
    sync.reclaim_queue(ACC, ["SMALL"])
    sync.reclaim_step(lambda a: client)  # SMALL starts (its fake download fails) without a library read
    assert sync.q("SELECT state FROM reclaim WHERE media_key='SMALL'")[0][0] == "failed"


def test_checker_waits_for_google_to_list_a_fresh_upload(library):
    client = FakeClient(library, {b64(DATA)})
    sync.reclaim_queue(ACC, ["BIG"])
    for _ in range(2):
        step(client)
    sync.check_state.clear()
    assert not sync.check_step(ACC, lambda a: client)  # uploaded a moment ago: not read yet
    later = sync.time.time() + sync.CHECK_SETTLE + 1
    assert sync.check_step(ACC, lambda a: client, now=later)
    assert sync.q("SELECT state FROM reclaim WHERE media_key='BIG'")[0][0] == "done"
    assert not sync.check_step(ACC, lambda a: client, now=later + 5)  # nothing left to check
    assert sync.storage_report(ACC)["check"]["waiting"] == 0


def _on_this_server(tmp_path, data, name="IMG_1.jpg", account="other@example.com"):
    original = tmp_path / "media" / name
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(data)
    sync.os.utime(original, (1_600_000_000, 1_600_000_000))
    sync.q("INSERT OR REPLACE INTO files (path, size, status, account, sha1, updated) VALUES (?,?,?,?,?,?)",
           (str(original), len(data), "uploaded", account, hashlib.sha1(data).hexdigest(), 1))  # fmt: skip
    return original


def test_uses_the_copy_on_this_server_instead_of_downloading(library, tmp_path, monkeypatch):
    original = _on_this_server(tmp_path, DATA)

    def no_download(url, **kw):
        raise AssertionError("downloaded from Google although the file is here")

    monkeypatch.setattr("requests.get", no_download)
    client = FakeClient(library, {b64(DATA)})
    sync.reclaim_queue(ACC, ["BIG"])
    for _ in range(3):
        step(client)
    assert sync.q("SELECT state FROM reclaim WHERE media_key='BIG'")[0][0] == "done"
    assert client.uploads[0][0] == DATA
    # The original is untouched: still there, same bytes, same time.
    assert original.read_bytes() == DATA and original.stat().st_mtime == 1_600_000_000


def test_a_changed_copy_on_this_server_is_not_used(library, tmp_path):
    _on_this_server(tmp_path, DATA)
    (tmp_path / "media" / "IMG_1.jpg").write_bytes(b"x" * len(DATA))  # changed since its fingerprint
    client = FakeClient(library, {b64(DATA)})
    sync.reclaim_queue(ACC, ["BIG"])
    step(client)  # downloaded from Google instead
    state, local = sync.q("SELECT state, local FROM reclaim WHERE media_key='BIG'")[0]
    assert state == "downloaded" and sync.Path(local).read_bytes() == DATA


def test_each_account_reports_what_still_counts(library):
    s = sync.storage_report(ACC)["summary"][ACC]
    # BIG, SMALL and the item in the Trash (Google counts it until the Trash is emptied), as Google
    # One shows it; free and partner items don't count.
    assert s["counted"] == len(DATA) + 5 + 5
    with Storage(library) as st:
        st.update([item("BIG", quota=0), item("SMALL", data=b"small", quota=0)])
    sync.meta_set(f"library_refreshed:{ACC}", "2")  # as every library read does
    assert sync.storage_summary()[ACC]["counted"] == 5  # read again after the library changed


def test_an_unread_library_reports_nothing_counted(tmp_path, monkeypatch, media):
    monkeypatch.setattr(sync, "GPMC_HOME", tmp_path / "none")
    assert sync.storage_summary()[ACC]["counted"] is None


def test_auto_free_queues_new_phone_backups_only(library, monkeypatch):
    """Auto: new items that count against storage are queued after they settle; items Free storage
    handled before, and new ones still settling, are left alone. Off: nothing is queued."""
    client = FakeClient(library, {b64(DATA)})
    client.update_cache = lambda show_progress=False: None
    monkeypatch.setattr(sync, "_auto_free_at", {})
    now = 1_800_000_000
    assert not sync.auto_free_step(ACC, lambda a: client, now=now)  # off by default
    sync.update_settings({"reclaim": {"auto": True}})
    try:
        sync.q("INSERT INTO reclaim (account, media_key, state) VALUES (?, 'SMALL', 'failed')", (ACC,))
        assert sync.auto_free_step(ACC, lambda a: client, now=now)
        assert dict(sync.q("SELECT media_key, state FROM reclaim WHERE account=?", (ACC,))) == {
            "BIG": "queued",  # new: queued
            "SMALL": "failed",  # handled before: left as it is
        }
        assert not sync.auto_free_step(ACC, lambda a: client, now=now + 60)  # waits between looks
        with Storage(library) as st:  # a phone backup a minute ago: still settling
            st.update([item("NEW", data=b"new", server_creation_timestamp=(now + 16 * 60 - 60) * 1000)])
        assert not sync.auto_free_step(ACC, lambda a: client, now=now + 16 * 60)
        sync.meta_set(f"reclaim_paused:{ACC}", "1")
        assert not sync.auto_free_step(ACC, lambda a: client, now=now + 60 * 60)  # paused: nothing
    finally:
        sync.meta_set(f"reclaim_paused:{ACC}", "0")
        sync.update_settings({"reclaim": {"auto": False}})


def test_a_download_cut_off_by_the_network_is_tried_again():
    """A connection that breaks mid-download is the network's fault, not the item's: it stays queued."""
    import requests

    assert "No connection" in sync.outage_kind(requests.exceptions.ChunkedEncodingError("IncompleteRead"))


def test_one_trip_to_google_makes_both_thumbnail_sizes(library, monkeypatch):
    """Google is asked once per item, for the sharp size; the usual size is made from it. Warming
    fetches the first screen ahead of the browser, and skips what is here already."""
    import io

    from gpmc.api import Api
    from PIL import Image

    calls = []

    def fake(self, key, width=None):
        calls.append((key, width))
        buf = io.BytesIO()
        Image.new("RGB", (1280, 960), "green").save(buf, "JPEG")
        return buf.getvalue()

    monkeypatch.setattr(Api, "get_thumbnail", fake)
    monkeypatch.setattr(sync, "THUMBS", sync.RECLAIM_DIR.parent / "thumbs")
    name = f"{ACC}\nBIG"
    small = sync.google_thumb(name)
    big = sync.google_thumb(name, sync.media.THUMB_BIG)
    assert calls == [("BIG", 640)]
    assert max(Image.open(small).size) == 320 and max(Image.open(big).size) == 640
    assert sync.warm_google_thumbs(ACC, ["BIG", "SMALL"], wait=True) == 1  # BIG is here already
    assert calls == [("BIG", 640), ("SMALL", 640)]


class KeyedApi(FakeApi):
    """Google that answers a checksum with the item that has it, as the real one does."""

    def __init__(self, google, keys):
        super().__init__(google)
        self.keys = keys  # dedup key -> media key

    def find_remote_media_by_hash(self, digest):
        d = sync._b64_hash(digest)
        return self.keys.get(d) if d in self.google else None


def test_an_earlier_free_upload_is_not_taken_for_the_original(library, monkeypatch):
    """Google served an edited copy; an earlier try deleted the original and uploaded the copy free,
    then stopped before saving that. The copy's checksum now finds GP Sync's own upload: that isn't
    the original still in Google (which looped on "hasn't deleted it yet"), it's the upload, checked."""
    monkeypatch.setattr(sync.time, "sleep", lambda s: None)
    edited = b"google's edited copy"
    folder = sync.RECLAIM_DIR / ACC / "BIG"
    folder.mkdir(parents=True)
    (folder / "BIG.jpg").write_bytes(edited)
    with Storage(library) as st:
        st.update([item("UP", data=edited, quota=0)])  # the earlier free upload
    sync.reclaim_queue(ACC, ["BIG"])
    sync._reclaim_checked.clear()
    sync.q("UPDATE reclaim SET state='in_google', local=?, local_hash=? WHERE media_key='BIG'",
           (str(folder / "BIG.jpg"), b64(edited)))  # fmt: skip
    client = FakeClient(library, {b64(edited)})  # the original (b64(DATA)) is gone from Google
    client.api = KeyedApi(client.api.google, {b64(edited): "UP"})
    sync.reclaim_step(lambda a: client)
    state, new_key = sync.q("SELECT state, new_key FROM reclaim WHERE media_key='BIG'")[0]
    assert (state, new_key) == ("checking", "UP")
    assert client.uploads == [] and not [c for c in client.api.calls if c[0] in ("trash", "delete")]


def test_the_original_and_an_earlier_upload_both_there(library, monkeypatch):
    """The original is still in Google and so is an earlier free upload of the edited copy: the
    original is deleted (that upload doesn't make it look undeleted), and the upload stands in."""
    monkeypatch.setattr(sync.time, "sleep", lambda s: None)
    edited = b"google's edited copy"
    folder = sync.RECLAIM_DIR / ACC / "BIG"
    folder.mkdir(parents=True)
    (folder / "BIG.jpg").write_bytes(edited)
    sync.reclaim_queue(ACC, ["BIG"])
    sync.q("UPDATE reclaim SET state='downloaded', local=?, local_hash=? WHERE media_key='BIG'",
           (str(folder / "BIG.jpg"), b64(edited)))  # fmt: skip
    client = FakeClient(library, {b64(DATA), b64(edited)})
    client.api = KeyedApi(client.api.google, {b64(DATA): "BIG", b64(edited): "UP"})
    sync._reclaim_checked.clear()
    sync.reclaim_step(lambda a: client)
    state, new_key = sync.q("SELECT state, new_key FROM reclaim WHERE media_key='BIG'")[0]
    assert (state, new_key) == ("checking", "UP") and client.uploads == []
    # Deleted from the Trash twice: Google can miss the first while the item is still on its way there.
    assert [c for c in client.api.calls if c[0] == "delete"] == [("delete", [b64(DATA)])] * 2


def test_freed_originals_left_in_the_trash_are_emptied(library):
    """A freed original that stayed in Google's Trash still counts: it's deleted for good once its
    free copy (other bytes) is in the library. One whose copy has the same checksum is left alone,
    and each is asked at most once an hour."""
    free = b"free copy, other bytes"
    with Storage(library) as st:
        st.update([
            item("OLDBIG", trash_timestamp=5),  # the original, in the Trash
            item("NEWBIG", data=free, quota=0),  # its free copy
            item("OLDSMALL", data=b"small", trash_timestamp=5),
            item("NEWSMALL", data=b"small", quota=0),  # same bytes: deleting by checksum could hit it
        ])  # fmt: skip
    for key, dedup, new in (("OLDBIG", b64(DATA), "NEWBIG"), ("OLDSMALL", b64(b"small"), "NEWSMALL")):
        sync.q("INSERT INTO reclaim (account, media_key, dedup_key, name, state, new_key, updated) "
               "VALUES (?, ?, ?, ?, 'done', ?, 0)", (ACC, key, dedup, key, new))  # fmt: skip
    sync._trash_tried.clear()
    client = FakeClient(library, set())
    assert sync.empty_freed_trash(client, ACC, now=1000) == 1
    assert client.api.calls == [("delete", [b64(DATA)])]
    assert sync.empty_freed_trash(client, ACC, now=1000 + 60) == 0  # not again within the hour
    assert sync.empty_freed_trash(client, ACC, now=1000 + sync.TRASH_RETRY) == 1
    # And Using storage counts the Trash, as Google One does.
    r = sync.storage_report(ACC)
    assert r["trash"] == {"count": 3, "bytes": len(DATA) + 5 + 5}  # with the fixture's TRASHED
