"""iPhone Live Photos: find the still and the short video that belong together, and join them into one
motion photo file that Google Photos plays, keeping both originals byte for byte.

Apple saves a Live Photo as two files linked by a content identifier stored in both: in the still's
Apple maker note (tag 0x11) and in the video's QuickTime metadata. Google Photos shows a motion photo
only when it is one file. The layout is the one Samsung phones write and MotionPhoto2
(github.com/PetrVys/MotionPhoto2, a tested open-source converter) reproduces, which Google Photos
plays: the photo unchanged except for motion-photo XMP merged into its own XMP, then the original MOV
unchanged, then Samsung's small trailer that points at it. Nothing is re-encoded or converted: a HEIC
stays HEIC, a JPEG stays JPEG, the MOV stays MOV. The XMP also carries Apple's own still-frame time
from the MOV, so the photo and the first frame Google shows line up.
"""

import os
import re
import struct
from pathlib import Path

STILL_EXTS = {"heic", "heif", "jpg", "jpeg"}
VIDEO_EXTS = {"mov"}
MAX_VIDEO_BYTES = 80 * 1024**2  # a Live Photo's video is a few MB; anything big is a real video
MAX_VIDEO_SECONDS = 6.0
_UUID = re.compile(rb"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$")
_HEAD = 256 * 1024  # the maker note sits near the start (within 131 KB in 3,872 iPhone photos)


def _ext(path):
    return str(path).rsplit(".", 1)[-1].lower() if "." in str(path) else ""


# ---- Content identifiers


def _apple_makernote_id(data):
    """The content identifier in an Apple maker note ("Apple iOS\\0", version, "MM", then an IFD whose
    offsets count from the start of the maker note), or None."""
    start = 0
    while (i := data.find(b"Apple iOS\x00", start)) >= 0:
        start = i + 1
        if data[i + 12 : i + 14] != b"MM":
            continue
        try:
            (count,) = struct.unpack_from(">H", data, i + 14)
            for n in range(min(count, 200)):
                tag, typ, cnt, val = struct.unpack_from(">HHII", data, i + 16 + n * 12)
                if tag == 0x11 and typ == 2 and 30 <= cnt <= 64:
                    raw = data[i + val : i + val + cnt].rstrip(b"\x00")
                    if _UUID.match(raw):
                        return raw.decode().upper()
        except struct.error:
            continue
    return None


def still_id(path):
    """The Live Photo content identifier of a HEIC or JPEG still, or None."""
    try:
        with open(path, "rb") as f:
            return _apple_makernote_id(f.read(_HEAD))
    except OSError:
        return None


def video_info(path):
    """(content identifier, seconds) of a MOV, or (None, None) if it isn't a Live Photo video."""
    try:
        if os.path.getsize(path) > MAX_VIDEO_BYTES:
            return None, None
        import av

        with av.open(str(path)) as c:
            cid = c.metadata.get("com.apple.quicktime.content.identifier")
            seconds = c.duration / 1e6 if c.duration else None
            if not cid or not c.streams.video:
                return None, None
            if seconds is not None and seconds > MAX_VIDEO_SECONDS:
                return None, None
            raw = cid.strip().encode()
            return (raw.decode().upper() if _UUID.match(raw) else None), seconds
    except Exception:
        return None, None


def content_id(path):
    """The content identifier of either half, or None."""
    e = _ext(path)
    if e in STILL_EXTS:
        return still_id(path)
    if e in VIDEO_EXTS:
        return video_info(path)[0]
    return None


def video_date(mov):
    """When the Live Photo was taken (unix seconds), from its MOV: Apple's creation date (local time
    with its offset) or QuickTime's creation time (UTC). None if neither is there or makes sense."""
    import datetime as dt
    import time

    try:
        import av

        with av.open(str(mov)) as c:
            md = c.metadata
            raw = [md.get("com.apple.quicktime.creationdate"), md.get("creation_time")]
    except Exception:
        return None
    for text in raw:
        if not text:
            continue
        text = text.strip().replace("Z", "+00:00")
        if len(text) > 5 and text[-5] in "+-" and text[-3] != ":":  # +0530 -> +05:30
            text = f"{text[:-2]}:{text[-2:]}"
        try:
            when = dt.datetime.fromisoformat(text)
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.UTC)
        ts = when.timestamp()
        if 631152000 <= ts <= time.time() + 86400:  # 1990 to tomorrow: a clock that was never set fails
            return ts
    return None


# ---- ISO boxes


def _boxes(data, start=0, end=None):
    """(type, start, size, header size) of the ISO boxes in data[start:end]."""
    end = len(data) if end is None else end
    i = start
    while i + 8 <= end:
        size, typ = struct.unpack_from(">I4s", data, i)
        head = 8
        if size == 1:
            (size,) = struct.unpack_from(">Q", data, i + 8)
            head = 16
        elif size == 0:
            size = end - i
        if size < head or i + size > end:
            raise ValueError(f"broken box {typ!r}")
        yield typ, i, size, head
        i += size


def _box(typ, body, version=None, flags=0):
    head = b"" if version is None else struct.pack(">I", (version << 24) | flags)
    return struct.pack(">I4s", 8 + len(head) + len(body), typ) + head + body


def _uint(n, size):
    return n.to_bytes(size, "big") if size else b""


def _read_uint(data, off, size):
    return int.from_bytes(data[off : off + size], "big") if size else 0


# ---- Apple's still-frame time


STILL_TIME_KEY = b"com.apple.quicktime.still-image-time"


def still_time_us(mov):
    """When in the video Apple took the photo (microseconds), from the MOV's timed metadata track
    whose key is com.apple.quicktime.still-image-time: its one sample's time. -1 if there is none
    (the spec's "unset": readers then use the middle)."""
    try:
        data = Path(mov).read_bytes()
        moov = next((b for b in _boxes(data) if b[0] == b"moov"), None)
        if not moov:
            return -1
        traks = [b for b in _boxes(data, moov[1] + moov[3], moov[1] + moov[2]) if b[0] == b"trak"]
        index = next((n for n, t in enumerate(traks) if STILL_TIME_KEY in data[t[1] : t[1] + t[2]]), None)
        if index is None:
            return -1
        import av

        with av.open(str(mov)) as c:
            if index >= len(c.streams):
                return -1
            stream = c.streams[index]
            for packet in c.demux(stream):
                if packet.pts is not None:
                    return max(0, round(float(packet.pts * packet.time_base) * 1e6))
    except Exception:
        return -1
    return -1


# ---- Motion photo XMP


def _xmp_description(image_mime, video_len, padding, timestamp_us):
    return (
        '<rdf:Description rdf:about="" xmlns:GCamera="http://ns.google.com/photos/1.0/camera/"'
        ' xmlns:Container="http://ns.google.com/photos/1.0/container/"'
        ' xmlns:Item="http://ns.google.com/photos/1.0/container/item/"'
        ' GCamera:MotionPhoto="1" GCamera:MotionPhotoVersion="1"'
        f' GCamera:MotionPhotoPresentationTimestampUs="{timestamp_us}">'
        "<Container:Directory><rdf:Seq>"
        f'<rdf:li rdf:parseType="Resource"><Container:Item Item:Mime="{image_mime}" Item:Semantic="Primary"'
        f' Item:Length="0" Item:Padding="{padding}"/></rdf:li>'
        '<rdf:li rdf:parseType="Resource"><Container:Item Item:Mime="video/quicktime" Item:Semantic="MotionPhoto"'
        f' Item:Length="{video_len}" Item:Padding="0"/></rdf:li>'
        "</rdf:Seq></Container:Directory></rdf:Description>"
    )


def _xmp_packet(description):
    return (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        f"{description}</rdf:RDF></x:xmpmeta>"
    ).encode()


def _merge_xmp(packet, description):
    """The photo's own XMP with the motion-photo description added (what was there is kept)."""
    if not packet:
        return _xmp_packet(description)
    close = packet.rfind(b"</rdf:RDF>")
    if close < 0:
        raise ValueError("unreadable XMP")
    return packet[:close] + description.encode() + packet[close:]


# ---- Samsung's trailer (what Samsung phones write after a motion photo, and MotionPhoto2 copies)

_SEF_IDS = {"MotionPhoto_Data": bytes([0, 0, 0x30, 0x0A]), "MotionPhoto_Version": bytes([0, 0, 0x31, 0x0A])}
_SEFH_VERSION = 107


def _sef(tags):
    """Samsung SEF data: the tags, then the SEFH index, its length and "SEFT"."""
    data, entries = b"", []
    for name, value in tags.items():
        record = _SEF_IDS[name] + struct.pack("<i", len(name)) + name.encode() + value
        entries.append([name, len(data), len(record)])
        data += record
    # SEFH counts each tag's offset back from the start of the index.
    index = b"SEFH" + struct.pack("<ii", _SEFH_VERSION, len(entries))
    for name, start, length in entries:
        index += _SEF_IDS[name] + struct.pack("<ii", len(data) - start, length)
    return data + index + struct.pack("<i", len(index)) + b"SEFT"


def _sef_heic_tail(video, image_size):
    """For a HEIC: the original video, then a "sefd" box with SEF data pointing at it."""
    mp_data = b"mpv2" + struct.pack(">ii", image_size + 8, len(video))
    sef = _sef({"MotionPhoto_Data": mp_data, "MotionPhoto_Version": b"mpv3"})
    return video + struct.pack(">i", len(sef) + 8) + b"sefd" + sef


def _sef_jpeg_tail(video):
    """For a JPEG: SEF data holding the original video. Returns (tail, bytes before the video)."""
    tail = _sef({"MotionPhoto_Data": video, "MotionPhoto_Version": b"mpv3"})
    before = len(_SEF_IDS["MotionPhoto_Data"]) + 4 + len("MotionPhoto_Data")
    return tail, before


# ---- JPEG


_XMP_SIG = b"http://ns.adobe.com/xap/1.0/\x00"


def _jpeg_segments(jpg):
    """(marker, start, end) of the segments before the image data."""
    if jpg[:2] != b"\xff\xd8":
        raise ValueError("not a JPEG")
    i = 2
    while i + 4 <= len(jpg) and jpg[i] == 0xFF:
        marker = jpg[i + 1]
        if marker == 0xDA:  # start of scan: image data follows
            break
        (n,) = struct.unpack_from(">H", jpg, i + 2)
        yield marker, i, i + 2 + n
        i += 2 + n


def jpeg_motion(jpg, mov, timestamp_us):
    """A JPEG motion photo: the photo's bytes with motion-photo XMP merged into its own XMP, then
    Samsung's trailer holding the original MOV."""
    tail, before = _sef_jpeg_tail(mov)
    desc = _xmp_description("image/jpeg", len(tail) - before, before, timestamp_us)
    segments = list(_jpeg_segments(jpg))
    xmp = next((s for s in segments if s[0] == 0xE1 and jpg[s[1] + 4 : s[1] + 4 + len(_XMP_SIG)] == _XMP_SIG),
               None)  # fmt: skip
    if xmp:
        _, start, end = xmp
        payload = _XMP_SIG + _merge_xmp(jpg[start + 4 + len(_XMP_SIG) : end], desc)
        at, cut = start, end
    else:  # after the leading APPn segments (EXIF first, as cameras write it)
        at = 2
        for marker, _start, end in segments:
            if not 0xE0 <= marker <= 0xEF:
                break
            at = end
        payload, cut = _XMP_SIG + _xmp_packet(desc), at
    if len(payload) + 2 > 0xFFFF:
        raise ValueError("XMP too large")
    seg = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    return jpg[:at] + seg + jpg[cut:] + tail


# ---- HEIC


def heic_motion(heic, mov, timestamp_us):
    """A HEIC motion photo: the photo's own XMP item gets the motion-photo description (its new bytes
    go in a small mdat after the image and its iloc entry points there; nothing else in the image
    moves), or an XMP item is added if it has none; then a final 'mpvd' box holds the original MOV and
    Samsung's 'sefd' trailer."""
    data = heic
    top = list(_boxes(data))
    meta = next((b for b in top if b[0] == b"meta"), None)
    if not meta or top[0][0] != b"ftyp" or any(b[0] == b"mpvd" for b in top):
        raise ValueError("not a plain HEIF still")
    m_start, m_size, m_head = meta[1], meta[2], meta[3]
    if m_head != 8:
        raise ValueError("large meta box")
    children = list(_boxes(data, m_start + 12, m_start + m_size))
    by = {b[0]: b for b in children}
    for need in (b"pitm", b"iinf", b"iloc"):
        if need not in by:
            raise ValueError(f"HEIF without {need.decode()}")

    _, p, _, ph = by[b"pitm"]
    primary = struct.unpack_from(">H" if data[p + ph] == 0 else ">I", data, p + ph + 4)[0]

    # Items: which are XMP ("mime" items of type application/rdf+xml)
    _, i0, isz, ih = by[b"iinf"]
    cnt_size = 2 if data[i0 + ih] == 0 else 4
    count = _read_uint(data, i0 + ih + 4, cnt_size)
    ids, xmp_items = [], set()
    for typ, s, sz, hh in _boxes(data, i0 + ih + 4 + cnt_size, i0 + isz):
        if typ != b"infe":
            continue
        v = data[s + hh]
        n = 4 if v >= 3 else 2
        iid = _read_uint(data, s + hh + 4, n)
        ids.append(iid)
        rest = data[s + hh + 4 + n + 2 : s + sz]
        if v >= 2 and rest[:4] == b"mime" and b"application/rdf+xml" in rest:
            xmp_items.add(iid)

    # Which XMP item describes the primary image ("cdsc" reference to it)
    described = None
    if b"iref" in by:
        _, r0, rsz, rh = by[b"iref"]
        idsz = 2 if data[r0 + rh] == 0 else 4
        for typ, s, _sz, hh in _boxes(data, r0 + rh + 4, r0 + rsz):
            if typ != b"cdsc":
                continue
            frm = _read_uint(data, s + hh, idsz)
            n = _read_uint(data, s + hh + idsz, 2)
            tos = [_read_uint(data, s + hh + idsz + 2 + k * idsz, idsz) for k in range(n)]
            if frm in xmp_items and primary in tos:
                described = frm
                break

    # iloc: every entry, so offsets can move and the XMP item can point at its new bytes
    _, l0, lsz, lh = by[b"iloc"]
    lv = data[l0 + lh]
    o = l0 + lh + 4
    off_size, len_size = data[o] >> 4, data[o] & 15
    base_size, idx_size = data[o + 1] >> 4, (data[o + 1] & 15) if lv in (1, 2) else 0
    if off_size not in (4, 8) or len_size not in (4, 8):
        raise ValueError("unusual iloc sizes")
    o += 2
    id_size = 2 if lv < 2 else 4
    n_items = _read_uint(data, o, id_size)
    o += id_size
    entries = []  # (item id, header bytes, construction method, data ref, base, [(index, offset, length)])
    for _ in range(n_items):
        s = o
        iid = _read_uint(data, o, id_size)
        o += id_size
        cm = 0
        if lv in (1, 2):
            cm = _read_uint(data, o, 2) & 15
            o += 2
        dref = _read_uint(data, o, 2)
        o += 2
        head = data[s:o]
        base = _read_uint(data, o, base_size)
        o += base_size
        n_ext = _read_uint(data, o, 2)
        o += 2
        exts = []
        for _ in range(n_ext):
            idx = _read_uint(data, o, idx_size)
            o += idx_size
            eo = _read_uint(data, o, off_size)
            o += off_size
            el = _read_uint(data, o, len_size)
            o += len_size
            exts.append((idx, eo, el))
        entries.append((iid, head, cm, dref, base, exts))
    if o != l0 + lsz:
        raise ValueError("unreadable iloc")

    old_xmp = b""
    if described is not None:
        entry = next((e for e in entries if e[0] == described), None)
        if not entry or entry[2] != 0 or entry[3] != 0 or not entry[5]:
            raise ValueError("unusual XMP item")
        old_xmp = b"".join(data[entry[4] + eo : entry[4] + eo + el] for _i, eo, el in entry[5])

    # The new XMP's size depends only on the video's, not on where things end up.
    image_end_guess = len(data)  # replaced below once the layout is known
    tail = _sef_heic_tail(mov, image_end_guess)
    video_len = len(tail)
    xmp = _merge_xmp(old_xmp, _xmp_description("image/heic", video_len, 8, timestamp_us))

    new_id = described if described is not None else max(ids, default=0) + 1
    if new_id > 0xFFFF:
        raise ValueError("too many items")
    if described is None:
        infe = _box(b"infe", struct.pack(">HH4s", new_id, 0, b"mime") + b"\x00application/rdf+xml\x00", 2)
        iinf = data[i0 : i0 + ih + 4] + _uint(count + 1, cnt_size) + data[i0 + ih + 4 + cnt_size : i0 + isz] + infe
        iinf = struct.pack(">I", len(iinf)) + iinf[4:]
        if b"iref" in by:
            _, r0, rsz, rh = by[b"iref"]
            idf = ">H" if data[r0 + rh] == 0 else ">I"
            cdsc = _box(b"cdsc", struct.pack(idf, new_id) + struct.pack(">H", 1) + struct.pack(idf, primary))
            iref = data[r0 : r0 + rsz] + cdsc
            iref = struct.pack(">I", len(iref)) + iref[4:]
        else:
            iref = _box(b"iref", _box(b"cdsc", struct.pack(">HHH", new_id, 1, primary)), 0)
    else:
        iinf = data[i0 : i0 + isz]
        iref = data[by[b"iref"][1] : by[b"iref"][1] + by[b"iref"][2]]

    def build_iloc(shift, xmp_offset):
        body = bytearray(data[l0 + lh : l0 + lh + 6])  # version/flags + the four sizes
        body += _uint(n_items + (described is None), id_size)
        meta_end = m_start + m_size
        for iid, head, cm, dref, base, exts in entries:
            if iid == described:  # the XMP item now points at its new bytes
                body += head + _uint(0, base_size) + _uint(1, 2) + _uint(0, idx_size)
                body += _uint(xmp_offset, off_size) + _uint(len(xmp), len_size)
                continue
            moved = cm == 0 and dref == 0
            if moved and base_size and base + (exts[0][1] if exts else 0) >= meta_end:
                base += shift
            body += head + _uint(base, base_size) + _uint(len(exts), 2)
            for idx, eo, el in exts:
                if moved and not base_size and eo >= meta_end:
                    eo += shift
                body += _uint(idx, idx_size) + _uint(eo, off_size) + _uint(el, len_size)
        if described is None:
            body += _uint(new_id, id_size) + (b"\x00\x00" if lv in (1, 2) else b"") + b"\x00\x00"
            body += _uint(0, base_size) + _uint(1, 2) + _uint(0, idx_size)
            body += _uint(xmp_offset, off_size) + _uint(len(xmp), len_size)
        return struct.pack(">I4s", 8 + len(body), b"iloc") + bytes(body)

    def build_meta(shift, xmp_offset):
        parts = []
        for typ, s, sz, _ in children:
            if typ == b"iinf":
                parts.append(iinf)
            elif typ == b"iref":
                parts.append(iref)
            elif typ == b"iloc":
                parts.append(build_iloc(shift, xmp_offset))
            else:
                parts.append(data[s : s + sz])
        if b"iref" not in by:
            parts.insert([c[0] for c in children].index(b"iinf") + 1, iref)
        out = data[m_start : m_start + 12] + b"".join(parts)
        return struct.pack(">I", len(out)) + out[4:]

    shift = len(build_meta(0, 0)) - m_size  # field sizes are fixed, so offsets don't change it
    xmp_offset = len(data) + shift + 8  # inside the new mdat after the original image
    meta_bytes = build_meta(shift, xmp_offset)
    if len(meta_bytes) - m_size != shift:
        raise ValueError("meta size changed")
    image = data[:m_start] + meta_bytes + data[m_start + m_size :] + _box(b"mdat", xmp)
    tail = _sef_heic_tail(mov, len(image))
    if len(tail) != video_len:
        raise ValueError("trailer size changed")
    return image + struct.pack(">I4s", 8 + len(tail), b"mpvd") + tail


class Unsupported(ValueError):
    """The photo isn't HEIC or JPEG inside (a PNG named .heic, say): no motion photo can hold it."""


def still_format(data):
    """ "heic" or "jpeg", from the file's first bytes (not its name), or raise Unsupported."""
    if data[:2] == b"\xff\xd8":
        return "jpeg"
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"mif1", b"msf1", b"heim", b"heis"):
        return "heic"
    kind = "a PNG" if data[:4] == b"\x89PNG" else "not HEIC or JPEG"
    raise Unsupported(f"The photo is {kind} inside; a motion photo needs HEIC or JPEG, so it is left as it is")


def build(still, mov, target_dir):
    """Join a still and its video into one motion photo file in target_dir. Returns its path. Both
    originals are inside it unchanged; only XMP is added to the photo."""
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    raw = Path(still).read_bytes()
    video = Path(mov).read_bytes()
    when = still_time_us(mov)
    stem = Path(still).name.rsplit(".", 1)[0]
    if still_format(raw) == "heic":
        data, name = heic_motion(raw, video, when), f"{stem}.MP.HEIC"
    else:
        data, name = jpeg_motion(raw, video, when), f"{stem}.MP.JPG"
    target = target_dir / name
    target.write_bytes(data)
    st = os.stat(still)
    os.utime(target, (st.st_atime, st.st_mtime))
    return target
