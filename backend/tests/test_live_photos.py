"""iPhone Live Photos: pairs found by content identifier, sent as one motion photo, old pairs fixed."""

import hashlib
import io
import os
import struct
import time

import av
import pytest
from gpmc.db import Storage
from gpmc.models import MediaItem
from helpers import FakeClient
from PIL import Image

import livephoto
import sync

CID = "B7150A12-FFA6-47A1-B896-EA93402E0B66"
ACC = "test@example.com"


def makernote(cid):
    """An Apple maker note holding the content identifier (tag 0x11), as iPhones write it."""
    text = cid.encode() + b"\x00"
    ifd = struct.pack(">H", 1) + struct.pack(">HHII", 0x11, 2, len(text), 32) + struct.pack(">I", 0)
    return b"Apple iOS\x00" + b"\x00\x01" + b"MM" + ifd + text


def exif_with(cid):
    exif = Image.Exif()
    exif[0x010F] = "Apple"
    if cid:
        exif.get_ifd(0x8769)[0x927C] = makernote(cid)
    return exif.tobytes()


def still_bytes(fmt="JPEG", cid=CID, color=(200, 30, 30)):
    buf = io.BytesIO()
    im = Image.new("RGB", (64, 48), color)
    if fmt == "HEIF":
        from pillow_heif import register_heif_opener

        register_heif_opener()
    im.save(buf, fmt, exif=exif_with(cid), quality=90)
    return buf.getvalue()


def write_mov(path, cid=CID, frames=10, taken=None):
    out = av.open(str(path), "w", format="mov", options={"movflags": "use_metadata_tags"})
    if cid:
        out.metadata["com.apple.quicktime.content.identifier"] = cid
    if taken:  # Apple's creation date: local time with its offset
        out.metadata["com.apple.quicktime.creationdate"] = taken
    s = out.add_stream("libx264", rate=10)
    s.width, s.height, s.pix_fmt = 64, 48, "yuv420p"
    for i in range(frames):
        for p in s.encode(av.VideoFrame.from_image(Image.new("RGB", (64, 48), (i * 20, 0, 0)))):
            out.mux(p)
    for p in s.encode():
        out.mux(p)
    out.close()
    return path


# ---- livephoto.py


def test_content_ids_of_both_halves(tmp_path):
    (tmp_path / "a.jpg").write_bytes(still_bytes())
    (tmp_path / "b.heic").write_bytes(still_bytes("HEIF"))
    (tmp_path / "plain.jpg").write_bytes(still_bytes(cid=None))
    write_mov(tmp_path / "c.mov")
    write_mov(tmp_path / "d.mov", cid=None)
    assert livephoto.content_id(tmp_path / "a.jpg") == CID
    assert livephoto.content_id(tmp_path / "b.heic") == CID
    assert livephoto.content_id(tmp_path / "plain.jpg") is None
    assert livephoto.content_id(tmp_path / "c.mov") == CID
    assert livephoto.content_id(tmp_path / "d.mov") is None


def test_a_long_video_is_not_a_live_photo(tmp_path):
    write_mov(tmp_path / "long.mov", frames=80)  # 8 seconds
    assert livephoto.video_info(tmp_path / "long.mov") == (None, None)


def test_jpeg_motion_photo_keeps_both_originals(tmp_path):
    jpg = still_bytes()
    still = tmp_path / "IMG_1.jpg"
    still.write_bytes(jpg)
    mov = write_mov(tmp_path / "IMG_1.mov")
    out = livephoto.build(still, mov, tmp_path / "out")
    data = out.read_bytes()
    assert out.name == "IMG_1.MP.JPG"
    assert b'GCamera:MotionPhoto="1"' in data and b'Item:Mime="video/quicktime"' in data
    video = mov.read_bytes()
    assert video in data and data.endswith(b"SEFT")  # the MOV itself, inside Samsung's trailer
    length = int(data.split(b'Item:Semantic="MotionPhoto" Item:Length="')[1].split(b'"')[0])
    padding = int(data.split(b'Item:Semantic="Primary" Item:Length="0" Item:Padding="')[1].split(b'"')[0])
    start = len(data) - length  # where readers look: Length bytes back from the end
    assert data[start : start + len(video)] == video
    assert data[start - padding : start - padding + 4] == bytes([0, 0, 0x30, 0x0A])  # MotionPhoto_Data tag
    with Image.open(still) as a, Image.open(out) as b:  # the picture itself is untouched
        assert a.tobytes() == b.tobytes()
    assert livephoto.still_id(out) == CID
    assert os.path.getmtime(out) == os.path.getmtime(still)


def test_jpeg_with_xmp_keeps_its_own_xmp():
    jpg = still_bytes()
    own = b"http://ns.adobe.com/xap/1.0/\x00" + (
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        b'<rdf:Description rdf:about="" xmlns:a="urn:a" a:mine="1"/></rdf:RDF></x:xmpmeta>'
    )
    jpg = jpg[:2] + b"\xff\xe1" + struct.pack(">H", len(own) + 2) + own + jpg[2:]
    data = livephoto.jpeg_motion(jpg, b"VIDEO", 0)
    assert data.count(b"http://ns.adobe.com/xap/1.0/\x00") == 1
    assert b'a:mine="1"' in data and b'GCamera:MotionPhoto="1"' in data and data.endswith(b"SEFT")


def heif_items(data):
    """(item id, type, content type) of a HEIF's items."""
    meta = next(b for b in livephoto._boxes(data) if b[0] == b"meta")
    iinf = next(b for b in livephoto._boxes(data, meta[1] + 12, meta[1] + meta[2]) if b[0] == b"iinf")
    cs = 2 if data[iinf[1] + iinf[3]] == 0 else 4
    out = []
    for _t, s, sz, h in livephoto._boxes(data, iinf[1] + iinf[3] + 4 + cs, iinf[1] + iinf[2]):
        body = data[s + h + 4 : s + sz]
        typ = body[4:8]
        out.append((int.from_bytes(body[:2], "big"), typ, body[8:].split(b"\x00")[1] if typ == b"mime" else b""))
    return out


@pytest.mark.parametrize("with_xmp", [False, True])
def test_heic_motion_photo_keeps_both_originals(tmp_path, with_xmp):
    from pillow_heif import register_heif_opener

    register_heif_opener()
    still = tmp_path / "IMG_2.heic"
    buf = io.BytesIO()
    own = b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description rdf:about="" xmlns:a="urn:a" a:mine="1"/></rdf:RDF></x:xmpmeta>'  # noqa: E501
    Image.new("RGB", (64, 48), (10, 120, 200)).save(buf, "HEIF", exif=exif_with(CID), xmp=own if with_xmp else None)
    still.write_bytes(buf.getvalue())
    mov = write_mov(tmp_path / "IMG_2.mov")
    out = livephoto.build(still, mov, tmp_path / "out")
    data = out.read_bytes()
    assert out.name == "IMG_2.MP.HEIC"
    boxes = list(livephoto._boxes(data))
    assert boxes[-1][0] == b"mpvd"
    payload = data[boxes[-1][1] + 8 :]
    video = mov.read_bytes()
    assert payload.startswith(video) and b"sefd" in payload[len(video) : len(video) + 8] and payload.endswith(b"SEFT")
    length = int(data.split(b'Item:Semantic="MotionPhoto" Item:Length="')[1].split(b'"')[0])
    assert length == len(payload) and data[len(data) - length :][: len(video)] == video
    # Samsung's MotionPhoto_Data points at the video: "mpv2", offset, size.
    i = payload.find(b"MotionPhoto_Data") + len(b"MotionPhoto_Data")
    assert payload[i : i + 4] == b"mpv2"
    assert struct.unpack(">ii", payload[i + 4 : i + 12]) == (boxes[-1][1] + 8, len(video))
    # One XMP item describing the photo, with the photo's own XMP kept.
    assert sum(1 for _i, t, ct in heif_items(data) if t == b"mime" and ct == b"application/rdf+xml") == 1
    with Image.open(still) as a, Image.open(out) as b:
        assert a.size == b.size and a.tobytes() == b.tobytes()
        assert b'GCamera:MotionPhoto="1"' in b.info["xmp"] and b'Item:Padding="8"' in b.info["xmp"]
        assert (b'a:mine="1"' in b.info["xmp"]) is with_xmp
    assert livephoto.still_id(out) == CID


def test_still_time_is_unset_without_apples_track(tmp_path):
    assert livephoto.still_time_us(write_mov(tmp_path / "a.mov")) == -1
    assert livephoto.still_time_us(tmp_path / "missing.mov") == -1


def test_broken_heic_is_refused():
    with pytest.raises(ValueError):
        livephoto.heic_motion(b"\x00\x00\x00\x10ftypheic\x00\x00\x00\x00", b"v", 0)


# ---- Backups


@pytest.fixture
def iphone(media):
    sync.q("DELETE FROM copies")
    sync.q("DELETE FROM live_ids")
    sync.q("DELETE FROM live_fix")
    sync._live_broken.clear()
    root = media / "Photos" / "iPhone"
    (root / "13").mkdir(parents=True, exist_ok=True)
    (root / "72").mkdir(exist_ok=True)
    still = root / "13" / "aaa.heic"
    still.write_bytes(still_bytes("HEIF"))
    mov = write_mov(root / "72" / "bbb.mov")
    old = time.time() - 2 * sync.LIVE_WAIT
    for p in (still, mov):
        os.utime(p, (old, old))
    return still, mov


class Recorder(FakeClient):
    def __init__(self):
        super().__init__({})
        self.sent = []

    def _upload_file(self, file_path, hash_value, progress, force_upload, use_quota, saver, progress_callback):
        self.sent.append((file_path.name, file_path.read_bytes()))
        progress_callback({"phase": "uploading", "bytes_completed": 1})
        return {str(file_path): f"key-{file_path.name}"}


def test_a_pair_goes_up_as_one_motion_photo(iphone):
    still, mov = iphone
    todo = sync.scan()
    names = [os.path.basename(p) for p, *_ in todo]
    assert "aaa.heic" in names and "bbb.mov" not in names  # the video goes inside the photo
    client = Recorder()
    sync.upload_all(client, [i for i in todo if "iPhone" in i[0]])
    assert [n for n, _ in client.sent] == ["aaa.MP.HEIC"]
    assert client.sent[0][1][4:12] == b"ftypheic" and b'GCamera:MotionPhoto="1"' in client.sent[0][1]
    rows = dict(sync.q("SELECT path, media_key FROM files WHERE path LIKE '%iPhone%'"))
    assert rows == {str(still): "key-aaa.MP.HEIC", str(mov): "key-aaa.MP.HEIC"}
    assert not list((sync.LIVE_DIR / "work").glob("*/*"))  # the joined file is removed
    # Next scan: both halves are backed up, nothing to send or fix.
    assert not [i for i in sync.scan() if "iPhone" in i[0]]
    # Waits for Google to confirm the motion photo, like the fix does.
    assert sync.q("SELECT state FROM live_fix") == [("uploaded",)]


def test_a_new_still_waits_for_its_video(iphone):
    still, mov = iphone
    mov.unlink()
    os.utime(still)  # just arrived
    assert not [i for i in sync.scan() if "iPhone" in i[0]]
    old = time.time() - 2 * sync.LIVE_WAIT
    os.utime(still, (old, old))  # the video never came: the photo goes alone
    assert [os.path.basename(i[0]) for i in sync.scan() if "iPhone" in i[0]] == ["aaa.heic"]


def test_a_pair_that_cannot_be_joined_goes_up_as_two_files(iphone, monkeypatch):
    def broken(*a):
        raise ValueError("odd file")

    monkeypatch.setattr(livephoto, "build", broken)
    client = Recorder()
    sync.upload_all(client, [i for i in sync.scan() if "iPhone" in i[0]])
    assert [n for n, _ in client.sent] == ["aaa.heic"]
    todo = [i for i in sync.scan() if "iPhone" in i[0]]
    assert [os.path.basename(i[0]) for i in todo] == ["bbb.mov"]


# ---- Fixing pairs sent as two items


def b64(data):
    return sync._b64_hash(hashlib.sha1(data).digest())


def gitem(key, dedup, motion=False, **kw):
    return MediaItem(
        media_key=key, file_name=f"{key}.heic", dedup_key=dedup, is_canonical=True, type=kw.pop("type", 1),
        caption=kw.pop("caption", None), collection_id="", size_bytes=10, quota_charged_bytes=0, origin="self",
        content_version=1, utc_timestamp=1_700_000_000_000, server_creation_timestamp=0,
        is_micro_video=motion, **kw,
    )  # fmt: skip


class FixApi:
    bearer_token = "token"
    user_agent = "test"

    def __init__(self):
        self.calls = []

    def move_remote_media_to_trash(self, dedups):
        self.calls.append(("trash", list(dedups)))

    def set_favorite(self, dedup, on):
        self.calls.append(("favorite", dedup, on))

    def set_archived(self, dedups, on):
        self.calls.append(("archived", dedups, on))

    def set_item_caption(self, dedup_key="", caption=""):
        self.calls.append(("caption", dedup_key, caption))

    def commit_upload(self, upload_timestamp=None, **kw):
        return upload_timestamp

    def get_download_urls(self, key):
        return {"1": {"5": {"2": {"6": f"https://img/{key}"}, "5": {"2": {"5": f"https://vid/{key}"}}}}}


class FixClient:
    def __init__(self):
        self.api = FixApi()
        self.sent = []

    def _upload_file(self, path, digest, bar, force_upload, use_quota, saver, progress_callback=None):
        self.sent.append((path.name, sync._b64_hash(digest), use_quota))
        return {str(path): "NEW"}


SERVED = {}  # what the fake Google serves when an item is downloaded: media key -> bytes


class FakeDownload:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, n):
        for i in range(0, len(self.data), n):
            yield self.data[i : i + n]


@pytest.fixture
def separate(iphone, tmp_path, monkeypatch):
    """The pair is in Google as two items: the still (a favourite) and the video."""
    still, mov = iphone
    monkeypatch.setattr(sync, "GPMC_HOME", tmp_path / "gpmc")
    SERVED.clear()
    monkeypatch.setattr("requests.get", lambda url, **kw: FakeDownload(SERVED.get(url.rsplit("/", 1)[-1], b"")))
    now = time.time()
    for path, key in ((still, "OLDSTILL"), (mov, "OLDMOV")):
        st = path.stat()
        sync.q("INSERT INTO files (path, size, mtime, status, media_key, account, updated) VALUES (?,?,?,?,?,?,?)",
               (str(path), st.st_size, st.st_mtime, "exists", key, ACC, now))  # fmt: skip
    db = sync._library_db(ACC)
    db.parent.mkdir(parents=True)
    with Storage(db) as s:
        s.update([gitem("OLDSTILL", b64(still.read_bytes()), is_favorite=True, caption="Goa"),
                  gitem("OLDMOV", b64(mov.read_bytes()), type=2)])  # fmt: skip
    sync.meta_set(f"library_refreshed:{ACC}", str(now))
    sync._live_refresh_at.clear()
    sync._refresh_requests.clear()
    return still, mov, db


def test_pairs_sent_before_become_motion_photos(separate):
    still, mov, db = separate
    assert not [i for i in sync.scan() if "iPhone" in i[0]]  # both already backed up
    assert sync.q("SELECT state FROM live_fix") == [("queued",)]
    client = FixClient()
    assert sync.live_fix_step(ACC, lambda a: client)
    ((name, dedup, use_quota),) = client.sent
    assert name == "aaa.MP.HEIC" and use_quota is False
    assert sync.q("SELECT state, new_key, old_still, old_mov FROM live_fix")[0] == (
        "uploaded",
        "NEW",
        b64(still.read_bytes()),
        b64(mov.read_bytes()),
    )
    # Nothing is deleted until Google's library shows the new item as a motion photo.
    sync.live_fix_step(ACC, lambda a: client)
    assert client.api.calls == [] and ACC in sync._refresh_requests
    with Storage(db) as s:
        s.update([gitem("NEW", dedup, motion=False)])
    sync.meta_set(f"library_refreshed:{ACC}", str(time.time() + 60))
    sync._live_refresh_at.clear()
    sync.live_fix_step(ACC, lambda a: client)
    assert client.api.calls == []
    with Storage(db) as s:
        s.update([gitem("NEW", dedup, motion=True)])
    assert sync.live_fix_step(ACC, lambda a: client)
    assert ("trash", [b64(still.read_bytes()), b64(mov.read_bytes())]) in client.api.calls
    assert ("favorite", dedup, True) in client.api.calls and ("caption", dedup, "Goa") in client.api.calls
    assert sync.q("SELECT state FROM live_fix") == [("done",)]
    rows = dict(sync.q("SELECT path, media_key FROM files WHERE path LIKE '%iPhone%'"))
    assert rows == {str(still): "NEW", str(mov): "NEW"}
    assert sync.live_summary()[ACC]["done"] == 1


def test_google_never_making_it_motion_deletes_nothing(separate, monkeypatch):
    _still, _mov, _db = separate
    sync.scan()
    client = FixClient()
    sync.live_fix_step(ACC, lambda a: client)
    sync.q("UPDATE live_fix SET uploaded=?", (time.time() - sync.LIVE_GIVE_UP - 10,))
    sync.meta_set(f"library_refreshed:{ACC}", str(time.time()))
    sync.live_fix_step(ACC, lambda a: client)
    assert client.api.calls == []
    assert sync.q("SELECT state FROM live_fix") == [("failed",)]
    # A failed pair is left as two files from then on, and can be tried again.
    assert not [i for i in sync.scan() if "iPhone" in i[0]]
    assert sync.live_retry()["queued"] == 1


def test_already_a_motion_photo_only_the_extra_video_goes(separate):
    still, mov, db = separate
    with Storage(db) as s:
        s.update([gitem("OLDSTILL", b64(still.read_bytes()), motion=True)])
    sync.scan()
    client = FixClient()
    sync.live_fix_step(ACC, lambda a: client)
    assert client.sent == []
    sync.live_fix_step(ACC, lambda a: client)
    assert client.api.calls == [("trash", [b64(mov.read_bytes())])]
    assert dict(sync.q("SELECT path, media_key FROM files WHERE path LIKE '%iPhone%'"))[str(mov)] == "OLDSTILL"


def test_a_photo_in_googles_trash_is_left_alone(separate):
    still, _mov, db = separate
    with Storage(db) as s:
        s.update([gitem("OLDSTILL", b64(still.read_bytes()), trash_timestamp=5)])
    sync.scan()
    client = FixClient()
    sync.live_fix_step(ACC, lambda a: client)
    assert client.sent == [] and client.api.calls == []
    assert sync.q("SELECT state FROM live_fix") == [("skipped",)]


# ---- Motion photos already in Google are never re-uploaded without their motion


def test_free_storage_and_copies_skip_motion_photos(tmp_path, monkeypatch, media):
    monkeypatch.setattr(sync, "GPMC_HOME", tmp_path / "gpmc")
    for acc in (ACC, "other@example.com"):
        db = sync._library_db(acc)
        db.parent.mkdir(parents=True)
    with Storage(sync._library_db(ACC)) as s:
        s.update([
            MediaItem(media_key="LIVE", file_name="a.heic", dedup_key="d1", is_canonical=True, type=1, caption=None,
                      collection_id="", size_bytes=10, quota_charged_bytes=10, origin="self", content_version=1,
                      utc_timestamp=1, server_creation_timestamp=0, is_micro_video=True),
            MediaItem(media_key="STILL", file_name="b.jpg", dedup_key="d2", is_canonical=True, type=1, caption=None,
                      collection_id="", size_bytes=10, quota_charged_bytes=10, origin="self", content_version=1,
                      utc_timestamp=1, server_creation_timestamp=0),
        ])  # fmt: skip
    with Storage(sync._library_db("other@example.com")) as s:
        s.update([])
    sync.q("DELETE FROM reclaim")
    sync.q("DELETE FROM copyjobs")
    sync.reclaim_queue_all(ACC)
    assert [k for (k,) in sync.q("SELECT media_key FROM reclaim")] == ["STILL"]
    assert [i["key"] for i in sync.copy_candidates(ACC, "other@example.com")["items"]] == ["STILL"]
    sync.q("DELETE FROM reclaim")


def test_old_video_noted_empty_is_found_again(separate):
    """1.9.0 noted some old videos' keys as empty: the tidy-up finds the video again by its row and,
    for pairs already finished, by its file name and exact size."""
    still, mov, db = separate
    with Storage(db) as s:
        s.update([gitem("OLDMOV", "", type=2)])  # the library copy had no key for it yet
    sync.scan()
    client = FixClient()
    sync.live_fix_step(ACC, lambda a: client)
    assert not sync.q("SELECT old_mov FROM live_fix")[0][0]
    dedup = client.sent[0][1]
    mov_dedup = b64(mov.read_bytes())
    with Storage(db) as s:  # a fresh read fills the key in, and Google shows the motion photo
        s.update([gitem("NEW", dedup, motion=True), gitem("OLDMOV", mov_dedup, type=2)])
    sync.meta_set(f"library_refreshed:{ACC}", str(time.time() + 60))
    sync.live_fix_step(ACC, lambda a: client)
    assert ("trash", [b64(still.read_bytes()), mov_dedup]) in client.api.calls
    # A pair finished with the video left over (its row already points at the motion photo).
    client.api.calls.clear()
    with Storage(db) as s:
        old = MediaItem(
            media_key="OLDMOV", file_name="bbb.mov", dedup_key=mov_dedup, is_canonical=True, type=2, caption=None,
            collection_id="", size_bytes=mov.stat().st_size, quota_charged_bytes=0, origin="self",
            content_version=1, utc_timestamp=1, server_creation_timestamp=0,
        )  # fmt: skip
        s.update([old])
    sync.q("UPDATE live_fix SET state='uploaded', old_mov=''")
    sync.live_fix_step(ACC, lambda a: client)
    assert client.api.calls[0] == ("trash", [b64(still.read_bytes()), mov_dedup])
    assert sync.q("SELECT state FROM live_fix") == [("done",)]


def confirm(db, dedup, *more):
    with Storage(db) as s:
        s.update([gitem("NEW", dedup, motion=True), *more])
    sync.meta_set(f"library_refreshed:{ACC}", str(time.time() + 60))
    sync._live_refresh_at.clear()


def test_the_jpeg_copy_older_versions_made_is_replaced(separate):
    """1.9.0-1.9.2 made a JPEG copy with an MP4 inside: it is made again from the originals, and the
    JPEG copy goes to the Trash once Google confirms the new motion photo."""
    still, mov, db = separate
    with Storage(db) as s:
        s.update([MediaItem(media_key="OLDJPG", file_name="aaa.MP.jpg", dedup_key="J" * 27, is_canonical=True,
                            type=1, caption=None, collection_id="", size_bytes=10, quota_charged_bytes=0,
                            origin="self", content_version=1, utc_timestamp=1_700_000_000_000,
                            server_creation_timestamp=0, is_micro_video=True)])  # fmt: skip
    st = still.stat()
    sync.q("UPDATE files SET media_key='OLDJPG' WHERE path IN (?, ?)", (str(still), str(mov)))
    sync.q("""INSERT INTO live_fix (still, account, mov, state, new_key, new_dedup, updated)
              VALUES (?,?,?,'done','OLDJPG',?,?)""", (str(still), ACC, str(mov), "J" * 27, st.st_mtime))  # fmt: skip
    sync.meta_set("live_originals", "")
    with sync.contextlib.suppress(Exception):
        sync.q("""UPDATE live_fix SET state='queued', error='', prev_motion=COALESCE(prev_motion, new_dedup)
                  WHERE COALESCE(fmt, 1)=1 AND state IN ('done', 'uploaded', 'held', 'failed')""")
    client = FixClient()
    sync.live_fix_step(ACC, lambda a: client)
    ((name, dedup, _q),) = client.sent  # not taken for "already a motion photo": it is GP Sync's own copy
    assert name == "aaa.MP.HEIC"
    confirm(db, dedup)
    sync.live_fix_step(ACC, lambda a: client)
    trashed = [d for c in client.api.calls if c[0] == "trash" for d in c[1]]
    assert "J" * 27 in trashed
    assert sync.q("SELECT state, fmt FROM live_fix") == [("done", 2)]


def test_copies_of_the_same_shot_go_only_when_their_content_id_matches(separate):
    """Other items taken at the same moment are checked one by one: a smaller copy of this Live
    Photo goes to the Trash; a different photo (another content ID) and a bigger copy stay."""
    _still, _mov, db = separate
    small = still_bytes(cid=CID)
    other = still_bytes(cid="00000000-1111-2222-3333-444444444444")
    with Storage(db) as s:
        s.update([
            gitem("SMALL", b64(small), width=16, height=12),
            gitem("OTHER", b64(other), width=16, height=12),
            gitem("BIGGER", b64(b"bigger"), width=4000, height=3000),
        ])  # fmt: skip
    SERVED.update({"SMALL": small, "OTHER": other, "BIGGER": still_bytes(cid=CID)})
    sync.scan()
    client = FixClient()
    sync.live_fix_step(ACC, lambda a: client)
    confirm(db, client.sent[0][1])
    sync.live_fix_step(ACC, lambda a: client)
    trashed = [d for c in client.api.calls if c[0] == "trash" for d in c[1]]
    assert b64(small) in trashed
    assert b64(other) not in trashed and b64(b"bigger") not in trashed
    assert sync.q("SELECT kept FROM live_fix")[0][0] == "BIGGER.heic"


def test_a_live_photo_the_phone_uploaded_is_kept_and_nothing_is_uploaded(separate):
    """Google already has this Live Photo as a real motion photo (the iPhone app sent it): nothing
    is uploaded, and only GP Sync's separate still and video go."""
    still, mov, db = separate
    phone = still_bytes(cid=CID, color=(1, 2, 3))
    with Storage(db) as s:
        s.update([gitem("PHONE", b64(phone), motion=True)])
    SERVED["PHONE"] = phone
    sync.scan()
    client = FixClient()
    sync.live_fix_step(ACC, lambda a: client)
    assert client.sent == []
    assert sync.q("SELECT new_key FROM live_fix")[0][0] == "PHONE"
    sync.live_fix_step(ACC, lambda a: client)
    trashed = [d for c in client.api.calls if c[0] == "trash" for d in c[1]]
    assert b64(still.read_bytes()) in trashed and b64(mov.read_bytes()) in trashed and b64(phone) not in trashed


def test_a_png_named_heic_is_left_as_it_is(separate):
    """A few phone backups save screenshots as .heic that are PNGs inside: no motion photo can hold
    a PNG, so the pair is skipped and nothing in Google changes."""
    still, _mov, _db = separate
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, "PNG")
    with pytest.raises(livephoto.Unsupported):
        livephoto.still_format(buf.getvalue())
    sync.scan()
    sync.q("UPDATE live_fix SET state='queued'")
    st = still.stat()
    still.write_bytes(buf.getvalue() + makernote(CID))  # keeps its content ID, but is a PNG
    os.utime(still, (st.st_atime, st.st_mtime))
    client = FixClient()
    sync.live_fix_step(ACC, lambda a: client)
    assert client.sent == [] and client.api.calls == []
    state, error = sync.q("SELECT state, error FROM live_fix")[0]
    assert state == "skipped" and "PNG" in error


def test_a_new_upload_google_never_plays_sends_its_video_alone(iphone, tmp_path, monkeypatch):
    _still, _mov = iphone
    monkeypatch.setattr(sync, "GPMC_HOME", tmp_path / "gpmc")
    sync.upload_all(Recorder(), [i for i in sync.scan() if "iPhone" in i[0]])
    db = sync._library_db(ACC)
    db.parent.mkdir(parents=True)
    with Storage(db) as s:
        s.update([gitem("key-aaa.MP.HEIC", "N" * 27)])
    sync.q("UPDATE live_fix SET new_dedup=?, uploaded=?", ("N" * 27, time.time() - sync.LIVE_GIVE_UP - 10))
    sync.meta_set(f"library_refreshed:{ACC}", str(time.time()))
    sync.live_fix_step(ACC, lambda a: FixClient())
    assert sync.q("SELECT state FROM live_fix") == [("failed",)]
    todo = [os.path.basename(i[0]) for i in sync.scan() if "iPhone" in i[0]]
    assert todo == ["bbb.mov"]  # the video goes up on its own now


def test_fix_dates_never_uploads_half_a_live_photo(iphone):
    still, mov = iphone
    todo = sync.scan()
    sync.upload_all(Recorder(), [i for i in todo if "iPhone" in i[0]])
    for p in (still, mov):
        sync.sq(
            "INSERT OR REPLACE INTO media_dates (path, size, mtime, sha1, fix, error) VALUES (?,?,?,?,'waiting','')",
            (str(p), p.stat().st_size, p.stat().st_mtime, hashlib.sha1(p.read_bytes()).hexdigest()),
        )
    asked = []
    assert not sync.datefix_step(lambda a: asked.append(a))
    assert asked == []  # Google is never touched
    fixes = sync.sq("SELECT fix, error FROM media_dates WHERE path IN (?, ?)", (str(still), str(mov)))
    assert [f for f, _ in fixes] == ["failed", "failed"] and all("Live Photo" in e for _, e in fixes)


TAKEN = "2024-10-12T20:13:58+0530"
TAKEN_TS = 1728744238  # 2024-10-12 14:43:58 UTC


def test_the_date_a_live_photo_was_taken_comes_from_its_video(tmp_path):
    assert livephoto.video_date(write_mov(tmp_path / "a.mov", taken=TAKEN)) == TAKEN_TS
    assert livephoto.video_date(write_mov(tmp_path / "b.mov")) is None
    assert livephoto.video_date(write_mov(tmp_path / "c.mov", taken="1970-01-01T00:00:00+0000")) is None
    assert livephoto.video_date(tmp_path / "missing.mov") is None


def dated_still(path):
    """A still with its own capture date (EXIF DateTimeOriginal)."""
    from pillow_heif import register_heif_opener

    register_heif_opener()
    exif = Image.Exif()
    exif.load(exif_with(CID))
    exif.get_ifd(0x8769)[0x9003] = "2024:10:12 20:13:58"
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (10, 200, 10)).save(buf, "HEIF", exif=exif.tobytes())
    path.write_bytes(buf.getvalue())


def dates_sent(monkeypatch):
    sent = []
    real = sync.upload_date

    def spy(client, when, mtime=None):
        sent.append(when)
        return real(client, when, mtime)

    monkeypatch.setattr(sync, "upload_date", spy)
    return sent


def test_a_live_photo_without_a_date_of_its_own_goes_up_dated_by_its_video(iphone, monkeypatch):
    _still, mov = iphone
    write_mov(mov, taken=TAKEN)
    old = time.time() - 2 * sync.LIVE_WAIT
    os.utime(mov, (old, old))
    sent = dates_sent(monkeypatch)
    sync.upload_all(Recorder(), [i for i in sync.scan() if "iPhone" in i[0]])
    assert sent == [TAKEN_TS]  # not the day the file was saved here


def test_a_live_photo_with_its_own_date_lets_google_read_it(iphone, monkeypatch):
    still, mov = iphone
    dated_still(still)
    write_mov(mov, taken=TAKEN)
    old = time.time() - 2 * sync.LIVE_WAIT
    for p in (still, mov):
        os.utime(p, (old, old))
    sent = dates_sent(monkeypatch)
    sync.upload_all(Recorder(), [i for i in sync.scan() if "iPhone" in i[0]])
    assert sent == [None]


def test_fixing_a_pair_sends_the_videos_date(separate, monkeypatch):
    _still, mov, _db = separate
    write_mov(mov, taken=TAKEN)
    sync.q("UPDATE files SET size=?, mtime=? WHERE path=?", (mov.stat().st_size, mov.stat().st_mtime, str(mov)))
    sync.scan()
    sent = dates_sent(monkeypatch)
    assert sync.live_fix_step(ACC, lambda a: FixClient())
    assert sent == [TAKEN_TS]


class RedateApi(FixApi):
    def __init__(self, google):
        super().__init__()
        self.google = google  # dedup keys Google has

    def find_remote_media_by_hash(self, digest):
        return "NEW" if sync._b64_hash(digest) in self.google else None

    def delete_remote_media_permanently(self, dedups):
        self.calls.append(("delete", list(dedups)))
        self.google.difference_update(dedups)


def done_motion_photo(still, mov, db, tmp_path, shown_ms, dedup=None):
    """A motion photo GP Sync sent before dates were given: done, Google showing `shown_ms`."""
    built = livephoto.build(str(still), str(mov), tmp_path / "built")
    dedup = dedup or b64(built.read_bytes())
    sync.q("""INSERT OR REPLACE INTO live_fix (still, account, mov, state, new_key, new_dedup, uploaded, error,
              updated, fmt) VALUES (?,?,?,'done','NEW',?,?,'',?,2)""",
           (str(still), ACC, str(mov), dedup, time.time() - 600, time.time()))  # fmt: skip
    for p in (still, mov):
        sync.q("UPDATE files SET media_key='NEW', status='uploaded' WHERE path=?", (str(p),))
    with Storage(db) as s:
        s.update([gitem("NEW", dedup, motion=True, is_favorite=True, caption="Goa")])
        s.conn.execute("UPDATE remote_media SET utc_timestamp=? WHERE dedup_key=?", (shown_ms, dedup))
        s.conn.commit()
    return dedup


@pytest.fixture
def undated_done(separate, tmp_path):
    still, mov, db = separate
    write_mov(mov, taken=TAKEN)
    sync.q("UPDATE files SET size=?, mtime=? WHERE path=?", (mov.stat().st_size, mov.stat().st_mtime, str(mov)))
    return still, mov, db


def test_a_motion_photo_on_the_wrong_day_goes_up_again_dated_by_its_video(undated_done, tmp_path, monkeypatch):
    still, mov, db = undated_done
    dedup = done_motion_photo(still, mov, db, tmp_path, shown_ms=1_778_000_000_000)  # the day it was saved here
    client = FixClient()
    client.api = RedateApi({dedup})
    sent = dates_sent(monkeypatch)
    assert sync.live_fix_step(ACC, lambda a: client)  # noticed
    assert sync.q("SELECT state FROM live_fix") == [("redate",)]
    assert sync.live_fix_step(ACC, lambda a: client)
    assert ("delete", [dedup]) in client.api.calls  # Google's copy is the same file, byte for byte
    assert [d for _n, d, _q in client.sent] == [dedup] and sent == [TAKEN_TS]
    assert sync.q("SELECT state, favorite, caption, dated FROM live_fix") == [("uploaded", 1, "Goa", 1)]
    # Confirmed as a motion photo as before; then never checked again.
    confirm(db, dedup)
    assert sync.live_fix_step(ACC, lambda a: client)
    assert sync.q("SELECT state FROM live_fix") == [("done",)]
    assert not sync.live_fix_step(ACC, lambda a: client)


def test_a_motion_photo_on_the_right_day_is_left_alone(undated_done, tmp_path):
    still, mov, db = undated_done
    done_motion_photo(still, mov, db, tmp_path, shown_ms=(TAKEN_TS + 30) * 1000)
    client = FixClient()
    assert not sync.live_fix_step(ACC, lambda a: client)
    assert client.sent == [] and client.api.calls == []
    assert sync.q("SELECT state, dated FROM live_fix") == [("done", 1)]


def test_a_date_is_never_fixed_when_googles_file_differs(undated_done, tmp_path):
    still, mov, db = undated_done
    other = b64(b"not this file")
    done_motion_photo(still, mov, db, tmp_path, shown_ms=1_778_000_000_000, dedup=other)
    client = FixClient()
    client.api = RedateApi({other})
    sync.live_fix_step(ACC, lambda a: client)
    sync.live_fix_step(ACC, lambda a: client)
    assert client.sent == [] and not [c for c in client.api.calls if c[0] in ("trash", "delete")]
    assert sync.q("SELECT state, dated FROM live_fix") == [("done", 1)]


def test_dates_report_reads_a_live_photo_from_its_video_and_motion_photo(undated_done, tmp_path):
    still, mov, db = undated_done
    done_motion_photo(still, mov, db, tmp_path, shown_ms=TAKEN_TS * 1000)
    st = still.stat()
    assert sync.date_facts(str(still), st.st_size, st.st_mtime) == 0  # no date of its own
    sync.meta_set(f"library_refreshed:{ACC}", str(time.time() + 60))
    item = next(i for i in sync.dates_report(ACC)["items"] if i["name"].endswith("aaa.heic"))
    assert (item["source"], item["best"]) == ("video", TAKEN_TS)
    assert item["google"] == TAKEN_TS and not item["wrong"]  # the motion photo's date, which is right
