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


def write_mov(path, cid=CID, frames=10):
    out = av.open(str(path), "w", format="mov", options={"movflags": "use_metadata_tags"})
    if cid:
        out.metadata["com.apple.quicktime.content.identifier"] = cid
    s = out.add_stream("libx264", rate=10)
    s.width, s.height, s.pix_fmt = 64, 48, "yuv420p"
    for i in range(frames):
        for p in s.encode(av.VideoFrame.from_image(Image.new("RGB", (64, 48), (i * 20, 0, 0)))):
            out.mux(p)
    for p in s.encode():
        out.mux(p)
    out.close()
    return path


def video_tail(data, length):
    """The appended video, found the way readers do: Length bytes back from the end."""
    return data[-length:]


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


def test_jpeg_motion_photo(tmp_path):
    jpg = still_bytes()
    still = tmp_path / "IMG_1.jpg"
    still.write_bytes(jpg)
    mov = write_mov(tmp_path / "IMG_1.mov")
    out = livephoto.build(still, mov, tmp_path / "out")
    data = out.read_bytes()
    assert out.name == "IMG_1.MP.jpg"
    assert b'GCamera:MotionPhoto="1"' in data and b'Item:Semantic="MotionPhoto"' in data
    length = int(data.split(b'Item:Mime="video/mp4" Item:Semantic="MotionPhoto" Item:Length="')[1].split(b'"')[0])
    with av.open(io.BytesIO(video_tail(data, length))) as c:
        assert c.streams.video and c.duration
    with Image.open(out) as im:  # the still opens as before, pixels untouched
        assert im.size == (64, 48)
    assert data.count(b"\xff\xd9") >= 1 and livephoto.still_id(out) == CID
    assert os.path.getmtime(out) == os.path.getmtime(still)


def test_jpeg_with_xmp_keeps_its_own_xmp(tmp_path):
    jpg = still_bytes()
    own = b"http://ns.adobe.com/xap/1.0/\x00" + (
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        b'<rdf:Description rdf:about="" xmlns:a="urn:a" a:mine="1"/></rdf:RDF></x:xmpmeta>'
    )
    jpg = jpg[:2] + b"\xff\xe1" + struct.pack(">H", len(own) + 2) + own + jpg[2:]
    data = livephoto.jpeg_motion(jpg, b"VIDEO", 0)
    assert data.count(b"http://ns.adobe.com/xap/1.0/\x00") == 1
    assert b'a:mine="1"' in data and b'GCamera:MotionPhoto="1"' in data and data.endswith(b"VIDEO")


def test_heic_live_photo_goes_up_as_a_jpeg_motion_photo(tmp_path):
    """Google doesn't show HEIC motion photos as motion, so the still becomes a JPEG first."""
    from pillow_heif import register_heif_opener

    register_heif_opener()
    still = tmp_path / "IMG_2.heic"
    still.write_bytes(still_bytes("HEIF"))
    mov = write_mov(tmp_path / "IMG_2.mov")
    out = livephoto.build(still, mov, tmp_path / "out")
    assert out.name == "IMG_2.MP.jpg"
    data = out.read_bytes()
    assert data[:2] == b"\xff\xd8" and b'GCamera:MotionPhoto="1"' in data
    with Image.open(still) as a, Image.open(out) as b:
        assert b.format == "JPEG" and a.size == b.size
        assert b.getexif().get(0x010F) == "Apple" and b.getexif().get(0x0112) == 1
        pa, pb = a.convert("RGB").getpixel((10, 10)), b.convert("RGB").getpixel((10, 10))
        assert all(abs(x - y) <= 6 for x, y in zip(pa, pb, strict=True))
    assert livephoto.still_id(out) == CID  # Apple's maker note is kept


def test_heic_motion_photo_to_the_spec(tmp_path):
    from pillow_heif import register_heif_opener

    register_heif_opener()
    still = tmp_path / "IMG_2.heic"
    still.write_bytes(still_bytes("HEIF"))
    mp4 = tmp_path / "v.mp4"
    livephoto.mov_to_mp4(write_mov(tmp_path / "IMG_2.mov"), mp4)
    out = tmp_path / "IMG_2.MP.HEIC"
    out.write_bytes(livephoto.heic_motion(still.read_bytes(), mp4.read_bytes(), 0))
    data = out.read_bytes()
    # The video sits in a final 'mpvd' box, and the XMP says where (Padding 8 = the box header).
    boxes = list(livephoto._boxes(data))
    assert boxes[-1][0] == b"mpvd" and boxes[-2][0] == b"mdat"
    mpvd = boxes[-1]
    length = int(data.split(b'Item:Mime="video/mp4" Item:Semantic="MotionPhoto" Item:Length="')[1].split(b'"')[0])
    assert length == mpvd[2] - 8
    with av.open(io.BytesIO(video_tail(data, length))) as c:
        assert c.streams.video
    # The still decodes to the same picture, and the XMP is a proper item of the image.
    with Image.open(still) as a, Image.open(out) as b:
        assert a.size == b.size and a.convert("RGB").tobytes() == b.convert("RGB").tobytes()
        assert b'GCamera:MotionPhoto="1"' in (b.info.get("xmp") or b"")
        assert b'Item:Mime="image/heic"' in b.info["xmp"] and b'Item:Padding="8"' in b.info["xmp"]
    assert livephoto.still_id(out) == CID


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
    assert [n for n, _ in client.sent] == ["aaa.MP.jpg"]
    assert client.sent[0][1][:2] == b"\xff\xd8" and b'GCamera:MotionPhoto="1"' in client.sent[0][1]
    rows = dict(sync.q("SELECT path, media_key FROM files WHERE path LIKE '%iPhone%'"))
    assert rows == {str(still): "key-aaa.MP.jpg", str(mov): "key-aaa.MP.jpg"}
    assert not list((sync.LIVE_DIR / "work").glob("*/*"))  # the joined file is removed
    # Next scan: both halves are backed up, nothing to send or fix.
    assert not [i for i in sync.scan() if "iPhone" in i[0]]
    assert sync.q("SELECT state FROM live_fix") == [("done",)]


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


class FixClient:
    def __init__(self):
        self.api = FixApi()
        self.sent = []

    def _upload_file(self, path, digest, bar, force_upload, use_quota, saver, progress_callback=None):
        self.sent.append((path.name, sync._b64_hash(digest), use_quota))
        return {str(path): "NEW"}


@pytest.fixture
def separate(iphone, tmp_path, monkeypatch):
    """The pair is in Google as two items: the still (a favourite) and the video."""
    still, mov = iphone
    monkeypatch.setattr(sync, "GPMC_HOME", tmp_path / "gpmc")
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
    assert name == "aaa.MP.jpg" and use_quota is False
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
