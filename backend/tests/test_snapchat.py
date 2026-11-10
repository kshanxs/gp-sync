"""Snapchat memories: the export list is read, each memory is looked for in Google Photos (same file,
same moment, looks the same), and only what's missing or wrongly dated is uploaded. Every memory ends
up in the Snapchat album."""

import hashlib
import io
import json
import os
import urllib.request

import pytest
from gpmc.db import Storage
from gpmc.models import MediaItem
from helpers import post
from test_dates import ACC, DAY, GpmcLike

import snapchat
import sync

T = 1_700_000_000  # the moment of the first memory


def picture(seed, size=(90, 160), quality=90):
    """A JPEG that looks different for every seed (and the same when only size or quality change)."""
    import random

    from PIL import Image

    rnd = random.Random(seed)
    im = Image.new("RGB", (9, 16))
    im.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)) for _ in range(9 * 16)])
    im = im.resize((90, 160), Image.Resampling.BICUBIC).resize(size)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def marked(data, box):
    """The same picture with a white block drawn on (a sticker): it looks less alike."""
    from PIL import Image, ImageDraw

    im = Image.open(io.BytesIO(data)).convert("RGB")
    ImageDraw.Draw(im).rectangle(box, fill=(255, 255, 255))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80)
    return buf.getvalue()


def put(name, data, mtime=T):
    p = sync.MEDIA / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    os.utime(p, (mtime, mtime))
    return p


def sha(data):
    return hashlib.sha1(data).hexdigest()


def item(key, data, when, kind=1, w=90, h=160, **kw):
    return MediaItem(
        media_key=key, file_name=f"{key}.jpg", dedup_key=sync._b64_hash(hashlib.sha1(data).digest()),
        is_canonical=True, type=kind, caption=kw.pop("caption", None), collection_id="", size_bytes=len(data),
        quota_charged_bytes=0, origin="self", content_version=1, utc_timestamp=int(when * 1000),
        server_creation_timestamp=0, width=w, height=h, **kw,
    )  # fmt: skip


class Google(GpmcLike):
    """GpmcLike with thumbnails: Google's copy of each item, as bytes."""

    def __init__(self, google=(), thumbs=None):
        super().__init__(google)
        self.api.get_thumbnail = lambda key, width=None: (thumbs or {})[key]


def manifest(folder, items, export=None):
    data = {
        "format": snapchat.FORMAT,
        "version": 1,
        "export": export or {"memories": len(items) + 2, "found": len(items), "missing": 2},
        "items": items,
    }
    (sync.MEDIA / folder / snapchat.MANIFEST).write_text(json.dumps(data))


@pytest.fixture
def snaps(media, tmp_path, monkeypatch):
    for table in ("snap_items", "snap_copies", "snap_prints", "snap_local", "albums", "copies", "downloads"):
        sync.q(f"DELETE FROM {table}")
    sync.q("DELETE FROM meta WHERE k LIKE 'snap%' OR k LIKE 'library_refreshed%'")
    sync.sq("DELETE FROM provenance")
    sync.sq("DELETE FROM media_dates")
    sync._snap.update(paths=frozenset(), skip=frozenset())
    sync._snap_dirty.clear()
    sync._snap_tried.clear()
    sync.snapchat_state.clear()
    monkeypatch.setattr(sync, "GPMC_HOME", tmp_path / "gpmc")
    monkeypatch.setattr(snapchat, "SHAPE_MEMORIES", 1)  # a handful of test memories still get searched by look
    monkeypatch.setattr(sync, "SNAP_PRINT_GAP", 0)
    monkeypatch.setitem(sync.state, "phase", "idle")
    for p in list(sync.MEDIA.rglob("*")):
        if p.is_file():
            p.unlink()
    return sync.MEDIA


def library(*items):
    db = sync._library_db(ACC)
    db.parent.mkdir(parents=True, exist_ok=True)
    with Storage(db) as st:
        st.update(list(items))
    sync.meta_set(f"library_refreshed:{ACC}", "10")


def run(step, client, limit=50):
    for _ in range(limit):
        if not step(ACC, lambda _a: client):
            return
    raise AssertionError("never finished")


# ---- pure parts


def test_manifest_keeps_paths_inside_its_folder(tmp_path):
    (tmp_path / snapchat.MANIFEST).write_text(
        json.dumps(
            {
                "format": snapchat.FORMAT,
                "items": [
                    {"file": "2024/a.jpg", "id": "a", "kind": "photo", "taken": T, "poster": "../../etc/x"},
                    {"file": "../outside.jpg", "id": "b", "kind": "photo", "taken": T},
                    {"file": "/etc/passwd", "id": "c", "kind": "photo", "taken": T},
                    {"id": "no file"},
                ],
            }
        )
    )
    data = snapchat.read_manifest(tmp_path / snapchat.MANIFEST)
    assert [i["file"] for i in data["items"]] == [tmp_path / "2024" / "a.jpg"]
    assert data["items"][0]["poster"] is None
    (tmp_path / snapchat.MANIFEST).write_text('{"format": "something else"}')
    with pytest.raises(snapchat.BadManifest):
        snapchat.read_manifest(tmp_path / snapchat.MANIFEST)


def test_fingerprint_survives_resizing_but_not_another_picture():
    a = snapchat.dhash_bytes(picture(1))
    small = snapchat.dhash_bytes(picture(1, size=(45, 80), quality=40))
    other = snapchat.dhash_bytes(picture(2))
    assert snapchat.distance(a, small) <= snapchat.LOOKS_SAME
    assert snapchat.distance(a, other) > snapchat.LOOKS_SAME


def mem(path, taken, kind="photo", dedups=(), dhash=""):
    return {"path": path, "kind": kind, "taken": taken, "dedups": list(dedups), "dhash": dhash, "width": 9,
            "height": 16}  # fmt: skip


def keys(found, path):
    return [c["key"] for c in found[path]["copies"]]


def test_match_prefers_exact_then_moment_then_looks():
    fp = snapchat.dhash_bytes(picture(1))
    memories = [
        mem("same", T, dedups=["D1"]),
        mem("moment", T + 100, kind="video"),
        mem("looks", T + 200, dhash=fp),
        mem("twin", T + 300, dhash=snapchat.dhash_bytes(picture(2))),
        mem("none", T + 400),
    ]
    lib = [
        ("K1", "D1", 1, T + 50 * DAY, 9, 16),
        ("K2", "D2", 1, T + 101, 9, 16),  # a photo: not the video taken then
        ("K3", "D3", 2, T + 102, 9, 16),
        ("K4", "D4", 1, T + 90 * DAY, 9, 16),
        ("K5", "D5", 1, T + 300, 16, 9),  # the same moment, either way up
    ]
    found = snapchat.match(memories, lib, {"K4": fp})
    assert found["same"]["match"] == "same_file" and keys(found, "same") == ["K1"]
    assert found["moment"]["match"] == "same_moment" and keys(found, "moment") == ["K3"]
    assert found["looks"]["match"] == "looks_same" and keys(found, "looks") == ["K4"]
    assert found["twin"]["match"] == "same_moment" and keys(found, "twin") == ["K5"]
    assert found["none"] == {"match": "none", "copies": [], "local": None}
    assert snapchat.action_for(found["moment"], T + 100, 120) == "album"
    assert snapchat.action_for(found["same"], T, 120) == "replace"  # Google dates it 50 days later
    assert snapchat.action_for(found["none"], T, 120) == "upload"


def test_every_copy_is_found_but_only_close_ones_count_as_duplicates():
    a = picture(1)
    fp = snapchat.dhash_bytes(a)
    near = snapchat.dhash_bytes(picture(1, size=(45, 80), quality=40))
    far = f"{int(fp, 16) ^ ((1 << 30) - 1):064x}"  # 30 bits off: a match alone, not a further copy
    lib = [
        ("MOMENT", "d1", 1, T + 1, 9, 16),  # backed up by the phone at the moment
        ("DUP", "d2", 1, T + 200 * DAY, 9, 16),  # the same picture uploaded again later
        ("LOOSE", "d3", 1, T + 300 * DAY, 9, 16),
        ("NEXT", "d4", 1, T + 2, 9, 16),  # the next memory, saved a second later
    ]
    found = snapchat.match([mem("a", T, dhash=fp), mem("b", T + 2)], lib, {"DUP": near, "LOOSE": far})
    assert keys(found, "a") == ["MOMENT", "DUP"]
    assert keys(found, "b") == ["NEXT"]  # two snaps a second apart keep their own copies


def test_plan_keeps_one_copy_and_never_deletes_camera_photos():
    later = {"key": "L", "taken": T + 30 * DAY}
    right = {"key": "R", "taken": T + 1}
    older = {"key": "O", "taken": T - 30 * DAY}
    camera = {"key": "C", "taken": T + 40 * DAY, "original": True}
    keep, drop = snapchat.plan("album", [later, right], T, 120)
    assert keep is right and drop == [later]
    keep, drop = snapchat.plan("album", [later, right, older], T, 120)
    assert keep is older and drop == [later, right]  # a camera-roll photo saved to Snapchat later
    keep, drop = snapchat.plan("replace", [later, camera], T, 120)
    assert keep is None and drop == [later]
    assert snapchat.plan("skip", [later], T, 120) == (None, [])
    assert snapchat.action_for({"copies": [later]}, T, 120) == "replace"
    assert snapchat.action_for({"copies": [later, camera]}, T, 120, {"C"}) == "album"
    assert snapchat.action_for({"copies": [older]}, T, 120) == "album"


def test_a_right_date_copy_stays_even_without_the_place():
    """It stays in the user's own albums (they can't be read, so a new upload would leave them)."""
    right = {"key": "R", "taken": T + 1, "how": "same_moment"}
    assert snapchat.action_for({"copies": [right]}, T, 120) == "album"


def test_one_google_item_is_one_memory():
    fp = snapchat.dhash_bytes(picture(3))
    found = snapchat.match([mem("a", T, dhash=fp), mem("b", T + 1, dhash=fp)], [("K", "D", 1, T + DAY, 9, 16)],
                           {"K": fp})  # fmt: skip
    assert sorted(f["match"] for f in found.values()) == ["looks_same", "none"]


def test_thumbs_wanted_only_same_kind_shape_and_not_older(monkeypatch):
    monkeypatch.setattr(snapchat, "SHAPE_MEMORIES", 1)
    memories = [{"path": "m", "kind": "photo", "taken": T, "dedups": [], "dhash": "x", "width": 9, "height": 16}]
    lib = [
        ("new", "a", 1, T + DAY, 90, 160),
        ("old", "b", 1, T - 10 * DAY, 90, 160),
        ("video", "c", 2, T + DAY, 90, 160),
        ("square", "d", 1, T + DAY, 100, 100),
        ("nosize", "e", 1, T + DAY, 0, 0),
    ]
    assert snapchat.thumbs_wanted(memories, lib, {}) == ["new", "nosize"]
    found = {"m": {"copies": [{"key": "new"}]}}  # already a copy: not fingerprinted again
    assert snapchat.thumbs_wanted(memories, lib, found) == ["nosize"]


def test_rare_shapes_dont_pull_in_the_library_and_camera_items_come_last():
    snaps = [mem(f"s{i}", T) for i in range(5)]  # five 9:16 memories
    odd = [{**mem("odd", T), "width": 3, "height": 4}]  # one 4:3 memory
    lib = [
        ("cam169", "a", 1, T + 2 * DAY, 90, 160, "Apple"),
        ("roll169", "b", 1, T + DAY, 90, 160, None),
        ("cam43", "c", 1, T + DAY, 300, 400, "Apple"),  # an ordinary camera photo
    ]
    assert snapchat.thumbs_wanted(snaps + odd, lib, {}) == ["roll169", "cam169"]


def test_google_saying_slow_down_pauses_and_keeps_what_came(snaps):
    mem_ = picture(13)
    put("Photos/Snapchat/x.jpg", mem_, T)
    manifest("Photos/Snapchat", [{"file": "x.jpg", "id": "x", "kind": "photo", "taken": T, "sha1": sha(mem_),
                                  "width": 90, "height": 160}])  # fmt: skip
    library(item("K1", picture(1), T + DAY), item("K2", picture(2), T + 2 * DAY))
    calls = []

    def thumb(key, width=None):
        calls.append(key)
        if key == "K1":
            import requests

            busy = requests.Response()
            busy.status_code = 429
            raise requests.HTTPError("429 Client Error: Too Many Requests", response=busy)
        return picture(2)

    client = Google()
    client.api.get_thumbnail = thumb
    sync.scan()
    for _ in range(5):
        sync.snapchat_match_step(ACC, lambda _a: client)
    track = sync.snapchat_state[ACC]
    assert track["slow_until"] > sync.time.time() + 500 and track["batch"] == sync.SNAP_PRINTS_PER_STEP // 2
    assert dict(sync.q("SELECT media_key, dhash FROM snap_prints")).keys() == {"K2"}  # kept
    assert sorted(calls) == ["K1", "K2"]  # waiting: no more calls
    assert sync.snapchat_report(ACC)["slow_until"]


# ---- the whole round in GP Sync


def test_memories_are_found_matched_and_sent(snaps, server, monkeypatch):
    album_mem = picture(1)
    moved_mem = picture(2)
    new_video = b"\0\0\0\x18ftypmp42 a snapchat video"
    poster = picture(3)
    local_mem = picture(4)
    exact_mem = picture(5)
    put("Photos/Snapchat/2023/a.jpg", album_mem, T)
    put("Photos/Snapchat/2023/b.jpg", moved_mem, T + DAY)
    put("Photos/Snapchat/2023/c.mp4", new_video, T + 2 * DAY)
    put("Photos/Snapchat/.posters/c.jpg", poster)
    put("Photos/Snapchat/2023/d.jpg", local_mem, T + 3 * DAY)
    put("Photos/Snapchat/2023/e.jpg", exact_mem, T + 4 * DAY)
    entries = [
        ("2023/a.jpg", album_mem, T, None),
        ("2023/b.jpg", moved_mem, T + DAY, None),
        ("2023/c.mp4", new_video, T + 2 * DAY, ".posters/c.jpg"),
        ("2023/d.jpg", local_mem, T + 3 * DAY, None),
        ("2023/e.jpg", exact_mem, T + 4 * DAY, None),
    ]
    manifest(
        "Photos/Snapchat",
        [
            {"file": f, "id": f, "kind": "video" if f.endswith("mp4") else "photo", "taken": t, "lat": 30.1,
             "lon": 77.2, "sha1": sha(data), "orig_sha1": "", "width": 90, "height": 160, "poster": p}
            for f, data, t, p in entries
        ],
    )  # fmt: skip
    # On this server: the camera-roll copy of b (in Google, dated when it was backed up) and a copy
    # of d that isn't in Google yet. Both were labelled as saved from an app.
    roll_b = picture(2, size=(72, 128), quality=60)
    roll_d = picture(4, size=(72, 128), quality=60)
    local_b = put("Photos/iPhone/roll_b.jpg", roll_b, T + 400 * DAY)
    local_d = put("Photos/iPhone/roll_d.jpg", roll_d, T + 400 * DAY)
    for p, data in ((local_b, roll_b), (local_d, roll_d)):
        sync.sq("INSERT INTO provenance (path, size, mtime, source) VALUES (?,?,?,'app_export')",
                (str(p), len(data), p.stat().st_mtime))  # fmt: skip
    dup_a = picture(1, size=(80, 142), quality=70)  # a uploaded again, later
    dup_b = marked(picture(2), (0, 0, 40, 50))  # b again, later, with a sticker on: a less certain match
    library(
        item("KA", picture(1, quality=50), T + 1),  # saved to the camera roll at the same moment
        item("KA2", dup_a, T + 200 * DAY, is_favorite=True),
        item("KB", roll_b, T + 400 * DAY, is_favorite=True, caption="Goa"),  # a copy, wrongly dated
        item("KB2", dup_b, T + 500 * DAY),
        item("KE", exact_mem, T + 4 * DAY),  # the very same file
        item("KX", picture(9), T + 5 * DAY),  # something else
    )
    in_google = {hashlib.sha1(d).digest() for d in (roll_b, exact_mem, dup_a, dup_b, picture(1, quality=50))}
    client = Google(google=in_google, thumbs={"KB": roll_b, "KX": picture(9), "KA": picture(1, quality=50),
                                             "KA2": dup_a, "KB2": dup_b})  # fmt: skip
    kept = []

    def download(client, account, key, dedup, name, size, taken, folder=None, track=None):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{key}.jpg").write_bytes(b"google's copy")
        kept.append(key)
        return folder / f"{key}.jpg", dedup

    monkeypatch.setattr(sync, "_download", download)

    todo = {p for p, *_ in sync.scan()}
    snap_files = {str(sync.MEDIA / "Photos/Snapchat" / e[0]) for e in entries}
    assert not todo & snap_files  # memories never go up as they are
    assert str(local_d) in todo  # not known as a copy yet

    # Nothing in Google is touched before the user starts it.
    with pytest.raises(ValueError):
        sync.snapchat_start(ACC)
    run(sync.snapchat_match_step, client)
    # The thumbnails fetched to compare pictures are kept: the page shows them without asking Google.
    assert sync._small_thumb_path(ACC, "KB").exists()
    from unittest import mock

    with mock.patch.object(sync, "accounts", lambda: [(ACC, "auth")]):
        assert sync.google_thumb_small(f"{ACC}\nKB") == sync._small_thumb_path(ACC, "KB")
    assert client.api.calls == [] and client.api.sent == []
    r = sync.snapchat_report(ACC)
    assert r["account"] == ACC and r["checked"] == 5 and not r["searching"]
    assert {k: v["total"] for k, v in r["counts"].items()} == {"album": 2, "replace": 1, "upload": 2, "skip": 0}
    assert r["matches"] == {"same_moment": 1, "looks_same": 1, "none": 1, "local": 1, "same_file": 1}
    assert r["manifests"][0]["export"]["missing"] == 2
    assert r["duplicates"] == {"total": 3, "deleted": 0}  # KA2 (a keeps KA), and KB and KB2 (b replaces them)
    by = {i["name"]: i for i in r["items"]}
    assert by["Photos/Snapchat/2023/b.jpg"]["google"]["taken"] == T + 400 * DAY
    roles = {c["key"]: c["role"] for c in by["Photos/Snapchat/2023/a.jpg"]["copies"]}
    assert roles == {"KA": "keep", "KA2": "delete"}
    assert by["Photos/Snapchat/2023/d.jpg"]["local"] == "Photos/iPhone/roll_d.jpg"

    # The copy of d here is left alone now (the memory takes its place), and it gets d's date.
    assert str(local_d) not in {p for p, *_ in sync.scan()}
    assert sync.best_date(str(local_d), len(roll_d), T + 400 * DAY, sha(roll_d))[1] == "snapchat"

    # The API starts it; the step does it.
    status, body = post(f"{server}/api/snapchat/start", json.dumps({"account": ACC}).encode())
    assert status == 200 and json.loads(body)["queued"] == 5
    run(sync.snapchat_step, client)

    for gone in (roll_b, dup_a, dup_b):
        d = sync._b64_hash(hashlib.sha1(gone).digest())
        assert ("trash", [d]) in client.api.calls and ("delete", [d]) in client.api.calls
    # A copy deleted from Google is kept on this server first, unless the memory here is certainly the
    # same picture (KA2: all but identical) or the same file is here (KB: the camera-roll copy). The
    # less certain KB2 is downloaded.
    assert kept == ["KB2"]
    assert {n for (n,) in sync.q("SELECT name FROM downloads")} == {"KB2.jpg"}
    assert ("favorite", sync._b64_hash(hashlib.sha1(picture(1, quality=50)).digest()), True) in client.api.calls
    assert sorted(client.api.sent) == [T + DAY, T + 2 * DAY, T + 3 * DAY]  # each memory's own moment
    new_b = sync._b64_hash(hashlib.sha1(moved_mem).digest())
    assert ("favorite", new_b, True) in client.api.calls and ("caption", new_b, "Goa") in client.api.calls
    ((name, keys),) = client.api.albums.values()
    assert name == "Snapchat" and {"KA", "KE"} <= set(keys) and len(keys) == 5
    r = sync.snapchat_report(ACC)
    assert all(v["done"] == v["total"] for v in r["counts"].values())
    assert r["duplicates"] == {"total": 3, "deleted": 3}
    # Backed up: nothing goes up again, not the memories nor the copies they stand in for.
    assert not {p for p, *_ in sync.scan()} & (snap_files | {str(local_b), str(local_d)})
    files = dict(sync.q("SELECT path, status FROM files"))
    assert files[str(local_d)] == "exists" and files[str(sync.MEDIA / "Photos/Snapchat/2023/a.jpg")] == "exists"
    # The page's list comes from the API.
    with urllib.request.urlopen(f"{server}/api/snapchat?action=replace") as resp:
        page = json.loads(resp.read())
    assert [i["name"] for i in page["items"]] == ["Photos/Snapchat/2023/b.jpg"]
    assert page["items"][0]["state"] == "done" and page["items"][0]["url"]


def test_skip_and_pause(snaps, server):
    mem = picture(6)
    put("Photos/Snapchat/x.jpg", mem, T)
    manifest("Photos/Snapchat", [{"file": "x.jpg", "id": "x", "kind": "photo", "taken": T, "sha1": sha(mem),
                                  "width": 90, "height": 160}])  # fmt: skip
    library(item("KX", picture(9), T + 5 * DAY))
    client = Google(thumbs={"KX": picture(9)})
    sync.scan()
    run(sync.snapchat_match_step, client)
    assert sync.snapchat_report(ACC)["counts"]["upload"]["total"] == 1
    # Replace needs a copy in Google: refused for this one.
    assert sync.snapchat_set_action(["Photos/Snapchat/x.jpg"], "replace") == {"updated": 0}
    assert sync.snapchat_set_action(["Photos/Snapchat/x.jpg"], "skip") == {"updated": 1}
    assert sync.snapchat_start(ACC) == {"queued": 0}
    sync.snapchat_set_action(["Photos/Snapchat/x.jpg"], "auto")
    status, _ = post(f"{server}/api/snapchat/pause", json.dumps({"account": ACC, "paused": True}).encode())
    assert status == 200
    assert sync.snapchat_start(ACC) == {"queued": 1}
    assert not sync.snapchat_step(ACC, lambda _a: client) and client.api.sent == []
    sync.meta_set(f"snap_paused:{ACC}", "0")
    run(sync.snapchat_step, client)
    assert client.api.sent == [T]


def test_a_changed_list_is_read_again_and_gone_files_leave(snaps):
    a, b = picture(7), picture(8)
    put("Photos/Snapchat/a.jpg", a, T)
    entry = {"kind": "photo", "taken": T, "width": 90, "height": 160}
    manifest("Photos/Snapchat", [{**entry, "file": "a.jpg", "id": "a", "sha1": sha(a)}])
    sync.scan()
    assert sync._snap["paths"] == {str(sync.MEDIA / "Photos/Snapchat/a.jpg")}
    put("Photos/Snapchat/b.jpg", b, T)
    (sync.MEDIA / "Photos/Snapchat/a.jpg").unlink()
    manifest("Photos/Snapchat", [{**entry, "file": "b.jpg", "id": "b", "sha1": sha(b)}])
    os.utime(sync.MEDIA / "Photos/Snapchat" / snapchat.MANIFEST, (T + 5, T + 5))  # a new modified time
    sync.scan()
    assert sync._snap["paths"] == {str(sync.MEDIA / "Photos/Snapchat/b.jpg")}


def test_video_thumbnail_comes_from_its_poster(snaps):
    put("Photos/Snapchat/v.mp4", b"\0\0\0\x18ftypmp42 video", T)
    put("Photos/Snapchat/.posters/v.jpg", picture(1))
    manifest("Photos/Snapchat", [{"file": "v.mp4", "id": "v", "kind": "video", "taken": T, "sha1": "",
                                  "poster": ".posters/v.jpg"}])  # fmt: skip
    sync.scan()
    thumb = sync.thumbnail("Photos/Snapchat/v.mp4")
    assert thumb and thumb.exists()


def test_nothing_in_an_export_folder_goes_up_until_it_is_listed(snaps):
    """The export script writes into the folder while GP Sync scans: a file not in the list yet (or
    any file while the script's marker is there) is held back, wherever the folder is."""
    listed, fresh, other = picture(10), picture(11), picture(12)
    put("Photos/Memories/a.jpg", listed, T)
    put("Photos/Memories/2024/b.jpg", fresh, T)  # written, not listed yet
    put("Photos/trip/c.jpg", other, T)
    manifest("Photos/Memories", [{"file": "a.jpg", "id": "a", "kind": "photo", "taken": T, "sha1": sha(listed)}])
    (sync.MEDIA / "Photos/Memories" / snapchat.RUNNING).write_text("123")
    todo = {sync.rel(p) for p, *_ in sync.scan()}
    assert todo == {"Photos/trip/c.jpg"}
    assert sync.state["snap_exporting"] == ["Photos/Memories"] and sync.state["snap_held"] == 1
    # Done: the marker goes, and b is still held until the list names it.
    (sync.MEDIA / "Photos/Memories" / snapchat.RUNNING).unlink()
    assert {sync.rel(p) for p, *_ in sync.scan()} == {"Photos/trip/c.jpg"}
    r = sync.snapchat_report(ACC)
    assert r["held"] == 1 and r["exporting"] == []
    # A folder the script is only starting on (no list yet) is held too.
    put("Videos/Snap/x.mp4", b"\0\0\0\x18ftypmp42 v", T)
    (sync.MEDIA / "Videos/Snap" / snapchat.RUNNING).write_text("1")
    assert "Videos/Snap/x.mp4" not in {sync.rel(p) for p, *_ in sync.scan()}


def test_quick_album_work_never_waits_behind_slow_work(snaps, monkeypatch):
    """Memories that only need the album are finished and added at once, all of them, even when a
    slow job (here: a duplicate to download, with no slow work allowed this round) is in the queue."""
    entries, items = [], []
    for n in range(150):  # more than one album call's worth
        data = picture(100 + n)
        put(f"Photos/Snapchat/m{n}.jpg", data, T + n * 10)
        entries.append({"file": f"m{n}.jpg", "id": f"m{n}", "kind": "photo", "taken": T + n * 10, "sha1": sha(data),
                        "width": 90, "height": 160})  # fmt: skip
        items.append(item(f"K{n}", picture(100 + n, quality=50), T + n * 10 + 1))  # right date
    dup = picture(100, size=(80, 142), quality=70)  # a second copy of m0, later: deleted after a download
    items.append(item("DUP", dup, T + 300 * DAY))
    manifest("Photos/Snapchat", entries)
    library(*items)
    client = Google(google={hashlib.sha1(dup).digest()}, thumbs={"DUP": dup})
    sync.scan()
    run(sync.snapchat_match_step, client)
    assert sync.snapchat_report(ACC)["duplicates"]["total"] == 1
    sync.snapchat_start(ACC, ["album"])
    monkeypatch.setattr(sync, "SNAP_UPLOADS_PER_STEP", 0)  # the slow job has to wait
    assert sync.snapchat_step(ACC, lambda _a: client)
    c = sync.snapchat_report(ACC)["counts"]["album"]
    assert (c["done"], c["queued"]) == (149, 1)  # all but m0, which waits for its duplicate
    ((_name, keys),) = client.api.albums.values()
    assert len(keys) == 149  # two album calls in one step
