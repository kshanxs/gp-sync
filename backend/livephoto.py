"""iPhone Live Photos: find the still and the short video that belong together, and join them into one
Google-style motion photo (https://developer.android.com/media/platform/motion-photo-format).

Apple saves a Live Photo as two files linked by a content identifier stored in both: in the still's
Apple maker note (tag 0x11) and in the video's QuickTime metadata. Google Photos shows a motion photo
only when it is one file: the still with the video appended and XMP saying where the video starts.
The video is copied from MOV into MP4 without re-encoding. A JPEG still keeps its bytes (XMP is
added next to them); a HEIC still becomes a high-quality JPEG first, because Google Photos doesn't
show HEIC motion photos as motion (heic_motion builds one to the spec, kept for when it does)."""

import contextlib
import os
import re
import struct
import tempfile
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


# ---- Video: MOV to MP4 without re-encoding


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


def _child(data, box, name):
    _, start, size, head = box
    return next((b for b in _boxes(data, start + head, start + size) if b[0] == name), None)


def _video_matrix(data):
    """The display matrix (36 bytes) of the first video track in a MOV/MP4, or None."""
    moov = next((b for b in _boxes(data) if b[0] == b"moov"), None)
    if not moov:
        return None, None
    for trak in _boxes(data, moov[1] + moov[3], moov[1] + moov[2]):
        if trak[0] != b"trak":
            continue
        mdia = _child(data, trak, b"mdia")
        hdlr = mdia and _child(data, mdia, b"hdlr")
        if not hdlr or data[hdlr[1] + hdlr[3] + 8 : hdlr[1] + hdlr[3] + 12] != b"vide":
            continue
        tkhd = _child(data, trak, b"tkhd")
        if not tkhd:
            return None, None
        body = tkhd[1] + tkhd[3]
        # version 1: 8-byte times; the matrix comes after the times, track ID, reserved, duration,
        # reserved (8), layer, alternate group, volume and reserved (8 bytes in all).
        off = body + (4 + 8 + 8 + 4 + 4 + 8 if data[body] == 1 else 4 + 4 + 4 + 4 + 4 + 4)
        off += 8 + 2 + 2 + 2 + 2
        return off, data[off : off + 36]
    return None, None


def mov_to_mp4(mov, target):
    """Copy the video and audio of a MOV into an MP4 file (no re-encoding), keeping its rotation."""
    import av

    with av.open(str(mov)) as src, av.open(str(target), "w", format="mp4") as dst:
        streams = {}
        v = src.streams.video[0]
        streams[v.index] = dst.add_stream_from_template(v)
        if src.streams.audio:
            a = src.streams.audio[0]
            streams[a.index] = dst.add_stream_from_template(a)
        last = {}  # stream -> last decode time written
        for packet in src.demux(*[src.streams[i] for i in streams]):
            if packet.dts is None:
                continue
            # A few iPhone videos have a frame shown a moment before it is decoded (pts < dts) or a
            # decode time that doesn't move on; MP4 refuses both, so nudge them (a few milliseconds).
            i = packet.stream.index
            if i in last and packet.dts <= last[i]:
                packet.dts = last[i] + 1
            if packet.pts is not None and packet.pts < packet.dts:
                packet.pts = packet.dts
            last[i] = packet.dts
            packet.stream = streams[i]
            dst.mux(packet)
    # The track's display matrix (how the phone was held) isn't carried over by a stream copy here.
    src_bytes = Path(mov).read_bytes()
    _, matrix = _video_matrix(src_bytes)
    out = bytearray(Path(target).read_bytes())
    off, current = _video_matrix(bytes(out))
    if matrix and off is not None and current != matrix:
        out[off : off + 36] = matrix
        Path(target).write_bytes(bytes(out))
    return target


# ---- Motion photo XMP


def _xmp_description(mime, video_len, padding, timestamp_us):
    return (
        '<rdf:Description rdf:about="" xmlns:GCamera="http://ns.google.com/photos/1.0/camera/"'
        ' xmlns:Container="http://ns.google.com/photos/1.0/container/"'
        ' xmlns:Item="http://ns.google.com/photos/1.0/container/item/"'
        f' GCamera:MotionPhoto="1" GCamera:MotionPhotoVersion="1"'
        f' GCamera:MotionPhotoPresentationTimestampUs="{timestamp_us}"'
        f' GCamera:MicroVideo="1" GCamera:MicroVideoVersion="1" GCamera:MicroVideoOffset="{video_len}"'
        f' GCamera:MicroVideoPresentationTimestampUs="{timestamp_us}">'
        "<Container:Directory><rdf:Seq>"
        f'<rdf:li rdf:parseType="Resource"><Container:Item Item:Mime="{mime}" Item:Semantic="Primary"'
        f' Item:Length="0" Item:Padding="{padding}"/></rdf:li>'
        '<rdf:li rdf:parseType="Resource"><Container:Item Item:Mime="video/mp4" Item:Semantic="MotionPhoto"'
        f' Item:Length="{video_len}" Item:Padding="0"/></rdf:li>'
        "</rdf:Seq></Container:Directory></rdf:Description>"
    )


def _xmp_packet(description):
    return (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        f"{description}</rdf:RDF></x:xmpmeta>"
    ).encode()


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


def jpeg_motion(jpg, mp4, timestamp_us):
    """A JPEG motion photo: the still's bytes with motion-photo XMP, then the MP4."""
    desc = _xmp_description("image/jpeg", len(mp4), 0, timestamp_us)
    segments = list(_jpeg_segments(jpg))
    xmp = next((s for s in segments if s[0] == 0xE1 and jpg[s[1] + 4 : s[1] + 4 + len(_XMP_SIG)] == _XMP_SIG),
               None)  # fmt: skip
    if xmp:  # add our description to the photo's own XMP, keeping what is there
        _, start, end = xmp
        packet = jpg[start + 4 + len(_XMP_SIG) : end]
        close = packet.rfind(b"</rdf:RDF>")
        if close < 0:
            raise ValueError("unreadable XMP")
        packet = packet[:close] + desc.encode() + packet[close:]
        payload = _XMP_SIG + packet
        if len(payload) + 2 > 0xFFFF:
            raise ValueError("XMP too large")
        seg = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
        return jpg[:start] + seg + jpg[end:] + mp4
    # No XMP yet: after the leading APPn segments (EXIF first, as cameras write it).
    at = 2
    for marker, _start, end in segments:
        if not 0xE0 <= marker <= 0xEF:
            break
        at = end
    payload = _XMP_SIG + _xmp_packet(desc)
    seg = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    return jpg[:at] + seg + jpg[at:] + mp4


def _box(typ, body, version=None, flags=0):
    head = b"" if version is None else struct.pack(">I", (version << 24) | flags)
    return struct.pack(">I4s", 8 + len(head) + len(body), typ) + head + body


def _uint(n, size):
    return n.to_bytes(size, "big") if size else b""


def _read_uint(data, off, size):
    return int.from_bytes(data[off : off + size], "big") if size else 0


def heic_motion(heic, mp4, timestamp_us):
    """A HEIC motion photo: an XMP item added to the HEIF meta box (existing items untouched, their
    file offsets moved along), the XMP bytes in a new mdat, and the MP4 in a final 'mpvd' box."""
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

    # iinf: entry count, then infe boxes
    _, i0, isz, ih = by[b"iinf"]
    iv = data[i0 + ih]
    cnt_size = 2 if iv == 0 else 4
    count = _read_uint(data, i0 + ih + 4, cnt_size)
    ids = []
    for typ, s, _sz, hh in _boxes(data, i0 + ih + 4 + cnt_size, i0 + isz):
        if typ == b"infe":
            v = data[s + hh]
            ids.append(_read_uint(data, s + hh + 4, 4 if v >= 3 else 2))
    new_id = max(ids, default=0) + 1
    if new_id > 0xFFFF:
        raise ValueError("too many items")
    infe = _box(b"infe", struct.pack(">HH4s", new_id, 0, b"mime") + b"\x00application/rdf+xml\x00", 2)
    iinf = (data[i0 : i0 + ih + 4] + _uint(count + 1, cnt_size) + data[i0 + ih + 4 + cnt_size : i0 + isz]) + infe
    iinf = struct.pack(">I", len(iinf)) + iinf[4:]

    # iref: add "cdsc" (content describes) from the XMP item to the primary image
    if b"iref" in by:
        _, r0, rsz, rh = by[b"iref"]
        rv = data[r0 + rh]
        idf = ">H" if rv == 0 else ">I"
        cdsc = _box(b"cdsc", struct.pack(idf, new_id) + struct.pack(">H", 1) + struct.pack(idf, primary))
        iref = data[r0 : r0 + rsz] + cdsc
        iref = struct.pack(">I", len(iref)) + iref[4:]
    else:
        iref = _box(b"iref", _box(b"cdsc", struct.pack(">HHH", new_id, 1, primary)), 0)

    # iloc: parse every entry so file offsets can move, then add the XMP item's entry
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
    entries = []  # (header bytes before base offset, cm, base, [(index, offset, length)])
    for _ in range(n_items):
        s = o
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
        entries.append((head, cm, dref, base, exts))
    if o != l0 + lsz:
        raise ValueError("unreadable iloc")

    def build_iloc(shift, xmp_offset, xmp_len):
        body = bytearray(data[l0 + lh : l0 + lh + 6])  # version/flags + the four sizes
        body += _uint(n_items + 1, id_size)
        meta_end = m_start + m_size
        for head, cm, dref, base, exts in entries:
            moved = cm == 0 and dref == 0
            if moved and base_size and base + (exts[0][1] if exts else 0) >= meta_end:
                base += shift
            body += head + _uint(base, base_size) + _uint(len(exts), 2)
            for idx, eo, el in exts:
                if moved and not base_size and eo >= meta_end:
                    eo += shift
                body += _uint(idx, idx_size) + _uint(eo, off_size) + _uint(el, len_size)
        body += _uint(new_id, id_size) + (b"\x00\x00" if lv in (1, 2) else b"") + b"\x00\x00"
        body += _uint(0, base_size) + _uint(1, 2) + _uint(0, idx_size)
        body += _uint(xmp_offset, off_size) + _uint(xmp_len, len_size)
        return struct.pack(">I4s", 8 + len(body), b"iloc") + bytes(body)

    desc = _xmp_description("image/heic", len(mp4), 8, timestamp_us)
    xmp = _xmp_packet(desc)

    def build_meta(shift, xmp_offset):
        parts = []
        for typ, s, sz, _ in children:
            if typ == b"iinf":
                parts.append(iinf)
            elif typ == b"iref":
                parts.append(iref)
            elif typ == b"iloc":
                parts.append(build_iloc(shift, xmp_offset, len(xmp)))
            else:
                parts.append(data[s : s + sz])
        if b"iref" not in by:
            parts.insert([c[0] for c in children].index(b"iinf") + 1, iref)
        body = b"".join(parts)
        return data[m_start : m_start + 12] + body

    # The meta box's new size doesn't depend on the offsets written into it (fixed-size fields).
    trial = build_meta(0, 0)
    shift = (len(trial)) - m_size
    xmp_offset = len(data) + shift + 8  # inside the new mdat after the original file
    meta_bytes = build_meta(shift, xmp_offset)
    meta_bytes = struct.pack(">I", len(meta_bytes)) + meta_bytes[4:]
    if len(meta_bytes) - m_size != shift:
        raise ValueError("meta size changed")
    out = data[:m_start] + meta_bytes + data[m_start + m_size :]
    out += _box(b"mdat", xmp)
    out += _box(b"mpvd", mp4)
    return out


JPEG_QUALITY = 95


def heic_to_jpeg(heic):
    """The HEIC still as a high-quality JPEG with its EXIF (dates, place, camera, Apple's maker note)
    and colour profile. Google Photos doesn't treat a HEIC motion photo as one (tried 2026-11-16: a
    HEIC built to the spec stayed a still, the same photo as JPEG became a motion photo), so a HEIC
    Live Photo goes up as a JPEG motion photo. The pixels come rotated the right way up (pillow-heif
    applies the HEIF rotation and sets the EXIF orientation to 1)."""
    import io

    from PIL import Image
    from pillow_heif import register_heif_opener

    register_heif_opener()
    with Image.open(io.BytesIO(heic)) as im:
        im.load()
        exif = im.getexif()
        exif[0x0112] = 1
        rgb = im.convert("RGB")
        out = io.BytesIO()
        rgb.save(out, "JPEG", quality=JPEG_QUALITY, subsampling=0, exif=exif.tobytes(),
                 icc_profile=im.info.get("icc_profile") or None)  # fmt: skip
        return out.getvalue()


def presentation_us(seconds):
    """The still sits in the middle of an Apple Live Photo's video."""
    return int(seconds * 1e6 / 2) if seconds else 0


def build(still, mov, target_dir):
    """Join a still and its video into one motion photo file in target_dir. Returns its path."""
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    e = _ext(still)
    _, seconds = video_info(mov)
    fd, tmp = tempfile.mkstemp(suffix=".mp4", dir=target_dir)
    os.close(fd)
    try:
        mov_to_mp4(mov, tmp)
        mp4 = Path(tmp).read_bytes()
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
    raw = Path(still).read_bytes()
    stem = Path(still).name.rsplit(".", 1)[0]
    if e in ("heic", "heif"):
        raw = heic_to_jpeg(raw)
    data, name = jpeg_motion(raw, mp4, presentation_us(seconds)), f"{stem}.MP.jpg"
    target = target_dir / name
    target.write_bytes(data)
    st = os.stat(still)
    os.utime(target, (st.st_atime, st.st_mtime))
    return target
