#!/usr/bin/env python3
"""Turn a Snapchat "Download My Data" Memories export into a folder GP Sync can back up.

Snapchat splits the export into several zips (mydata~123.zip, mydata~123-2.zip, ...). The photos
and videos inside carry no date of their own, but the export knows the exact moment and place of
every memory: json/memories_history.json lists them, and each file's time in the zip is the same
moment. This script:

- extracts every memory into OUT/<year>/, named by its local time,
- writes the date, time and GPS into each file (EXIF for photos, QuickTime keys for videos) and
  sets the file's modified time to that moment, so any app shows the right date,
- burns the text and stickers layer (the "-overlay.png" Snapchat keeps apart) into the picture,
  as Snapchat shows it (--no-overlays keeps the plain picture),
- saves the first frame of each video in OUT/.posters/ (GP Sync compares it with Google's
  thumbnails to find videos already in Google Photos),
- leaves OUT/.snapchat-export-running while it works: GP Sync backs up nothing in the folder
  until every file is listed (so it can run straight into a backed-up folder),
- writes OUT/snapchat-memories.json: one entry per memory with its exact time, place and
  fingerprints. GP Sync's Snapchat page reads it: memories already in Google Photos aren't
  uploaded twice, ones Google has with a wrong date are replaced, and all go into an album.

Run it again with more zips (a part that failed to download, a newer export): what is already
there is kept and only new memories are added.

Needs Python 3.11+, exiftool and ffmpeg (with ffprobe). Nothing else is installed or changed:
the zips are only read.

    python3 scripts/snapchat_export.py ~/Downloads ~/Photos/Snapchat
"""

from __future__ import annotations

import argparse
import bisect
import contextlib
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from zoneinfo import ZoneInfo

MANIFEST = "snapchat-memories.json"
RUNNING = ".snapchat-export-running"  # GP Sync holds back the folder while this is here
FORMAT = "gp-sync/snapchat-memories"
POSTERS = ".posters"
WORK = ".partial"
POSTER_WIDTH = 360
MATCH_SECONDS = 3  # zip times are rounded to 2 seconds
MEDIA_NAME = re.compile(r"(?:^|/)(\d{4}-\d{2}-\d{2})_([0-9A-Za-z-]+)-main\.(\w+)$")
LOCATION = re.compile(r"(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)")
MAGIC = (
    (b"\xff\xd8\xff", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"RIFF", "webp"),
    (b"GIF8", "gif"),
)


def say(msg):
    print(time.strftime("%H:%M:%S ") + msg, flush=True)


# ---------------------------------------------------------------- reading the export


def find_zips(folders):
    """Every zip under the given folders that holds Snapchat memories or their list."""
    found = []
    for folder in folders:
        folder = Path(folder).expanduser()
        paths = [folder] if folder.is_file() else sorted(folder.rglob("*.zip"))
        for p in paths:
            try:
                with zipfile.ZipFile(p) as z:
                    names = z.namelist()
            except (zipfile.BadZipFile, OSError):
                continue
            if any(n.startswith("memories/") or n.endswith("memories_history.json") for n in names):
                found.append(p)
    return found


def parse_history(raw):
    """[(unix time, "photo"|"video", lat, lon)] from memories_history.json. A 0, 0 location means
    Snapchat didn't record one."""
    out = []
    for e in raw.get("Saved Media") or []:
        try:
            when = dt.datetime.strptime(e["Date"].replace(" UTC", ""), "%Y-%m-%d %H:%M:%S")
        except (KeyError, ValueError):
            continue
        kind = "video" if str(e.get("Media Type", "")).lower().startswith("video") else "photo"
        lat = lon = None
        m = LOCATION.search(str(e.get("Location") or ""))
        if m:
            lat, lon = float(m.group(1)), float(m.group(2))
            if abs(lat) < 1e-6 and abs(lon) < 1e-6:
                lat = lon = None
        out.append((when.replace(tzinfo=dt.UTC).timestamp(), kind, lat, lon))
    return out


def zip_entries(zips):
    """Memories in the zips: {date_id: {"id", "zip", "name", "kind", "time", "overlay", "size"}}. The time in
    the zip is the memory's moment in UTC."""
    found = {}
    overlays = {}
    for zp in zips:
        with zipfile.ZipFile(zp) as z:
            for info in z.infolist():
                name = info.filename
                if name.endswith("-overlay.png"):
                    overlays[name.rsplit("/", 1)[-1][: -len("-overlay.png")]] = (zp, name)
                    continue
                m = MEDIA_NAME.search(name)
                if not m:
                    continue
                ext = m.group(3).lower()
                kind = "video" if ext in ("mp4", "mov") else "photo"
                when = dt.datetime(*info.date_time, tzinfo=dt.UTC).timestamp()
                prefix = name.rsplit("/", 1)[-1][: -len(f"-main.{m.group(3)}")]
                # Keyed by date and id: Snapchat lists a memory saved twice as two memories.
                found[prefix] = {
                    "id": m.group(2),
                    "zip": zp,
                    "name": name,
                    "kind": kind,
                    "time": when,
                    "size": info.file_size,
                    "prefix": prefix,
                }
    for entry in found.values():
        entry["overlay"] = overlays.get(entry["prefix"])
    return found


def match_history(entries, history):
    """Give each file its memory from the list: same kind, within MATCH_SECONDS, closest first.
    Returns {id: (time, lat, lon, "list")}; files with no match keep their zip time ("zip")."""
    pairs = []
    by_kind = {}
    for i, (t, kind, _lat, _lon) in enumerate(history):
        by_kind.setdefault(kind, []).append((t, i))
    for lst in by_kind.values():
        lst.sort()
    for mid, e in entries.items():
        lst = by_kind.get(e["kind"], [])
        times = [t for t, _ in lst]
        j = bisect.bisect_left(times, e["time"] - MATCH_SECONDS)
        while j < len(lst) and lst[j][0] <= e["time"] + MATCH_SECONDS:
            pairs.append((abs(lst[j][0] - e["time"]), mid, lst[j][1]))
            j += 1
    pairs.sort()
    used_files, used_rows, out = set(), set(), {}
    for _gap, mid, i in pairs:
        if mid in used_files or i in used_rows:
            continue
        used_files.add(mid)
        used_rows.add(i)
        t, _kind, lat, lon = history[i]
        out[mid] = (t, lat, lon, "list")
    for mid, e in entries.items():
        out.setdefault(mid, (e["time"], None, None, "zip"))
    missing = [history[i] for i in range(len(history)) if i not in used_rows]
    return out, missing


# ---------------------------------------------------------------- tools


class ExifTool:
    """One exiftool process for the whole run (starting it per file is slow)."""

    def __init__(self):
        self.p = subprocess.Popen(
            ["exiftool", "-stay_open", "True", "-@", "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    def run(self, *args):
        self.p.stdin.write("\n".join(args) + "\n-execute\n")
        self.p.stdin.flush()
        out = []
        for line in self.p.stdout:
            if line.strip() == "{ready}":
                break
            out.append(line)
        text = "".join(out)
        if "error" in text.lower() and "files updated" not in text:
            raise RuntimeError(text.strip())
        return text

    def close(self):
        with contextlib.suppress(Exception):
            self.p.stdin.write("-stay_open\nFalse\n")
            self.p.stdin.flush()
            self.p.wait(10)


def ffmpeg(*args):
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", *args], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr.strip()[-500:])


def size_of(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )  # fmt: skip
    try:
        w, h = (int(x) for x in r.stdout.strip().splitlines()[0].split(",")[:2])
        return w, h
    except (ValueError, IndexError):
        return None, None


def sha1_of(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while block := f.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


def real_ext(path, ext):
    """Snapchat names a few PNGs .jpg: use what the file really is."""
    with open(path, "rb") as f:
        head = f.read(12)
    for magic, kind in MAGIC:
        if head.startswith(magic) and (kind != "webp" or head[8:12] == b"WEBP"):
            return kind
    return ext


# ---------------------------------------------------------------- writing one memory


def burn_overlay(src, overlay, dst, kind):
    """Put Snapchat's text and stickers layer on top, scaled to the picture."""
    graph = "[1:v][0:v]scale2ref[ov][base];[base][ov]overlay=0:0"
    if kind == "video":
        ffmpeg("-i", str(src), "-i", str(overlay), "-filter_complex",
               graph + ",scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
               "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-c:a", "copy",
               "-map_metadata", "0", "-movflags", "+faststart", str(dst))  # fmt: skip
    else:
        ffmpeg("-i", str(src), "-i", str(overlay), "-filter_complex", graph,
               "-q:v", "2", "-frames:v", "1", "-update", "1", str(dst))  # fmt: skip


def write_metadata(exif, path, kind, taken, lat, lon, zone):
    local = dt.datetime.fromtimestamp(taken, zone)
    stamp = local.strftime("%Y:%m:%d %H:%M:%S")
    off = local.strftime("%z")
    off = f"{off[:3]}:{off[3:]}"
    args = ["-overwrite_original", "-m", "-P"]
    if kind == "video":
        args += ["-api", "QuickTimeUTC=1"]
        for tag in ("CreateDate", "ModifyDate", "TrackCreateDate", "TrackModifyDate", "MediaCreateDate",
                    "MediaModifyDate"):  # fmt: skip
            args.append(f"-QuickTime:{tag}={stamp}{off}")
        args.append(f"-Keys:CreationDate={stamp}{off}")
        if lat is not None:
            args += [f"-Keys:GPSCoordinates={lat}, {lon}, 0", f"-UserData:GPSCoordinates={lat}, {lon}, 0"]
    else:
        for tag in ("DateTimeOriginal", "CreateDate", "ModifyDate"):
            args.append(f"-{tag}={stamp}")
        for tag in ("OffsetTimeOriginal", "OffsetTimeDigitized", "OffsetTime"):
            args.append(f"-{tag}={off}")
        if lat is not None:
            args += [
                f"-GPSLatitude={abs(lat)}",
                f"-GPSLatitudeRef={'N' if lat >= 0 else 'S'}",
                f"-GPSLongitude={abs(lon)}",
                f"-GPSLongitudeRef={'E' if lon >= 0 else 'W'}",
            ]
        args.append("-XMP-dc:Source=Snapchat Memories")
    exif.run(*args, str(path))


def poster(video, target):
    """The first frame, small: Google's thumbnail of a video is its first frame."""
    target.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg("-i", str(video), "-frames:v", "1", "-vf", f"scale={POSTER_WIDTH}:-2", "-q:v", "3",
           "-update", "1", str(target))  # fmt: skip


def export_one(mid, e, when, out, work, exif, zone, overlays):
    taken, lat, lon, time_from = when
    local = dt.datetime.fromtimestamp(taken, zone)
    raw = work / f"{mid}.raw"
    with zipfile.ZipFile(e["zip"]) as z, z.open(e["name"]) as src, open(raw, "wb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
    orig_sha1 = sha1_of(raw)
    ext = real_ext(raw, e["name"].rsplit(".", 1)[-1].lower())
    final = work / f"{mid}.{ext}"
    with_overlay = False
    if overlays and e["overlay"]:
        ov = work / f"{mid}.overlay"
        with zipfile.ZipFile(e["overlay"][0]) as z:
            ov.write_bytes(z.read(e["overlay"][1]))
        try:
            burn_overlay(raw, ov, final, e["kind"])
            with_overlay = True
        except RuntimeError as err:
            say(f"  overlay skipped for {mid}: {err}")
        ov.unlink(missing_ok=True)
    if not with_overlay:
        raw.replace(final)
    raw.unlink(missing_ok=True)
    write_metadata(exif, final, e["kind"], taken, lat, lon, zone)
    os.utime(final, (taken, taken))
    width, height = size_of(final)
    short = e["id"][:8]
    rel = f"{local:%Y}/Snapchat {local:%Y-%m-%d %H.%M.%S} {short}.{ext}"
    target = out / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    n = 1
    while target.exists():
        rel = f"{local:%Y}/Snapchat {local:%Y-%m-%d %H.%M.%S} {short} ({n}).{ext}"
        target = out / rel
        n += 1
    poster_rel = None
    if e["kind"] == "video":
        poster_rel = f"{POSTERS}/{mid}.jpg"
        try:
            poster(final, out / poster_rel)
        except RuntimeError as err:
            say(f"  no poster for {mid}: {err}")
            poster_rel = None
    item = {
        "file": rel,
        "id": mid,  # date_id: unique even for a memory Snapchat lists twice
        "kind": e["kind"],
        "taken": int(taken),
        "time_from": time_from,
        "lat": lat,
        "lon": lon,
        "overlay": with_overlay,
        "sha1": sha1_of(final),
        "orig_sha1": orig_sha1,
        "size": final.stat().st_size,
        "width": width,
        "height": height,
        "poster": poster_rel,
    }
    os.replace(final, target)  # appears in one go, already dated
    return item


# ---------------------------------------------------------------- manifest


def load_manifest(out):
    p = out / MANIFEST
    if not p.exists():
        return {}
    data = json.loads(p.read_text())
    if data.get("format") != FORMAT:
        raise SystemExit(f"{p} isn't a GP Sync Snapchat list")
    return {i["id"]: i for i in data.get("items", []) if (out / i["file"]).exists()}


def save_manifest(out, items, history, missing, zips, zone_name):
    months = {}
    for t, *_ in missing:
        m = time.strftime("%Y-%m", time.gmtime(t))
        months[m] = months.get(m, 0) + 1
    data = {
        "format": FORMAT,
        "version": 1,
        "updated": int(time.time()),
        "timezone": zone_name,
        "export": {
            "memories": len(history),
            "found": len(items),
            "missing": len(missing),
            "missing_months": dict(sorted(months.items())),
            "zips": sorted(p.name for p in zips),
        },
        "items": sorted(items.values(), key=lambda i: i["taken"]),
    }
    tmp = out / (MANIFEST + ".part")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, out / MANIFEST)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("source", nargs="+", help="folders (or zips) with the Snapchat export")
    ap.add_argument("out", help="where the memories go, e.g. Photos/Snapchat")
    ap.add_argument("--timezone", default="", help="for file names and photo dates (default: this machine's)")
    ap.add_argument("--no-overlays", action="store_true", help="keep the plain picture, without text and stickers")
    ap.add_argument("--limit", type=int, default=0, help="stop after this many new memories (for a test)")
    args = ap.parse_args(argv)

    for tool in ("exiftool", "ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise SystemExit(f"{tool} is needed (apt install libimage-exiftool-perl ffmpeg)")
    zone_name = args.timezone or os.environ.get("TZ") or _system_zone()
    zone = ZoneInfo(zone_name) if zone_name else dt.datetime.now().astimezone().tzinfo
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    zips = find_zips(args.source)
    if not zips:
        raise SystemExit("No Snapchat export zips found")
    history = []
    for zp in zips:
        with zipfile.ZipFile(zp) as z:
            name = next((n for n in z.namelist() if n.endswith("memories_history.json")), None)
            if name:
                history = parse_history(json.loads(z.read(name)))
                break
    entries = zip_entries(zips)
    times, missing = match_history(entries, history)
    say(f"{len(zips)} zips: {len(entries)} memories, {len(history)} in Snapchat's list, {len(missing)} not in them")

    done = load_manifest(out)
    todo = [mid for mid in sorted(entries, key=lambda m: times[m][0]) if mid not in done]
    if args.limit:
        todo = todo[: args.limit]
    say(f"{len(done)} already here, {len(todo)} to add")
    work = out / WORK
    work.mkdir(exist_ok=True)
    running = out / RUNNING
    running.write_text(f"{os.getpid()} {int(time.time())}\n")
    exif = ExifTool()
    failed = 0
    try:
        for n, mid in enumerate(todo, 1):
            try:
                done[mid] = export_one(mid, entries[mid], times[mid], out, work, exif, zone, not args.no_overlays)
            except Exception as err:  # one bad file doesn't stop the rest
                failed += 1
                say(f"  failed {entries[mid]['name']}: {type(err).__name__}: {err}")
            if n % 100 == 0 or n == len(todo):
                save_manifest(out, done, history, missing, zips, zone_name)
                say(f"{n}/{len(todo)}")
    finally:
        exif.close()
        save_manifest(out, done, history, missing, zips, zone_name)
        running.unlink(missing_ok=True)  # every file is listed now
        with contextlib.suppress(OSError):
            for p in work.iterdir():
                p.unlink()
            work.rmdir()
    say(f"Done: {len(done)} memories in {out}" + (f", {failed} failed (run again to retry)" if failed else ""))
    if missing:
        months = sorted({time.strftime("%Y-%m", time.gmtime(t)) for t, *_ in missing})
        say(f"{len(missing)} memories aren't in these zips ({months[0]} to {months[-1]}): download the missing parts")
    return 1 if failed else 0


def _system_zone():
    with contextlib.suppress(OSError):
        link = os.readlink("/etc/localtime")
        if "zoneinfo/" in link:
            return link.split("zoneinfo/", 1)[1]
    with contextlib.suppress(OSError):
        return Path("/etc/timezone").read_text().strip()
    return ""


if __name__ == "__main__":
    sys.exit(main())
