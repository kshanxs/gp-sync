"""Snapchat Memories exports: read the list scripts/snapchat_export.py writes next to the memories,
and work out which memories Google Photos already has. Pure functions, no database or Google calls.

A memory is "in Google" when Google has:
- the same file (its SHA-1, either the file here or the one in Snapchat's zip),
- an item of the same kind taken at the same moment (a snap saved to the camera roll and backed
  up by the phone: Google's date is already right), or
- an item that looks the same (a fingerprint of the picture, or of a video's first frame, compared
  with Google's thumbnail): a camera-roll copy, often with the date it was copied instead.

Google can hold several copies of one memory. One is kept (the original, or one with the right
date) or none (all are later copies: the memory takes their place); every other copy is a
duplicate, kept on the server and deleted from Google. Photos from a camera are never deleted.
"""

import bisect
import json
from pathlib import Path, PurePosixPath

MANIFEST = "snapchat-memories.json"
RUNNING = ".snapchat-export-running"  # the export script is still adding files to the folder
FORMAT = "gp-sync/snapchat-memories"
SAME_MOMENT = 3  # seconds: a camera-roll save is written in the same second as the memory
LOOKS_SAME = 40  # of 256 bits: same picture 0-35 in tests, different pictures 55 and more
DUPLICATE_SAME = 24  # stricter for a further copy, which gets deleted (same picture 1-16 on real copies)
SHAPE_SLACK = 0.03  # width/height may differ this much (Google's sizes round)
PHOTO, VIDEO = 1, 2  # Google's media types


class BadManifest(ValueError):
    pass


def read_manifest(path):
    """The memories listed in a snapchat-memories.json, with paths inside its folder:
    {"export": {...}, "items": [{"file": Path, "id", "kind", "taken", ...}]}."""
    path = Path(path)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise BadManifest(f"Can't read {path.name}: {e}") from e
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise BadManifest(f"{path.name} isn't a Snapchat list made by GP Sync's script")
    folder = path.parent
    items = []
    for raw in data.get("items") or []:
        try:
            rel = PurePosixPath(str(raw["file"]))
            if rel.is_absolute() or ".." in rel.parts:
                continue
            poster = raw.get("poster")
            poster_rel = PurePosixPath(str(poster)) if poster else None
            if poster_rel and (poster_rel.is_absolute() or ".." in poster_rel.parts):
                poster_rel = None
            item = {
                "file": folder / rel,
                "id": str(raw["id"]),
                "kind": "video" if raw.get("kind") == "video" else "photo",
                "taken": float(raw["taken"]),
                "lat": _num(raw.get("lat")),
                "lon": _num(raw.get("lon")),
                "sha1": str(raw.get("sha1") or ""),
                "orig_sha1": str(raw.get("orig_sha1") or ""),
                "width": int(raw.get("width") or 0),
                "height": int(raw.get("height") or 0),
                "poster": folder / poster_rel if poster_rel else None,
            }
        except (KeyError, TypeError, ValueError):
            continue
        items.append(item)
    export = data.get("export") if isinstance(data.get("export"), dict) else {}
    return {"export": export, "items": items}


def _num(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- fingerprints


def dhash(image):
    """A 256-bit fingerprint of a picture (brightness steps on a 17x16 grid), as 64 hex digits.
    It survives resizing and recompression, so a thumbnail matches its original."""
    from PIL import Image, ImageOps

    im = ImageOps.exif_transpose(image).convert("L").resize((17, 16), Image.Resampling.BILINEAR)
    px = im.tobytes()
    bits = 0
    for y in range(16):
        row = px[y * 17 : y * 17 + 17]
        for x in range(16):
            bits = (bits << 1) | (row[x] > row[x + 1])
    return f"{bits:064x}"


def dhash_file(path):
    from PIL import Image

    with Image.open(path) as im:
        im.draft("L", (256, 256))  # JPEG: decode small, much faster
        return dhash(im)


def dhash_bytes(data):
    import io

    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        return dhash(im)


def distance(a, b):
    """How many of the 256 bits differ (0: the same picture)."""
    return (int(a, 16) ^ int(b, 16)).bit_count()


def same_shape(w1, h1, w2, h2):
    """Both are the same shape, either way up (a missing size counts as the same)."""
    if not (w1 and h1 and w2 and h2):
        return True
    a, b = min(w1, h1) / max(w1, h1), min(w2, h2) / max(w2, h2)
    return abs(a - b) <= SHAPE_SLACK * max(a, b)


# ---------------------------------------------------------------- matching


def match(memories, library, prints, local=None):
    """Find every copy of each memory in Google.

    memories: [{"path", "kind", "taken", "dedups": [dedup keys], "dhash", "width", "height"}]
    library:  [(media key, dedup key, type, taken (seconds), width, height)] (visible items)
    prints:   {media key: dhash} for library items whose thumbnail was fingerprinted
    local:    {path: dhash} for photos on this server that aren't in Google

    Returns {path: {"match", "copies": [{"key", "dedup", "taken", "how", "distance"}], "local"}}:
    match is how the best copy was found ("same_file" | "same_moment" | "looks_same"), or "local"
    (a file here, not in Google) or "none". Each Google item belongs to one memory at most."""
    by_dedup = {}
    for row in library:
        by_dedup.setdefault(row[1], []).append(row)
    timeline = sorted((r for r in library if r[3]), key=lambda r: r[3])
    times = [r[3] for r in timeline]
    used = set()
    copies = {m["path"]: [] for m in memories}

    def add(m, row, how, dist=None):
        used.add(row[0])
        copies[m["path"]].append({"key": row[0], "dedup": row[1], "taken": row[3], "how": how, "distance": dist})

    # 1. The same file (the exported file, or the one in Snapchat's zip)
    for m in memories:
        for d in m["dedups"]:
            for row in by_dedup.get(d, []):
                if row[0] not in used:
                    add(m, row, "same_file", 0)
    # 2. Taken in the same second: one item per memory, closest first (two snaps saved a second
    #    apart each keep their own). Further copies must be the same file or look the same.
    near = []
    for m in memories:
        if copies[m["path"]]:
            continue
        want = VIDEO if m["kind"] == "video" else PHOTO
        i = bisect.bisect_left(times, m["taken"] - SAME_MOMENT)
        while i < len(timeline) and times[i] <= m["taken"] + SAME_MOMENT:
            row = timeline[i]
            if row[2] == want and row[0] not in used and same_shape(m["width"], m["height"], row[4], row[5]):
                near.append((abs(row[3] - m["taken"]), m["path"], row))
            i += 1
    near.sort(key=lambda x: (x[0], x[1]))
    by_path = {m["path"]: m for m in memories}
    for _gap, path, row in near:
        if row[0] not in used and not copies[path]:
            add(by_path[path], row, "same_moment")
    # 3. Looks the same: closest pairs first, so two similar snaps don't take each other's copy. A
    #    further copy of a memory already found must look closer still: it gets deleted.
    rows = {r[0]: r for r in library}
    lib_prints = [(key, int(fp, 16), rows[key]) for key, fp in prints.items() if fp and key in rows]
    pairs = []
    for m in memories:
        if not m.get("dhash"):
            continue
        want = VIDEO if m["kind"] == "video" else PHOTO
        mine = int(m["dhash"], 16)
        for key, fp, row in lib_prints:
            if row[2] != want or key in used:
                continue
            d = (mine ^ fp).bit_count()
            if d <= LOOKS_SAME and same_shape(m["width"], m["height"], row[4], row[5]):
                pairs.append((d, m["path"], key))
    pairs.sort()
    for d, path, key in pairs:
        if key in used or (copies[path] and d > DUPLICATE_SAME):
            continue
        add(by_path[path], rows[key], "looks_same", d)
    # 4. A photo here that isn't in Google yet (backing it up too would make a duplicate)
    local_prints = [(p, int(fp, 16)) for p, fp in (local or {}).items() if fp]
    taken_local = set()
    out = {}
    for m in memories:
        found = copies[m["path"]]
        if found:
            best = min(found, key=lambda c: HOW_ORDER.index(c["how"]))
            out[m["path"]] = {"match": best["how"], "copies": found, "local": None}
            continue
        best = None
        if m.get("dhash") and m["kind"] == "photo":
            mine = int(m["dhash"], 16)
            for lpath, fp in local_prints:
                d = (mine ^ fp).bit_count()
                if d <= LOOKS_SAME and lpath not in taken_local and (best is None or d < best[0]):
                    best = (d, lpath)
        if best:
            taken_local.add(best[1])
        out[m["path"]] = {"match": "local" if best else "none", "copies": [], "local": best[1] if best else None}
    return out


HOW_ORDER = ("same_file", "same_moment", "looks_same")


def plan(action, copies, taken, slack):
    """Which of a memory's copies in Google stays and which go, for an action.

    copies: [{"key", "taken", "original"}] (original: from a camera). Returns (copy kept or None,
    copies to delete). Copies from a camera are never deleted."""
    if action == "skip" or not copies:
        return None, []
    deletable = [c for c in copies if not c.get("original")]
    if action == "replace" or action == "upload":
        return None, deletable
    # album: keep the original (from a camera, or older than the memory), else the closest date
    originals = [c for c in copies if c.get("original") or (c["taken"] and c["taken"] < taken - slack)]
    if originals:
        keep = min(originals, key=lambda c: c["taken"] or float("inf"))
    else:
        keep = min(copies, key=lambda c: abs((c["taken"] or 0) - taken))
    return keep, [c for c in deletable if c is not keep]


def action_for(found, taken, slack, originals=frozenset()):
    """What to do with a memory:

    - "album": Google has the original (from a camera, its key in `originals`, or older than the
      memory: a camera-roll photo later saved to Snapchat keeps its own date), or a copy with the
      right date. It stays (in your own albums too) and goes into the album; any other copies are
      duplicates.
    - "replace": Google only has later copies (camera-roll saves backed up days later). The memory,
      with Snapchat's date, time and place inside, takes their place.
    - "upload": Google doesn't have it."""
    copies = found.get("copies") or []
    if not copies:
        return "upload"
    for c in copies:
        t = c["taken"]
        if c["key"] in originals or not t or t < taken - slack or abs(t - taken) <= slack:
            return "album"
    return "replace"


SHAPE_MEMORIES = 5  # a shape this few memories have isn't searched by look (it would mean thousands of thumbnails)


def thumbs_wanted(memories, library, found, since_slack=86400):
    """Library items worth fingerprinting: not yet a copy of a memory, the same kind and shape as at
    least SHAPE_MEMORIES memories (a handful of odd shapes, such as one 4:3 memory, would otherwise
    pull in every camera photo), and dated no earlier than the oldest memory (a copy is never older
    than the memory). Items without a camera come first: camera-roll saves of snaps are the likely
    copies. Media keys, in that order, newest first."""
    if not memories:
        return []
    used = {c["key"] for f in found.values() for c in f.get("copies") or []}
    oldest = min(m["taken"] for m in memories) - since_slack
    buckets = {}
    for m in memories:
        s = _shape(m["width"], m["height"])
        if s is not None:
            key = (VIDEO if m["kind"] == "video" else PHOTO, round(s, 2))
            buckets[key] = buckets.get(key, 0) + 1
    shapes = [(k, s) for (k, s), n in buckets.items() if n >= SHAPE_MEMORIES]
    out = []
    for row in library:
        key, _dedup, kind, taken, w, h = row[:6]
        if key in used or (taken or 0) < oldest or kind not in (PHOTO, VIDEO):
            continue
        s = _shape(w, h)
        if s is None or any(k == kind and abs(t - s) <= SHAPE_SLACK * max(t, s) for k, t in shapes):
            out.append(row)
    # row[6], when given, is the camera that took it
    out.sort(key=lambda r: (bool(r[6]) if len(r) > 6 else False, -(r[3] or 0)))
    return [r[0] for r in out]


def _shape(w, h):
    return min(w, h) / max(w, h) if w and h else None
