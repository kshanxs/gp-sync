"""Copy every photo and video in the mounted media folders to Google Photos.

Files are never changed: folders should be mounted read-only. A web dashboard shows
progress, history and network use, and is where Google accounts are connected.
Works on Umbrel and on any Docker host: Linux, Windows, macOS, NAS (see README.md).
"""

import base64
import calendar
import collections
import concurrent.futures
import contextlib
import fnmatch
import hashlib
import heapq
import hmac
import http.cookies
import json
import logging
import os
import re
import shutil
import socket
import sqlite3
import sys
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cache
import media
import notify
import provenance
import snapchat

HERE = Path(__file__).resolve().parent
# VERSION sits next to sync.py in the image, one level up (repo root) when run from backend/.
_VERSION_FILE = next((p for p in (HERE / "VERSION", HERE.parent / "VERSION") if p.exists()), None)
VERSION = _VERSION_FILE.read_text().strip() if _VERSION_FILE else "dev"


def _backend_version():
    """The backend's own version (pyproject.toml): raised only when the server side changes. VERSION
    is the release (image tag); the dashboard has its own in web/package.json."""
    import tomllib

    with contextlib.suppress(OSError, ValueError, KeyError):
        return tomllib.loads((HERE / "pyproject.toml").read_text())["project"]["version"]
    return VERSION


BACKEND_VERSION = _backend_version()
DATA = Path(os.environ.get("DATA_DIR", "/data"))
MEDIA = Path(os.environ.get("MEDIA_DIR", "/media"))
PORT = int(os.environ.get("PORT", "8527"))
HOST = os.environ.get("HOST", "0.0.0.0")
INTERVAL = int(os.environ.get("SCAN_INTERVAL_MINUTES", "5")) * 60
SETTLE = int(os.environ.get("SETTLE_SECONDS", "120"))  # skip files still being copied
MAX_ATTEMPTS = 5
MAX_ACCOUNTS = 5  # Google accounts one app can hold
NET_HISTORY = 90  # seconds of speed history kept for the graph
STARTED = time.time()

LEGACY_AUTH_FILE = DATA / "auth_data"  # single account (older installs)
ACCOUNTS_DIR = DATA / "accounts"
PAUSE_FILE = DATA / "paused"
DB_FILE = DATA / "state.db"
STATS_FILE = DATA / "stats.db"
THUMBS = DATA / "thumbs"
# The data folder is a volume under Docker, but a plain path when run from source: make it so the
# databases below can be opened. A read-only or missing parent is left to fail where it is used.
with contextlib.suppress(OSError):
    DATA.mkdir(parents=True, exist_ok=True)
# Phones upload here (Umbrel: Photos/Uploads). The rest of the media stays read-only.
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "/uploads"))
UPLOAD_OWNER = (int(os.environ.get("PUID", "1000")), int(os.environ.get("PGID", "1000")))
MAX_UPLOAD = 50 * 1024**3
upload_lock = threading.Lock()
# The dashboard built by Vite (web/ → static/ in the image; ../web/dist when run from backend/ in the repo).
STATIC_DIR = next(
    (d for d in (HERE / "static", HERE.parent / "web" / "dist") if (d / "index.html").exists()), HERE / "static"
)

PHOTO_EXTS = {
    "jpg", "jpeg", "png", "heic", "heif", "gif", "webp", "bmp", "tif", "tiff", "avif",
    "dng", "cr2", "cr3", "nef", "arw", "raf", "orf", "rw2", "srw", "pef",
}  # fmt: skip
VIDEO_EXTS = {"mp4", "mov", "m4v", "3gp", "3g2", "avi", "mkv", "webm", "mts", "m2ts", "wmv", "mpg", "mpeg"}
EXTS = PHOTO_EXTS | VIDEO_EXTS
THUMB_EXTS = {"jpg", "jpeg", "png", "heic", "heif", "gif", "webp", "bmp", "tif", "tiff", "avif"}
DONE = ("uploaded", "exists")
# Top-level folders backed up until folders are chosen in Settings (FOLDERS="Photos,Videos" by
# default; when none of them exist, every folder is backed up, mtime).
DEFAULT_FOLDERS = tuple(f.strip() for f in os.environ.get("FOLDERS", "Photos,Videos").split(",") if f.strip())
# Folder and file names never backed up (comma-separated patterns, * and ? allowed): NAS and
# system clutter, and Immich's transcoded videos. Hidden names (starting with ".") are skipped too.
DEFAULT_EXCLUDE = "@eaDir,#recycle,#snapshot,$RECYCLE.BIN,System Volume Information,lost+found,encoded-video"
EXCLUDE = tuple(p.strip() for p in os.environ.get("EXCLUDE", DEFAULT_EXCLUDE).split(",") if p.strip())
# Where the app is deployed. Nothing assumes a platform unless this says so: PLATFORM=umbrel turns
# on Umbrel's wording and hints (the Umbrel app sets it). Empty: any Docker host. Older Umbrel
# installs only set HOST_NAME=Umbrel, which counts as PLATFORM=umbrel.
PLATFORM = os.environ.get("PLATFORM", "").strip().lower()
if not PLATFORM and os.environ.get("HOST_NAME", "").strip().lower() == "umbrel":
    PLATFORM = "umbrel"
PLATFORM = PLATFORM or "docker"
# What the dashboard calls the machine the files are on ("My NAS"); Umbrel's name on Umbrel.
HOST_NAME = os.environ.get("HOST_NAME", "").strip() or ("Umbrel" if PLATFORM == "umbrel" else "this server")
# Optional password for the dashboard and API (any user name). Umbrel has its own login.
PASSWORD = os.environ.get("PASSWORD", "")

log_lines = collections.deque(maxlen=300)  # (unix time, kind, text, file name or None)
state = {
    "phase": "starting",
    "done": 0,
    "total": 0,
    "last_scan": 0,
    "error": "",
    "current": None,  # {"name", "bytes_done", "bytes_total", "step", "kind", "account"}
    "library": {"files": 0, "bytes": 0, "synced_files": 0, "synced_bytes": 0, "types": {}},
    "folders": {},  # top folder -> {"files", "bytes", "synced_files", "synced_bytes"}
    "pending": [],  # names waiting to upload (first 500)
    "index": [],  # every media file from the last scan: (rel path, is video, size, added, backed up)
    "session": {"started": STARTED, "uploaded_files": 0, "uploaded_bytes": 0},
    "login_failed": set(),  # accounts Google rejected; cleared on success or a new login
    "next_window": 0,
}
net = {
    "up": collections.deque(maxlen=NET_HISTORY),
    "down": collections.deque(maxlen=NET_HISTORY),
    "sent": 0,
    "received": 0,
}
wake = threading.Event()
background_work = threading.Event()  # new uploads: make their thumbnails soon
changed = threading.Condition()  # wakes live dashboards (/api/stream) when something happens
stream_slots = threading.BoundedSemaphore(16)
db_lock = threading.Lock()


def log(msg, kind="info", name=None):
    """Print, keep in memory for the live view, and save to the activity history."""
    now = time.time()
    out = sys.stderr if kind in ("error", "failed") else sys.stdout
    print(time.strftime("%Y-%m-%d %H:%M:%S ") + msg, file=out, flush=True)
    log_lines.append((now, kind, msg, name))
    save_event(now, kind, msg, name)
    notify_change()


changes = [0]  # bumped on every notify_change, so cached status answers refresh at once


def notify_change():
    changes[0] += 1
    with changed:
        changed.notify_all()


def rel(path):
    return str(path).removeprefix(str(MEDIA) + "/")


def ext(name):
    return str(name).rsplit(".", 1)[-1].lower()


def top_folder(path):
    parts = rel(path).split("/")
    return parts[0] if len(parts) > 1 else ""


# ---------------------------------------------------------------- upload state (SQLite)


def db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS files (
            path TEXT PRIMARY KEY, size INTEGER, mtime REAL, status TEXT,
            media_key TEXT, error TEXT, attempts INTEGER DEFAULT 0, updated REAL)"""
    )
    conn.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(files)")}
    if "account" not in cols:  # older installs: add the account column
        conn.execute("ALTER TABLE files ADD COLUMN account TEXT")
    if "sha1" not in cols:  # older installs: add the sha1 column (duplicate finder)
        conn.execute("ALTER TABLE files ADD COLUMN sha1 TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS files_sha1 ON files(sha1)")
    conn.execute("CREATE INDEX IF NOT EXISTS files_updated ON files(updated)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS albums (account TEXT, name TEXT, album_key TEXT, PRIMARY KEY (account, name))"
    )
    # Copies of a file in more Google accounts than the folder's first one (one row per account).
    conn.execute(
        """CREATE TABLE IF NOT EXISTS copies (
            path TEXT NOT NULL, account TEXT NOT NULL, size INTEGER, mtime REAL, status TEXT,
            media_key TEXT, error TEXT, attempts INTEGER DEFAULT 0, updated REAL,
            PRIMARY KEY (path, account))"""
    )
    # Items moved out of Google storage (see "free up Google storage").
    conn.execute(
        """CREATE TABLE IF NOT EXISTS reclaim (
            account TEXT NOT NULL, media_key TEXT NOT NULL, dedup_key TEXT, name TEXT, kind TEXT,
            size INTEGER, quota INTEGER, taken REAL, favorite INTEGER, archived INTEGER, caption TEXT,
            state TEXT, error TEXT, local TEXT, new_key TEXT, checks INTEGER DEFAULT 0, updated REAL,
            PRIMARY KEY (account, media_key))"""
    )
    # Items copied from one Google account to another (see "copy between Google accounts").
    conn.execute(
        """CREATE TABLE IF NOT EXISTS copyjobs (
            src TEXT NOT NULL, dst TEXT NOT NULL, media_key TEXT NOT NULL, dedup_key TEXT, name TEXT,
            kind TEXT, size INTEGER, taken REAL, favorite INTEGER, archived INTEGER, caption TEXT,
            state TEXT, error TEXT, new_key TEXT, checks INTEGER DEFAULT 0, updated REAL,
            PRIMARY KEY (src, dst, media_key))"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS copyjobs_state ON copyjobs(state, taken)")
    # Keep in sync: everything `src` has is copied to `dst` every so often (both ways: two rows).
    conn.execute(
        """CREATE TABLE IF NOT EXISTS mirrors (
            src TEXT NOT NULL, dst TEXT NOT NULL, added REAL, last_run REAL, last_queued INTEGER DEFAULT 0,
            error TEXT DEFAULT '', PRIMARY KEY (src, dst))"""
    )
    have = {r[1] for r in conn.execute("PRAGMA table_info(copyjobs)")}
    for col in ("started", "finished"):  # when each copy began and ended (copy speed on the page)
        if col not in have:
            conn.execute(f"ALTER TABLE copyjobs ADD COLUMN {col} REAL")
    have = {r[1] for r in conn.execute("PRAGMA table_info(reclaim)")}
    for col, decl in (
        ("keep", "INTEGER DEFAULT 0"),  # keep the downloaded copy
        ("local_hash", "TEXT"),  # the downloaded file's dedup key, when Google served its edited copy
        ("started", "REAL"),  # when the download began (Freed page: how long it took)
        ("finished", "REAL"),  # when Google confirmed the free upload
        ("downloaded_at", "REAL"),  # when the download was checked (Time taken: where the time goes)
        ("uploaded_at", "REAL"),  # when the free upload finished
    ):
        if col not in have:
            conn.execute(f"ALTER TABLE reclaim ADD COLUMN {col} {decl}")
    # Copies kept on this server after freeing storage (Downloads page).
    conn.execute(
        """CREATE TABLE IF NOT EXISTS downloads (
            id INTEGER PRIMARY KEY, account TEXT NOT NULL, name TEXT, kind TEXT, size INTEGER, taken REAL,
            path TEXT NOT NULL, media_key TEXT, added REAL)"""
    )
    # Snapchat memories (see "Snapchat memories"): one row per memory file, and fingerprints of
    # Google's thumbnails and of files here, so each is only made once.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS snap_items (
            path TEXT PRIMARY KEY, manifest TEXT, mid TEXT, kind TEXT, taken REAL, lat REAL, lon REAL,
            sha1 TEXT, orig_sha1 TEXT, width INTEGER, height INTEGER, poster TEXT, dhash TEXT,
            size INTEGER, mtime REAL, account TEXT, match TEXT, google_key TEXT, google_dedup TEXT,
            google_taken REAL, distance INTEGER, local TEXT, action TEXT, decided INTEGER DEFAULT 0,
            state TEXT, error TEXT, new_key TEXT, favorite INTEGER, archived INTEGER, caption TEXT,
            kept INTEGER DEFAULT 0, in_album INTEGER DEFAULT 0, original INTEGER DEFAULT 0, updated REAL)"""
    )
    # Every copy of a memory Google has (one is kept, or none; the others are deleted there).
    conn.execute(
        """CREATE TABLE IF NOT EXISTS snap_copies (
            path TEXT NOT NULL, media_key TEXT NOT NULL, dedup TEXT, taken REAL, how TEXT, distance INTEGER,
            original INTEGER DEFAULT 0, favorite INTEGER DEFAULT 0, local TEXT, state TEXT, error TEXT,
            PRIMARY KEY (path, media_key))"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS snap_prints (
            account TEXT NOT NULL, media_key TEXT NOT NULL, dhash TEXT, PRIMARY KEY (account, media_key))"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS snap_local (
            path TEXT PRIMARY KEY, size INTEGER, mtime REAL, sha1 TEXT, dhash TEXT)"""
    )
    conn.execute("PRAGMA journal_mode=WAL")
    conn.commit()
    return conn


CONN = db()


def q(sql, args=()):
    with db_lock:
        cur = CONN.execute(sql, args)
        rows = cur.fetchall()  # before commit, so RETURNING rows are not lost
        if CONN.in_transaction:
            CONN.commit()
            cache.bump()  # cached answers that read the data are now stale
        return rows


def meta_get(k):
    rows = q("SELECT v FROM meta WHERE k=?", (k,))
    return rows[0][0] if rows else None


def meta_set(k, v):
    q("INSERT OR REPLACE INTO meta VALUES (?,?)", (k, v))


# ---------------------------------------------------------------- settings

DEFAULT_SETTINGS = {
    "folders": None,  # {top folder: account email or ""}; None = Photos and Videos to the first account
    "schedule": {"enabled": False, "start": 1, "end": 7},  # local hours; may wrap past midnight
    "tz_offset_min": 0,  # minutes east of UTC now, worked out from the time zone below
    "timezone": "",  # IANA name chosen in Settings ("Asia/Kolkata"); "" = the system default
    "system_timezone": "",  # the dashboard device's zone, the system default when TZ isn't set
    "speed_limit_mbps": 0,  # 0 = no limit
    "upload_workers": 3,  # files uploaded at once per account
    "albums": "off",  # off | top (first subfolder) | parent (folder the file is in)
    "notify": {
        "ntfy_url": "",
        "ntfy_token": "",
        "telegram_token": "",
        "telegram_chat": "",
        "on_login": True,
        "on_gave_up": True,
        "on_round": True,
        "round_min": 20,
        "weekly": True,
    },
    "hash_existing": True,  # duplicate finder: fingerprint already uploaded files when idle
    "priority": [],  # folders whose files upload first ("Photos/iPhone"), in this order
    # Photos and videos with no date of their own (Snapchat saves, screenshots, chat pictures).
    # "asked" turns true once the user has seen the choice (first setup, or once after updating).
    "undated": {"album": True, "name": "Date unknown", "asked": False},
    # Where files came from (Sources page): what to do in Google with items saved from apps.
    # Nothing happens in Google until "asked" is true (the user has seen the choice).
    "sources": {
        "asked": False,
        "estimate_dates": True,  # give Snapchat saves their estimated date when uploading
        "snapchat": {"album": True, "album_name": "Snapchat", "caption": True, "archive": False},
        "instagram": {"album": True, "album_name": "Instagram", "caption": True, "archive": False},
        "tiktok": {"album": True, "album_name": "TikTok", "caption": True, "archive": False},
        "whatsapp": {"album": True, "album_name": "WhatsApp", "caption": True, "archive": False},
        "telegram": {"album": True, "album_name": "Telegram", "caption": True, "archive": False},
        "screenshot": {"album": False, "album_name": "Screenshots", "caption": False, "archive": False},
        "app_export": {"album": True, "album_name": "Saved from apps", "caption": False, "archive": False},
    },
    # Free storage: ask before each batch, keep the downloaded copies, how many items move at once,
    # and an optional album in Google for everything it freed (off: nothing is added to the library).
    "reclaim": {"confirm": True, "keep": False, "workers": 4, "album": False, "album_name": "Freed by GP Sync"},
}
# At most 4 items or files at once: more makes Google throttle and fail (tested: 16 at once broke,
# 4 at once ran almost without errors).
RECLAIM_MAX_WORKERS = 4
UPLOAD_MAX_WORKERS = 4
UNDATED_ALBUM = "Date unknown"
SECRET_KEYS = ("ntfy_token", "telegram_token")


def _merge(base, extra):
    out = dict(base)
    for k, v in (extra or {}).items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _merge(out[k], v)
        elif k in out:
            out[k] = v
    return out


def _zone(name):
    """The ZoneInfo for an IANA name, or None when it's empty or unknown."""
    from zoneinfo import ZoneInfo

    if not name or not isinstance(name, str):
        return None
    try:
        return ZoneInfo(name)
    except (ValueError, OSError, KeyError):  # unknown name, or no time zone data
        return None


def system_timezone(st=None):
    """The system default: the TZ the app was started with, else the zone of the device the
    dashboard was last opened on (the app's container often runs in UTC without TZ). "" when
    neither is known: then the offset the dashboard saved is used as it is."""
    env = os.environ.get("TZ", "").lstrip(":")
    if _zone(env) and env not in ("UTC", "Etc/UTC"):
        return env
    st = st if st is not None else get_settings()
    return st.get("system_timezone") if _zone(st.get("system_timezone")) else ""


def timezone_name(st=None):
    """The time zone in use: the one chosen in Settings, else the system default."""
    st = st if st is not None else get_settings()
    return st["timezone"] if _zone(st.get("timezone")) else system_timezone(st)


def zone_offset_min(name, when=None):
    """Minutes east of UTC in a zone at `when` (default now), daylight saving included."""
    import datetime

    z = _zone(name)
    if z is None:
        return None
    moment = datetime.datetime.fromtimestamp(when if when is not None else time.time(), datetime.UTC)
    return int(z.utcoffset(moment).total_seconds() // 60)


def get_settings():
    try:
        saved = json.loads(meta_get("settings") or "{}")
    except ValueError:
        saved = {}
    st = _merge(json.loads(json.dumps(DEFAULT_SETTINGS)), saved)  # deep copy: never change the defaults
    # Days, upload hours and the weekly summary follow the chosen zone (or the system default).
    offset = zone_offset_min(timezone_name(st))
    if offset is not None:
        st["tz_offset_min"] = offset
    # Saved before the limit was lowered (up to 16): keep within it.
    st["reclaim"]["workers"] = max(1, min(RECLAIM_MAX_WORKERS, int(st["reclaim"].get("workers") or 1)))
    st["upload_workers"] = max(1, min(UPLOAD_MAX_WORKERS, int(st.get("upload_workers") or 1)))
    return st


def update_settings(changes):
    """Merge `changes` into the saved settings, after checking the values. Returns the new settings."""
    if not isinstance(changes, dict):
        raise ValueError("Settings must be a JSON object")
    cur = get_settings()
    new = _merge(cur, changes)
    for key in ("notify", "schedule", "undated", "sources", "reclaim"):
        if not isinstance(new[key], dict):
            raise ValueError(f"{key} must be a JSON object")
    src = new["sources"]
    src["asked"], src["estimate_dates"] = bool(src["asked"]), bool(src["estimate_dates"])
    for key in provenance.APP_SOURCES:
        if not isinstance(src[key], dict):
            raise ValueError(f"sources.{key} must be a JSON object")
        e = src[key]
        e["album"], e["caption"], e["archive"] = bool(e["album"]), bool(e["caption"]), bool(e["archive"])
        default = DEFAULT_SETTINGS["sources"][key]["album_name"]
        e["album_name"] = " ".join(str(e["album_name"] or "").split())[:80] or default
    n, s = new["notify"], new["schedule"]
    notify_changes = changes.get("notify") if isinstance(changes.get("notify"), dict) else {}
    for key in SECRET_KEYS:  # empty secret in a change means "keep the saved one"
        if not notify_changes.get(key):
            n[key] = cur["notify"][key]
    s["start"], s["end"] = int(s["start"]) % 24, int(s["end"]) % 24
    s["enabled"] = bool(s["enabled"])
    new["tz_offset_min"] = max(-840, min(840, int(new["tz_offset_min"])))
    for key in ("timezone", "system_timezone"):
        new[key] = str(new.get(key) or "").strip()
        if new[key] and not _zone(new[key]):
            if key == "timezone":
                raise ValueError(f"Unknown time zone: {new[key]}")
            new[key] = cur[key]  # a device that reports a zone this server doesn't know: keep the last one
    new["speed_limit_mbps"] = max(0.0, float(new["speed_limit_mbps"] or 0))
    new["upload_workers"] = max(1, min(UPLOAD_MAX_WORKERS, int(new["upload_workers"] or 1)))
    if new["albums"] not in ("off", "top", "parent"):
        new["albums"] = "off"
    n["round_min"] = max(1, int(n["round_min"]))
    u = new["undated"]
    u["album"], u["asked"] = bool(u["album"]), bool(u["asked"])
    u["name"] = " ".join(str(u["name"] or "").split())[:80] or UNDATED_ALBUM
    rc = new["reclaim"]
    rc["confirm"], rc["keep"], rc["album"] = bool(rc["confirm"]), bool(rc["keep"]), bool(rc["album"])
    rc["workers"] = max(1, min(RECLAIM_MAX_WORKERS, int(rc["workers"] or 1)))
    rc["album_name"] = " ".join(str(rc["album_name"] or "").split())[:80] or "Freed by GP Sync"
    pr = new.get("priority") or []
    pr = [clean_folder(p) for p in (pr if isinstance(pr, list) else [])]
    new["priority"] = list(dict.fromkeys(p for p in pr if p))[:10]
    if new["folders"] is not None:
        # One account per folder, or a list (the first is the main one): up to MAX_ACCOUNTS.
        new["folders"] = {
            str(k): list(dict.fromkeys(a for a in _as_list(v) if a))[:MAX_ACCOUNTS]
            for k, v in dict(new["folders"]).items()
        }
    if n["ntfy_url"] and not n["ntfy_url"].startswith(("http://", "https://")):
        raise ValueError("The ntfy address must start with http:// or https://")
    meta_set("settings", json.dumps(new))
    return new


def public_settings():
    """Settings for the dashboard: secrets are never sent back, only whether they are set."""
    st = get_settings()
    for key in SECRET_KEYS:
        st["notify"][key + "_set"] = bool(st["notify"][key])
        st["notify"][key] = ""
    return st


# ---------------------------------------------------------------- Google accounts


def email_of(auth):
    params = urllib.parse.parse_qs(auth)
    return (params.get("Email") or params.get("email") or ["unknown"])[0]


def _account_file(email):
    safe = "".join(c for c in email if c.isalnum() or c in "@._-+")
    return ACCOUNTS_DIR / f"{safe}.auth"


def _account_paths():
    """{email: [auth files]} read from the files themselves: a file's name can differ from the
    email inside it (an older version, or a login whose email could not be read)."""
    found = {}
    for f in ACCOUNTS_DIR.glob("*.auth"):
        auth = f.read_text().strip()
        if auth:
            found.setdefault(email_of(auth), []).append(f)
    return found


def migrate_accounts():
    """Older installs: the single auth_data file becomes the first account."""
    ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
    if LEGACY_AUTH_FILE.exists() and not any(ACCOUNTS_DIR.glob("*.auth")):
        auth = LEGACY_AUTH_FILE.read_text().strip()
        if auth:
            target = _account_file(email_of(auth))
            target.write_text(auth)
            target.chmod(0o600)
    first = accounts()
    if first and not _account_order():
        meta_set("account_order", json.dumps([e for e, _ in first]))
    if first:
        q("UPDATE files SET account=? WHERE account IS NULL", (first[0][0],))


def _account_order():
    try:
        return json.loads(meta_get("account_order") or "[]")
    except ValueError:
        return []


def accounts():
    """[(email, auth)] in the order they were added. The first one is the main account."""
    found = {}
    for f in ACCOUNTS_DIR.glob("*.auth"):
        auth = f.read_text().strip()
        if auth:
            found[email_of(auth)] = (auth, f.stat().st_mtime)
    order = _account_order()
    rank = {e: i for i, e in enumerate(order)}
    emails = sorted(found, key=lambda e: (rank.get(e, len(order)), found[e][1], e))
    return [(e, found[e][0]) for e in emails]


def parse_auth(auth):
    auth = (auth or "").strip()
    if "androidId=" not in auth:
        return None
    auth = auth[auth.index("androidId=") :].split()[0]
    return auth if "Email=" in auth else None


def save_account(auth):
    """Add or replace an account's login. Returns its email, or None if the text is not a login."""
    auth = parse_auth(auth)
    if not auth:
        return None
    email = email_of(auth)
    target = _account_file(email)
    ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
    # The same login may be saved under another file name (older versions): replace it too, or the
    # stale login could still be the one that is used.
    others = [f for f in _account_paths().get(email, []) if f != target]
    existed = target.exists() or bool(others)
    if not existed and len(accounts()) >= MAX_ACCOUNTS:
        raise ValueError(f"Up to {MAX_ACCOUNTS} Google accounts can be connected. Remove one first.")
    target.write_text(auth)
    target.chmod(0o600)
    for f in others:
        f.unlink(missing_ok=True)
    order = _account_order()
    if email not in order:  # replacing a login keeps the account's place
        meta_set("account_order", json.dumps([*order, email]))
    state["login_failed"].discard(email)
    log(("Updated login for " if existed else "Connected Google account ") + email, "account")
    wake.set()
    return email


def remove_account(email):
    files = _account_paths().get(email) or [f for f in [_account_file(email)] if f.exists()]
    if files:
        for f in files:
            f.unlink(missing_ok=True)
        meta_set("account_order", json.dumps([e for e in _account_order() if e != email]))
        state["login_failed"].discard(email)  # no warning for an account that is gone
        q("DELETE FROM mirrors WHERE src=? OR dst=?", (email, email))  # nothing to keep in sync with
        with _clients_lock:
            _clients.pop(email, None)
        log(f"Removed Google account {email}", "account")
        return True
    return False


def _as_list(value):
    """A folder's accounts as saved: one email (older settings) or a list of emails."""
    if not value:
        return []
    return [str(v) for v in value] if isinstance(value, (list, tuple)) else [str(value)]


def folder_targets(st=None, tops=None):
    """Every account each top-level folder is backed up to; the first is its main account.

    An empty list means the folder is not backed up."""
    st = st or get_settings()
    accs = [a for a, _ in accounts()]
    tops = tops if tops is not None else list_top_folders()
    if st["folders"] is None:
        defaults = [t for t in tops if t in DEFAULT_FOLDERS] or tops
        return {t: (accs[:1] if t in defaults else []) for t in tops}
    out = {}
    for t in tops:
        chosen = [a for a in _as_list(st["folders"].get(t)) if a in accs]
        out[t] = list(dict.fromkeys(chosen))[:MAX_ACCOUNTS]
    return out


def folder_map(st=None, tops=None):
    """The main account of each top-level folder. "" means it is not backed up."""
    return {t: (accs[0] if accs else "") for t, accs in folder_targets(st, tops).items()}


def skipped(name):
    """Hidden, or matches an EXCLUDE pattern."""
    return name.startswith(".") or any(fnmatch.fnmatch(name, p) for p in EXCLUDE)


def list_top_folders():
    try:
        return sorted(e.name for e in os.scandir(MEDIA) if e.is_dir() and not skipped(e.name))
    except OSError:
        return []


# ---------------------------------------------------------------- statistics (Turso)
# Daily statistics live in a Turso database (open-source, SQLite-compatible; turso.tech).
# Upload state stays in plain SQLite: Turso is still beta, and a statistics problem must
# never stop backups. If Turso can't be used, statistics fall back to SQLite.

STAT_COLUMNS = (
    "photos",
    "videos",
    "photo_bytes",
    "video_bytes",
    "existing",
    "failed",
    "net_sent",
    "net_received",
    "photo_ms",  # time spent uploading (added later; older rows have none)
    "video_ms",
)
HOURLY_SCHEMA = """CREATE TABLE IF NOT EXISTS hourly (
    hour INTEGER PRIMARY KEY,
    photos INTEGER NOT NULL DEFAULT 0, videos INTEGER NOT NULL DEFAULT 0,
    photo_bytes INTEGER NOT NULL DEFAULT 0, video_bytes INTEGER NOT NULL DEFAULT 0,
    existing INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0,
    net_sent INTEGER NOT NULL DEFAULT 0, net_received INTEGER NOT NULL DEFAULT 0,
    peak_up REAL NOT NULL DEFAULT 0, peak_down REAL NOT NULL DEFAULT 0)"""  # one row per hour (unix time // 3600)
STATS_SCHEMA = (
    HOURLY_SCHEMA,
    "CREATE TABLE IF NOT EXISTS events (time REAL NOT NULL, kind TEXT, text TEXT, name TEXT)",
    # One row per finished file: how long it took, for averages and insights.
    """CREATE TABLE IF NOT EXISTS uploads (time REAL NOT NULL, name TEXT, kind TEXT, bytes INTEGER,
       ms INTEGER, result TEXT, account TEXT)""",
    # Everything the app prints, so problems can be looked at later.
    "CREATE TABLE IF NOT EXISTS logs (time REAL NOT NULL, level TEXT, text TEXT)",
    # Per-minute averages of speed, CPU, memory and Google latency.
    """CREATE TABLE IF NOT EXISTS samples (minute INTEGER PRIMARY KEY, up REAL, down REAL, cpu REAL,
       mem INTEGER, latency REAL)""",
    # Dates: does the file keep its own capture date (inside: 1 yes, 0 no, NULL can't tell), the date
    # the user picked, the date sent to Google, and the "fix the date in Google" job for it.
    """CREATE TABLE IF NOT EXISTS media_dates (path TEXT PRIMARY KEY, size INTEGER, mtime REAL, sha1 TEXT,
       inside INTEGER, manual REAL, sent REAL, fix TEXT, error TEXT, favorite INTEGER, archived INTEGER,
       caption TEXT, updated REAL, previous_google REAL, previous_google_source TEXT)""",
    # Files without a date already added to the album for them (per account and album name).
    """CREATE TABLE IF NOT EXISTS undated_album (path TEXT NOT NULL, account TEXT NOT NULL, album TEXT,
       PRIMARY KEY (path, account))""",
    # Where each file came from (provenance.detect), the user's correction (decision: a source,
    # or NULL for the detected one), the estimated date of Snapchat saves, and what was done in
    # Google: album name it was added to, archived, caption written by GP Sync ("-" = it had its own).
    """CREATE TABLE IF NOT EXISTS provenance (path TEXT PRIMARY KEY, size INTEGER, mtime REAL, source TEXT,
       app TEXT, device TEXT, app_version TEXT, os_version TEXT, taken REAL, confidence TEXT, reasons TEXT,
       est_date REAL, est_lo REAL, est_hi REAL, est_reason TEXT, decision TEXT, album TEXT,
       archived INTEGER, captioned TEXT, error TEXT, updated REAL)""",
)
EVENT_DAYS = 90  # activity history kept this long
LOG_DAYS = 30
SAMPLE_DAYS = 14
stats_lock = threading.Lock()


def _dist_version(name):
    try:
        from importlib.metadata import version

        return version(name)
    except Exception:
        return ""


def open_stats():
    """Open the statistics database: Turso if possible, otherwise SQLite. Returns (connection, engine name)."""
    try:
        import turso  # optional at runtime, so a broken install can't stop the app

        conn = turso.connect(str(STATS_FILE))
        _create_stats_tables(conn)
        return conn, f"Turso {_dist_version('pyturso')}".strip()
    except Exception as e:
        print(f"Turso unavailable, using SQLite for statistics: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        conn = sqlite3.connect(STATS_FILE, check_same_thread=False)
        _create_stats_tables(conn)
        return conn, f"SQLite {sqlite3.sqlite_version} (Turso unavailable)"


def _create_stats_tables(conn):
    for sql in STATS_SCHEMA:
        conn.execute(sql)
    have = {row[1] for row in conn.execute("PRAGMA table_info(hourly)").fetchall()}
    for col in STAT_COLUMNS:
        if col not in have:  # columns added by newer versions
            conn.execute(f"ALTER TABLE hourly ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")
    date_cols = {row[1] for row in conn.execute("PRAGMA table_info(media_dates)").fetchall()}
    for col, kind in (("previous_google", "REAL"), ("previous_google_source", "TEXT")):
        if col not in date_cols:
            conn.execute(f"ALTER TABLE media_dates ADD COLUMN {col} {kind}")
    conn.commit()


STATS, STATS_ENGINE = open_stats()


def sq(sql, args=()):
    """Run one statement on the statistics database."""
    with stats_lock:
        cur = STATS.execute(sql, args)
        rows = cur.fetchall()
        STATS.commit()
        if sql.lstrip()[:6].upper() != "SELECT":
            cache.bump()
        return rows


def init_events():
    """Indexes, and drop history older than it is kept."""
    now = time.time()
    for table, days in (("events", EVENT_DAYS), ("logs", LOG_DAYS), ("uploads", None)):
        sq(f"CREATE INDEX IF NOT EXISTS {table}_time ON {table}(time)")
        if days:
            sq(f"DELETE FROM {table} WHERE time < ?", (now - days * 86400,))
    sq("DELETE FROM samples WHERE minute < ?", (int(now // 60) - SAMPLE_DAYS * 1440,))
    # Date fixes and freed storage used to be saved as plain uploads.
    sq("UPDATE events SET kind='fixed' WHERE kind='uploaded' AND text LIKE 'Fixed the date of %'")
    sq("UPDATE events SET kind='freed' WHERE kind='uploaded' AND text LIKE 'Freed %'")
    sq("UPDATE events SET kind='deleted' WHERE kind='info' AND text LIKE 'Deleted % in Google Photos%'")
    sq("UPDATE events SET kind='downloaded' WHERE kind='info' AND text LIKE 'Downloaded % from Google%'")
    with contextlib.suppress(OSError):
        clean_chunks()


def save_event(when, kind, text, name=None):
    """Store one activity entry. Never raises."""
    try:
        sq("INSERT INTO events (time, kind, text, name) VALUES (?,?,?,?)", (when, kind, text, name))
    except Exception as e:
        print(f"Could not save activity: {type(e).__name__}: {e}", file=sys.stderr, flush=True)


EVENT_KINDS = {
    "uploaded": ("uploaded",),
    # What GP Sync changed in Google Photos besides new uploads.
    "google": ("fixed", "freed", "copied", "deleted", "downloaded"),
    "exists": ("exists",),
    "failed": ("failed", "error"),
    "system": ("scan", "info", "pause", "resume", "account", "album", "received"),
}


def like(text):
    """`text` for a LIKE ... ESCAPE '\\' pattern, so % and _ match only themselves."""
    return str(text).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _before(value):
    """A paging time from the dashboard, or None when it is missing or not a number."""
    try:
        return float(value) if value else None
    except (TypeError, ValueError):
        return None


def activity(kind="all", query="", before=None, limit=100):
    """Saved activity, newest first. `kind` is all, uploaded, google, exists, failed or system."""
    where, args = [], []
    if kind in EVENT_KINDS:
        where.append(f"kind IN ({', '.join('?' * len(EVENT_KINDS[kind]))})")
        args += EVENT_KINDS[kind]
    if query:
        where.append("text LIKE ? ESCAPE '\\'")
        args.append(f"%{like(query)}%")
    if (before := _before(before)) is not None:
        where.append("time < ?")
        args.append(before)
    sql_where = ("WHERE " + " AND ".join(where)) if where else ""
    rows = sq(f"SELECT time, kind, text, name FROM events {sql_where} ORDER BY time DESC LIMIT ?", (*args, int(limit)))
    return {"items": [{"time": t, "kind": k, "text": x, "name": n} for t, k, x, n in rows]}


def save_upload(name, kind, size, ms, result, account):
    """Store how long one file took. Never raises."""
    try:
        sq(
            "INSERT INTO uploads (time, name, kind, bytes, ms, result, account) VALUES (?,?,?,?,?,?,?)",
            (time.time(), name, kind, int(size), int(ms), result, account),
        )
    except Exception as e:
        print(f"Could not save upload time: {type(e).__name__}: {e}", file=sys.stderr, flush=True)


def _per(total, count):
    return total / count if count else 0


def insights(tz_offset_min=0):
    """Averages and patterns: upload time, file sizes, busiest hours and days, file types."""
    t = totals()
    lib = state["library"]
    offset = int(tz_offset_min) * 60
    kinds = {}
    for kind, n, size, ms, slow in sq(
        """SELECT kind, COUNT(*), COALESCE(SUM(bytes), 0), COALESCE(SUM(ms), 0), COALESCE(MAX(ms), 0)
           FROM uploads WHERE result='uploaded' GROUP BY kind"""
    ):
        kinds[kind] = {"files": n, "bytes": size, "ms": ms, "max_ms": slow}
    hours = [0] * 24
    for h, n in sq(
        "SELECT CAST((time + ?) / 3600 AS INTEGER) % 24 AS h, COUNT(*) FROM uploads WHERE result='uploaded' GROUP BY h",
        (offset,),
    ):
        hours[int(h)] = n
    weekdays = [0] * 7  # Monday first
    for d, n in sq(
        """SELECT (CAST((time + ?) / 86400 AS INTEGER) + 3) % 7 AS d, COUNT(*)
           FROM uploads WHERE result='uploaded' GROUP BY d""",
        (offset,),
    ):
        weekdays[int(d)] = n

    def rows(sql):
        return [{"time": tm, "name": n, "kind": k, "bytes": b, "ms": ms} for tm, n, k, b, ms in sq(sql)]

    total_ms = t["photo_ms"] + t["video_ms"]
    uploaded = t["photos"] + t["videos"]
    timed = sum(k["files"] for k in kinds.values())
    timed_bytes = sum(k["bytes"] for k in kinds.values())
    timed_ms = sum(k["ms"] for k in kinds.values())
    return {
        "upload_ms": total_ms,
        "uploaded": uploaded,
        "timed_files": timed,  # files with a recorded upload time
        "avg_ms": _per(timed_ms, timed),
        "avg_speed": _per(timed_bytes, timed_ms / 1000),  # bytes per second while uploading
        "photo": {
            "avg_ms": _per(kinds.get("photo", {}).get("ms", 0), kinds.get("photo", {}).get("files", 0)),
            "max_ms": kinds.get("photo", {}).get("max_ms", 0),
            "avg_bytes": _per(lib.get("photo_bytes", 0), lib.get("photos", 0)),
            "avg_uploaded_bytes": _per(t["photo_bytes"], t["photos"]),
            "files": lib.get("photos", 0),
            "bytes": lib.get("photo_bytes", 0),
        },
        "video": {
            "avg_ms": _per(kinds.get("video", {}).get("ms", 0), kinds.get("video", {}).get("files", 0)),
            "max_ms": kinds.get("video", {}).get("max_ms", 0),
            "avg_bytes": _per(lib.get("video_bytes", 0), lib.get("videos", 0)),
            "avg_uploaded_bytes": _per(t["video_bytes"], t["videos"]),
            "files": lib.get("videos", 0),
            "bytes": lib.get("video_bytes", 0),
        },
        "types": sorted(
            ({"ext": e, "files": n, "bytes": b} for e, (n, b) in lib.get("types", {}).items()),
            key=lambda x: -x["bytes"],
        ),
        "by_hour": hours,
        "by_weekday": weekdays,
        "largest": rows(
            "SELECT time, name, kind, bytes, ms FROM uploads WHERE result='uploaded' ORDER BY bytes DESC LIMIT 5"
        ),
        "slowest": rows(
            "SELECT time, name, kind, bytes, ms FROM uploads WHERE result='uploaded' ORDER BY ms DESC LIMIT 5"
        ),
        "tz_offset_min": int(tz_offset_min),
    }


# ---------------------------------------------------------------- app log (everything printed)


class LogTee:
    """Copies printed lines to the logs table, a few at a time from a background thread."""

    def __init__(self, stream, level):
        self.stream, self.level = stream, level
        self.buf = ""

    def write(self, text):
        self.stream.write(text)
        self.buf += text
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            line = LOG_STAMP.sub("", line, count=1).rstrip()  # the time is stored separately
            if line.strip():
                level = "warning" if line.startswith("WARNING ") else self.level
                pending_logs.append((time.time(), level, line[:2000]))
        return len(text)

    def flush(self):
        self.stream.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


pending_logs = collections.deque(maxlen=5000)
LOG_STAMP = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d ")


def flush_logs():
    rows = []
    while pending_logs:
        rows.append(pending_logs.popleft())
    if not rows:
        return
    try:
        with stats_lock:
            for row in rows:
                STATS.execute("INSERT INTO logs (time, level, text) VALUES (?,?,?)", row)
            STATS.commit()
    except Exception as e:
        sys.__stdout__.write(f"Could not save the log: {type(e).__name__}: {e}\n")  # not to the log: no loop


def log_writer():
    while True:
        time.sleep(2)
        flush_logs()


def app_logs(level="all", query="", before=None, limit=200):
    where, args = [], []
    if level == "info":
        where.append("level = 'info'")
    elif level == "error":  # problems: errors and warnings
        where.append("level IN ('error', 'warning')")
    if query:
        where.append("text LIKE ? ESCAPE '\\'")
        args.append(f"%{like(query)}%")
    if (before := _before(before)) is not None:
        where.append("time < ?")
        args.append(before)
    sql_where = ("WHERE " + " AND ".join(where)) if where else ""
    rows = sq(f"SELECT time, level, text FROM logs {sql_where} ORDER BY time DESC LIMIT ?", (*args, int(limit)))
    return {"items": [{"time": t, "level": lv, "text": x} for t, lv, x in rows]}


# ---------------------------------------------------------------- per-minute metrics


def save_minute(minute, up, down, cpu, mem, ms):
    try:
        sq(
            "INSERT OR REPLACE INTO samples (minute, up, down, cpu, mem, latency) VALUES (?,?,?,?,?,?)",
            (int(minute), up, down, cpu, int(mem), ms),
        )
    except Exception as e:
        print(f"Could not save metrics: {type(e).__name__}: {e}", file=sys.stderr, flush=True)


def minute_sampler():
    """Every minute: store averages from the live samplers. Once a day: drop old history."""
    last_prune = time.time()
    while True:
        time.sleep(60 - time.time() % 60 + 0.5)
        now = time.time()

        def avg(d, n=60):
            vals = list(d)[-n:]
            return sum(vals) / len(vals) if vals else 0

        recent = [ms for t, ms in latency if ms is not None and now - t < 90]
        save_minute(
            now // 60 - 1,
            avg(net["up"]),
            avg(net["down"]),
            avg(res["cpu"]),
            avg(res["mem"]),
            sum(recent) / len(recent) if recent else None,
        )
        if now - last_prune > 86400:
            init_events()
            last_prune = now


def metrics(hours=24):
    """Per-minute history grouped so about 240 points come back."""
    hours = max(1, min(int(hours), SAMPLE_DAYS * 24))
    step = max(1, hours * 60 // 240)  # minutes per point
    first = int(time.time() // 60) - hours * 60
    rows = sq(
        """SELECT minute / ? * ? AS m, AVG(up), MAX(up), AVG(down), MAX(down), AVG(cpu), AVG(mem), AVG(latency)
           FROM samples WHERE minute >= ? GROUP BY m ORDER BY m""",
        (step, step, first),
    )
    keys = ("up", "up_max", "down", "down_max", "cpu", "mem", "latency")
    return {
        "hours": hours,
        "step_min": step,
        "points": [{"time": m * 60, **dict(zip(keys, vals, strict=True))} for m, *vals in rows],
    }


def migrate_stats():
    """Move statistics kept in state.db by version 1.0.3 into the Turso database."""
    with db_lock:
        has_old = CONN.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='hourly'").fetchone()
        rows = CONN.execute("SELECT * FROM hourly").fetchall() if has_old else []
    if not has_old:
        return
    cols = ", ".join(("hour", *STAT_COLUMNS, "peak_up", "peak_down"))
    with stats_lock:
        for row in rows:
            STATS.execute(f"INSERT OR REPLACE INTO hourly ({cols}) VALUES ({', '.join('?' * len(row))})", row)
        STATS.commit()
    with db_lock:
        CONN.execute("DROP TABLE hourly")
        CONN.commit()


def add_stats(when=None, peak_up=0.0, peak_down=0.0, **counts):
    """Add counts to the hour containing `when` (default now) and raise its peak speeds.

    Never raises: statistics must not interrupt uploads."""
    try:
        _add_stats(when, peak_up, peak_down, **counts)
    except Exception as e:
        print(f"Could not save statistics: {type(e).__name__}: {e}", file=sys.stderr, flush=True)


def _add_stats(when, peak_up, peak_down, **counts):
    hour = int((when or time.time()) // 3600)
    cols = [c for c in counts if c in STAT_COLUMNS]
    sq(
        f"""INSERT INTO hourly (hour, {", ".join([*cols, "peak_up", "peak_down"])})
            VALUES (?{", ?" * (len(cols) + 2)})
            ON CONFLICT(hour) DO UPDATE SET {"".join(f"{c}={c}+excluded.{c}, " for c in cols)}
            peak_up=MAX(peak_up, excluded.peak_up), peak_down=MAX(peak_down, excluded.peak_down)""",
        (hour, *(int(counts[c]) for c in cols), peak_up, peak_down),
    )


def backfill_stats():
    """Once: build daily history from uploads made before statistics existed."""
    if meta_get("stats_backfilled"):
        return
    rows = q("SELECT path, size, status, updated FROM files WHERE status IN ('uploaded','exists') AND updated")
    for path, size, status, updated in rows:
        if status == "exists":
            add_stats(updated, existing=1)
        elif ext(path) in VIDEO_EXTS:
            add_stats(updated, videos=1, video_bytes=size)
        else:
            add_stats(updated, photos=1, photo_bytes=size)
    meta_set("stats_backfilled", "1")


def history(days=365, tz_offset_min=0):
    """Daily totals for the last `days` days, in the viewer's time zone (offset east of UTC, minutes)."""
    offset = int(tz_offset_min) * 60
    today = int((time.time() + offset) // 86400)
    first = today - int(days) + 1
    sums = ", ".join(f"SUM({c})" for c in STAT_COLUMNS)
    rows = sq(
        f"""SELECT (hour * 3600 + ?) / 86400 AS d, {sums}, MAX(peak_up), MAX(peak_down)
            FROM hourly WHERE hour * 3600 + ? >= ? * 86400 GROUP BY d ORDER BY d""",
        (offset, offset, first),
    )
    out = []
    for d, *vals in rows:
        day = time.strftime("%Y-%m-%d", time.gmtime(d * 86400))
        out.append({"day": day, **dict(zip((*STAT_COLUMNS, "peak_up", "peak_down"), vals, strict=True))})
    return {
        "days": int(days),
        "tz_offset_min": int(tz_offset_min),
        "first": time.strftime("%Y-%m-%d", time.gmtime(first * 86400)),
        "today": time.strftime("%Y-%m-%d", time.gmtime(today * 86400)),
        "data": out,
    }


PROCESSES = ("uploads", "freed", "copied", "fixed")


def process_activity(days=371, tz_offset_min=0):
    """Each process's own history, in the viewer's time zone: items and bytes per day, and when in
    the day and week they happen. Uploads come from the hourly statistics and the uploads list;
    freed storage, copies and date fixes from their own records."""
    offset = int(tz_offset_min) * 60
    since = time.time() - int(days) * 86400

    def blank():
        return {"days": {}, "hours": [0] * 24, "weekdays": [0] * 7, "count": 0, "bytes": 0}

    out = {p: blank() for p in PROCESSES}

    def add(proc, when, size=0, n=1, hour_too=True):
        if not when:
            return
        local = when + offset
        day = time.strftime("%Y-%m-%d", time.gmtime(local))
        d = out[proc]["days"].setdefault(day, {"day": day, "count": 0, "bytes": 0})
        d["count"] += n
        d["bytes"] += size or 0
        out[proc]["count"] += n
        out[proc]["bytes"] += size or 0
        if hour_too:
            out[proc]["hours"][int(local // 3600) % 24] += n
            out[proc]["weekdays"][(int(local // 86400) + 3) % 7] += n  # Monday first (1970-01-01 was a Thursday)

    # Uploads: daily totals from the hourly statistics, hours and weekdays from each finished file.
    for hour, n, b in sq(
        """SELECT hour, photos + videos, photo_bytes + video_bytes FROM hourly
           WHERE hour * 3600 >= ? AND photos + videos > 0""",
        (since,),
    ):
        add("uploads", hour * 3600, b, n, hour_too=False)
    for (when,) in sq("SELECT time FROM uploads WHERE time >= ? AND result = 'uploaded'", (since,)):
        local = when + offset
        out["uploads"]["hours"][int(local // 3600) % 24] += 1
        out["uploads"]["weekdays"][(int(local // 86400) + 3) % 7] += 1
    for when, size in q(
        """SELECT COALESCE(finished, updated), quota FROM reclaim
           WHERE state = 'done' AND COALESCE(finished, updated) >= ?""",
        (since,),
    ):
        add("freed", when, size)
    for when, size in q(
        """SELECT COALESCE(finished, updated), size FROM copyjobs WHERE state IN ('copied', 'done')
           AND COALESCE(finished, updated) >= ?""",
        (since,),
    ):
        add("copied", when, size)
    for (when,) in sq("SELECT time FROM events WHERE kind = 'fixed' AND time >= ?", (since,)):
        add("fixed", when)
    for proc in out.values():
        proc["days"] = sorted(proc["days"].values(), key=lambda d: d["day"])
    today = int((time.time() + offset) // 86400)
    return {
        "today": time.strftime("%Y-%m-%d", time.gmtime(today * 86400)),
        "tz_offset_min": int(tz_offset_min),
        "processes": out,
    }


def totals():
    sums = ", ".join(f"COALESCE(SUM({c}), 0)" for c in STAT_COLUMNS)
    row = sq(f"SELECT {sums}, COALESCE(MAX(peak_up), 0), COALESCE(MIN(hour), 0) FROM hourly")[0]
    return {**dict(zip((*STAT_COLUMNS, "peak_up"), row[:-1], strict=True)), "since": row[-1] * 3600}


# ---------------------------------------------------------------- network speed


def read_net_counters():
    """Total bytes received and sent on this container's network (all but loopback)."""
    rx = tx = 0
    try:
        with open("/proc/net/dev") as f:
            for line in f.readlines()[2:]:
                name, data = line.split(":", 1)
                if name.strip() == "lo":
                    continue
                fields = data.split()
                rx += int(fields[0])
                tx += int(fields[8])
    except OSError:
        pass
    return rx, tx


# Nobody is watching and nothing is uploading: sample less often to save CPU.
IDLE_STEP = 5
viewers = {"streams": 0, "last_request": 0.0}
watched_event = threading.Event()  # set when a dashboard shows up, so samplers speed up at once


def watched():
    """True while a dashboard is open (a live stream or a request in the last 20 s)."""
    return viewers["streams"] > 0 or time.time() - viewers["last_request"] < 20


def saw_viewer():
    was = watched()
    viewers["last_request"] = time.time()
    if not was:
        watched_event.set()


def sample_step():
    return 1 if watched() or state["phase"] in ("uploading", "scanning") else IDLE_STEP


def pause_for(seconds):
    """Sleep, but wake early when a dashboard opens."""
    if watched_event.wait(seconds):
        watched_event.clear()


def _repeat(d, value, n):
    for _ in range(n):
        d.append(value)


def sampler():
    """Network speed, CPU, memory and disk: every second while watched or busy, else every 5 s.

    Slow samples are repeated so each chart point still stands for one second."""
    prev_rx, prev_tx = read_net_counters()
    prev_cpu, prev_io = read_cpu_seconds(), read_io()
    prev_t = last_flush = time.time()
    pending = {"net_sent": 0, "net_received": 0, "peak_up": 0.0, "peak_down": 0.0}
    while True:
        pause_for(sample_step())
        rx, tx = read_net_counters()
        cpu, io = read_cpu_seconds(), read_io()
        now = time.time()
        dt = max(now - prev_t, 0.001)
        n = max(1, min(round(dt), NET_HISTORY))
        got, sent = max(rx - prev_rx, 0), max(tx - prev_tx, 0)
        _repeat(net["down"], got / dt, n)
        _repeat(net["up"], sent / dt, n)
        net["received"] += got
        net["sent"] += sent
        pending["net_received"] += got
        pending["net_sent"] += sent
        pending["peak_up"] = max(pending["peak_up"], sent / dt)
        pending["peak_down"] = max(pending["peak_down"], got / dt)
        used, limit = read_memory()
        _repeat(res["cpu"], max(cpu - prev_cpu, 0) / dt * 100, n)
        _repeat(res["mem"], used, n)
        _repeat(res["read"], max(io[0] - prev_io[0], 0) / dt, n)
        _repeat(res["write"], max(io[1] - prev_io[1], 0) / dt, n)
        res["mem_limit"] = limit
        prev_rx, prev_tx, prev_cpu, prev_io, prev_t = rx, tx, cpu, io, now
        # Write once a minute, and when the hour changes so traffic lands in the right hour.
        if now - last_flush >= 60 or int(now // 3600) != int(last_flush // 3600):
            add_stats(last_flush if int(now // 3600) != int(last_flush // 3600) else now, **pending)
            pending = dict.fromkeys(pending, 0)
            last_flush = now


# ---------------------------------------------------------------- resources and Google latency

res = {
    "cpu": collections.deque(maxlen=NET_HISTORY),  # percent of one core
    "mem": collections.deque(maxlen=NET_HISTORY),  # bytes
    "read": collections.deque(maxlen=NET_HISTORY),  # disk bytes per second
    "write": collections.deque(maxlen=NET_HISTORY),
    "mem_limit": 0,
    "cores": os.cpu_count() or 1,
}
latency = collections.deque(maxlen=120)  # (unix time, milliseconds or None) every 30 s
LATENCY_HOST = ("photos.googleapis.com", 443)
CGROUP = Path("/sys/fs/cgroup")


def _read(path):
    try:
        return Path(path).read_text()
    except OSError:
        return ""


def read_cpu_seconds():
    """CPU time used by this container (cgroup v2), or by this process."""
    for line in _read(CGROUP / "cpu.stat").splitlines():
        if line.startswith("usage_usec "):
            return int(line.split()[1]) / 1e6
    fields = _read("/proc/self/stat").rsplit(")", 1)[-1].split()
    return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK") if len(fields) > 12 else 0.0


def read_memory():
    """(bytes used, limit in bytes) for this container, or for this process and the machine."""
    used = _read(CGROUP / "memory.current").strip()
    limit = _read(CGROUP / "memory.max").strip()
    if used.isdigit():
        # Like `docker stats`: leave out file cache the kernel can drop at any time.
        for line in _read(CGROUP / "memory.stat").splitlines():
            if line.startswith("inactive_file "):
                used = str(max(0, int(used) - int(line.split()[1])))
                break
    total = 0
    for line in _read("/proc/meminfo").splitlines():
        if line.startswith("MemTotal:"):
            total = int(line.split()[1]) * 1024
    if not used.isdigit():
        for line in _read("/proc/self/status").splitlines():
            if line.startswith("VmRSS:"):
                used = str(int(line.split()[1]) * 1024)
    return int(used or 0), int(limit) if limit.isdigit() else total


def read_io():
    """Total bytes read and written to disk by this container (or process)."""
    rd = wr = 0
    stat = _read(CGROUP / "io.stat")
    if stat:
        for line in stat.splitlines():
            for part in line.split()[1:]:
                k, _, v = part.partition("=")
                if k == "rbytes":
                    rd += int(v)
                elif k == "wbytes":
                    wr += int(v)
        return rd, wr
    for line in _read("/proc/self/io").splitlines():
        k, _, v = line.partition(":")
        if k == "read_bytes":
            rd = int(v)
        elif k == "write_bytes":
            wr = int(v)
    return rd, wr


def measure_latency(host=LATENCY_HOST, timeout=5):
    """Milliseconds to open a connection to Google Photos (DNS + TCP), or None if it fails."""
    start = time.perf_counter()
    try:
        with socket.create_connection(host, timeout=timeout):
            return (time.perf_counter() - start) * 1000
    except OSError:
        return None


def latency_sampler():
    """Every 30 s while a dashboard is open or uploads run, every 5 min otherwise."""
    while True:
        latency.append((time.time(), measure_latency()))
        busy = watched() or state["phase"] == "uploading"
        deadline = time.time() + (30 if busy else 300)
        while time.time() < deadline:
            pause_for(max(1, deadline - time.time()))
            if watched() and deadline - time.time() > 30:
                break  # a dashboard just opened: measure now


def resources_json():
    def last(d):
        return d[-1] if d else 0

    ok = [ms for _, ms in latency if ms is not None]
    return {
        # Rounded: the live view sends this every couple of seconds, so it should be small.
        "cpu": [round(v, 1) for v in res["cpu"]],
        "mem": [int(v) for v in res["mem"]],
        "read": [int(v) for v in res["read"]],
        "write": [int(v) for v in res["write"]],
        "cpu_now": round(last(res["cpu"]), 1),
        "mem_now": int(last(res["mem"])),
        "read_now": int(last(res["read"])),
        "write_now": int(last(res["write"])),
        "mem_limit": res["mem_limit"],
        "cores": res["cores"],
        "latency": [{"time": int(t), "ms": None if ms is None else round(ms, 1)} for t, ms in latency],
        "latency_now": latency[-1][1] if latency else None,
        "latency_avg": sum(ok) / len(ok) if ok else None,
    }


# ---------------------------------------------------------------- schedule and speed limit


def local_hour(st, now=None):
    return int(((now or time.time()) + st["tz_offset_min"] * 60) // 3600) % 24


def upload_allowed(st=None, now=None):
    """True when the schedule lets uploads run now."""
    st = st or get_settings()
    s = st["schedule"]
    if not s["enabled"] or s["start"] == s["end"]:
        return True
    h = local_hour(st, now)
    return s["start"] <= h < s["end"] if s["start"] < s["end"] else (h >= s["start"] or h < s["end"])


def next_window(st=None, now=None):
    """Unix time when the upload window next opens (now if it is open)."""
    st = st or get_settings()
    now = now or time.time()
    if upload_allowed(st, now):
        return now
    # Local hours start on the hour in the viewer's time zone, which is not a UTC hour in zones
    # such as India (+05:30) or Nepal (+05:45).
    offset = int(st["tz_offset_min"]) * 60
    t = ((int(now) + offset) // 3600 + 1) * 3600 - offset
    for _ in range(25):
        if upload_allowed(st, t):
            return t
        t += 3600
    return now


class Throttle:
    """Sleeps inside gpmc's progress callback so the upload stays under the speed limit."""

    def __init__(self, mbps):
        self.rate = mbps * 1e6 / 8 if mbps else 0  # bytes per second
        self.start = None

    def __call__(self, bytes_done):
        if not self.rate:
            return
        if self.start is None:
            self.start = time.monotonic()
            return
        ahead = bytes_done / self.rate - (time.monotonic() - self.start)
        if ahead > 0:
            time.sleep(min(ahead, 2.0))


# ---------------------------------------------------------------- sync


def scan(st=None):
    """Count the library and return files that are new, changed or need another account.

    Returns [(path, size, mtime, account)] for files that finished copying."""
    st = st or get_settings()
    now = time.time()
    tmap = folder_targets(st)
    fmap = {t: (a[0] if a else "") for t, a in tmap.items()}
    known = {
        p: (s, m, status, a, acc)
        for p, s, m, status, a, acc in q("SELECT path,size,mtime,status,attempts,account FROM files")
    }
    known_copies = {
        (p, acc): (s, m, status, a)
        for p, acc, s, m, status, a in q("SELECT path,account,size,mtime,status,attempts FROM copies")
    }
    todo = []
    index = []
    copies_todo = []  # the same files for a folder's other accounts, after the main backup
    lib = {
        "files": 0,
        "bytes": 0,
        "synced_files": 0,
        "synced_bytes": 0,
        "photos": 0,
        "photo_bytes": 0,
        "videos": 0,
        "video_bytes": 0,
        "types": {},  # extension -> [files, bytes]
    }
    folders = {
        t: {
            "files": 0,
            "bytes": 0,
            "synced_files": 0,
            "synced_bytes": 0,
            "account": a,
            "accounts": tmap[t],
            "copies": dict.fromkeys(tmap[t][1:], 0),  # extra account -> files copied there
        }
        for t, a in fmap.items()
    }
    # Snapchat memories, and files here they stand in for, are never backed up as they are: the
    # Snapchat page first looks for each one in Google (see "Snapchat memories").
    # Everything below a Snapchat export folder (one with the list, or the export script's marker)
    # waits for the list, wherever the folder is: a file the script just wrote isn't listed yet.
    snap_paths, snap_skip = set(_snap["paths"]), set(_snap["skip"])
    manifests, snap_roots, held, exporting = [], [], 0, []
    for top, account in fmap.items():
        for root, dirs, names in os.walk(MEDIA / top):
            dirs[:] = sorted(d for d in dirs if not skipped(d))
            if snapchat.MANIFEST in names or snapchat.RUNNING in names:  # seen before the folders below it
                snap_roots.append(os.path.join(root, ""))
                if snapchat.RUNNING in names:
                    exporting.append(rel(root))
                if snapchat.MANIFEST in names:
                    manifests.append(os.path.join(root, snapchat.MANIFEST))
                    snap_paths |= snapchat_load(manifests[-1])
            in_export = any(os.path.join(root, "").startswith(r) for r in snap_roots)
            for name in sorted(names):
                if ext(name) not in EXTS or skipped(name):
                    continue
                path = os.path.join(root, name)
                try:
                    fst = os.stat(path)
                except OSError:
                    continue
                if fst.st_size == 0:
                    continue
                f = folders[top]
                f["files"] += 1
                f["bytes"] += fst.st_size
                row = known.get(path)
                index.append(
                    (
                        rel(path),
                        ext(name) in VIDEO_EXTS,
                        fst.st_size,
                        added_time(fst),
                        bool(account)
                        and bool(row)
                        and row[0] == fst.st_size
                        and row[1] == fst.st_mtime
                        and row[2] in DONE
                        and row[4] in (account, None),
                        fst.st_mtime,
                    )
                )
                if not account:
                    continue  # folder not backed up
                lib["files"] += 1
                lib["bytes"] += fst.st_size
                e = ext(name)
                kind = "video" if e in VIDEO_EXTS else "photo"
                lib[kind + "s"] += 1
                lib[kind + "_bytes"] += fst.st_size
                t = lib["types"].setdefault(e, [0, 0])
                t[0] += 1
                t[1] += fst.st_size
                if path in snap_paths or path in snap_skip or in_export:
                    held += in_export and path not in snap_paths
                    if row and row[2] in DONE:
                        for d in (lib, f):
                            d["synced_files"] += 1
                            d["synced_bytes"] += fst.st_size
                    continue
                settled = now - fst.st_mtime >= SETTLE
                for extra in tmap[top][1:]:
                    c = known_copies.get((path, extra))
                    if c and c[0] == fst.st_size and c[1] == fst.st_mtime:
                        if c[2] in DONE:
                            f["copies"][extra] += 1
                            continue
                        if c[3] >= MAX_ATTEMPTS:
                            continue
                    elif c:  # file changed: copy it again
                        q("UPDATE copies SET attempts=0 WHERE path=? AND account=?", (path, extra))
                    if settled:
                        copies_todo.append((path, fst.st_size, fst.st_mtime, extra, True))
                if row and row[0] == fst.st_size and row[1] == fst.st_mtime and row[4] in (account, None):
                    if row[2] in DONE:
                        for d in (lib, f):
                            d["synced_files"] += 1
                            d["synced_bytes"] += fst.st_size
                        continue
                    if row[3] >= MAX_ATTEMPTS:
                        continue
                elif row:  # file changed, or its folder moved to another account: start over
                    q("UPDATE files SET attempts=0 WHERE path=?", (path,))
                if now - fst.st_mtime < SETTLE:
                    continue
                todo.append((path, fst.st_size, fst.st_mtime, account))
    prio = st.get("priority") or []
    if prio:  # folders picked to go first; the rest keeps its usual order
        todo.sort(key=lambda i: priority_rank(rel(i[0]), prio))
        copies_todo.sort(key=lambda i: priority_rank(rel(i[0]), prio))
    snapchat_forget(manifests)
    state["snap_held"], state["snap_exporting"] = held, exporting
    state["library"] = lib
    state["folders"] = folders
    state["index"] = index
    state["pending"] = [rel(p) for p, *_ in todo[:500]]
    state["copies_pending"] = len(copies_todo)
    return todo + copies_todo


def priority_rank(name, prio):
    """Where a file goes in the upload order: its folder's place in the priority list, or last."""
    for i, p in enumerate(prio):
        if name == p or name.startswith(p + "/"):
            return i
    return len(prio)


class UploadQueue:
    """The files of one upload round. If the priority list changes while it runs, the files not
    sent yet are put in the new order at once (no need to wait for the next scan)."""

    def __init__(self, items):
        self.items = list(items)
        self.seen = None

    def __iter__(self):
        return self

    def __next__(self):
        if not self.items:
            raise StopIteration
        prio = get_settings().get("priority") or []
        if prio != self.seen:
            if self.seen is not None:
                self.items.sort(key=lambda i: priority_rank(rel(i[0]), prio))
                state["pending"] = [rel(p) for p, *_ in self.items[:500]]
            self.seen = prio
        return self.items.pop(0)


def added_time(fst):
    """When a file arrived on this machine. mtime is often the photo's own date (kept by
    copy tools), so use the inode change time when it is later."""
    return max(fst.st_mtime, fst.st_ctime)


def clean_folder(path):
    """A folder path from the dashboard ("Photos/iPhone"), or None when it isn't safe."""
    parts = [p for p in str(path or "").replace("\\", "/").split("/") if p]
    if any(p in (".", "..") or skipped(p) for p in parts):
        return None
    return "/".join(parts)


def _device_name(folder):
    """Folders for the same phone get numbered when it pairs again ("I2220 (2)")."""
    return re.sub(r" \(\d+\)$", "", folder)


def _blank():
    return {"photos": 0, "videos": 0, "photo_bytes": 0, "video_bytes": 0, "synced": 0, "new": 0, "last_added": 0}


def folder_report(path="", group="folders", days=30, tz_offset_min=0, now=None):
    """Counts for any folder: totals, files added per day, and the same per subfolder.

    group="devices" lists the folders one level inside each backed-up top folder (where
    phone backup apps put one folder per device), merged by device name."""
    now = now or time.time()
    shift = tz_offset_min * 60
    today = int((now + shift) // 86400)
    first = today - days + 1
    index = state["index"]
    total = _blank()
    daily = {}  # day number -> [photos, videos, backed up, waiting, failed]
    groups = {}
    file_states = {rel(p): st for p, st in q("SELECT path, status FROM files")}
    backed = backed_folders()
    skipped_tops = {}  # top folder not backed up -> files in it (shown as a note, not counted)
    uploads = upload_folder_name()
    if group == "devices":

        def key_of(name):
            parts = name.split("/")
            if len(parts) < 3 or (backed is not None and parts[0] not in backed):
                return None
            folder = f"{parts[0]}/{parts[1]}"
            return (_device_name(parts[1]), folder)
    else:
        prefix = path + "/" if path else ""
        depth = len(prefix.split("/")) - 1 if prefix else 0

        def key_of(name):
            if not name.startswith(prefix):
                return None
            parts = name.split("/")
            if not path and backed is not None and parts[0] not in backed:
                skipped_tops[parts[0]] = skipped_tops.get(parts[0], 0) + 1
                return None  # the overview only counts folders that are backed up
            sub = parts[depth] if len(parts) > depth + 1 else ""
            return (sub, prefix + sub if sub else path)

    for name, video, size, added, synced, *_ in index:
        k = key_of(name)
        if k is None:
            continue
        label, folder = k
        g = groups.get(label)
        if g is None:
            g = groups[label] = {**_blank(), "folders": set(), "sub": False, "daily": {}, "recent": [], "subs": set()}
        g["folders"].add(folder)
        if len(g["recent"]) < 4:
            heapq.heappush(g["recent"], (added, name))
        elif added > g["recent"][0][0]:
            heapq.heapreplace(g["recent"], (added, name))
        if label and name.count("/") > folder.count("/") + 1:
            g["sub"] = True  # has folders inside: the dashboard can open it
            g["subs"].add(name[len(folder) + 1 :].split("/", 1)[0])
        for d in (total, g):
            d["videos" if video else "photos"] += 1
            d["video_bytes" if video else "photo_bytes"] += size
            d["synced"] += synced
            d["last_added"] = max(d["last_added"], added)
        day = int((added + shift) // 86400)
        if first <= day <= today:
            total["new"] += 1
            g["new"] += 1
            counts = daily.setdefault(day, [0, 0, 0, 0, 0])
            counts[int(bool(video))] += 1
            st = file_states.get(name)
            counts[2 if synced or st in DONE else 4 if st == "failed" else 3] += 1
            g["daily"][day] = g["daily"].get(day, 0) + 1

    def day_str(n):
        return time.strftime("%Y-%m-%d", time.gmtime(n * 86400))

    def week(d):
        return sum(c for day, c in d.items() if day > today - 7)

    children = []
    for label, g in groups.items():
        files = g["photos"] + g["videos"]
        children.append(
            {
                "name": label,
                "folders": sorted(g["folders"]),
                "has_sub": g["sub"],
                "files": files,
                "photos": g["photos"],
                "videos": g["videos"],
                "bytes": g["photo_bytes"] + g["video_bytes"],
                "synced": g["synced"],
                "new": g["new"],
                "new_week": week(g["daily"]),
                "last_added": g["last_added"],
                "series": [g["daily"].get(n, 0) for n in range(first, today + 1)],
                "uploads": group == "devices" and uploads in g["folders"],
                "recent": [n for _, n in sorted(g["recent"], reverse=True)],
                # A phone's folder: a backup app filled it with folders 00..ff
                "device": group == "devices" or (len(g["subs"]) >= 4 and all(BUCKET.fullmatch(n) for n in g["subs"])),
            }
        )
    children.sort(key=lambda c: (c["name"] == "", -c["files"], c["name"].lower()))
    # Phone backup apps spread files over folders 00..ff. Those folders mean nothing to a person:
    # don't list them, the dashboard shows the photos of the whole folder instead.
    named = [c["name"] for c in children if c["name"]]
    buckets = group == "folders" and len(named) >= 4 and all(BUCKET.fullmatch(n) for n in named)
    return {
        "ready": bool(state["last_scan"]),
        "path": path if group == "folders" else "",
        "group": group,
        "days": days,
        "today": day_str(today),
        "total": {
            **{k: total[k] for k in ("photos", "videos", "photo_bytes", "video_bytes", "synced", "new")},
            "files": total["photos"] + total["videos"],
            "bytes": total["photo_bytes"] + total["video_bytes"],
            "new_week": sum(c[0] + c[1] for day, c in daily.items() if day > today - 7),
            "new_day": sum(c[0] + c[1] for day, c in daily.items() if day == today),
            "last_added": total["last_added"],
        },
        "daily": [
            dict(
                zip(
                    ("day", "photos", "videos", "backed_up", "waiting", "failed"),
                    (day_str(n), *daily.get(n, [0, 0, 0, 0, 0])),
                    strict=True,
                )
            )
            for n in range(first, today + 1)
        ],
        "children": [] if buckets else children,
        "buckets": buckets,
        "not_backed_up": [{"name": k, "files": v} for k, v in sorted(skipped_tops.items())],
    }


def backed_folders():
    """Top folders that are backed up, or None when none are yet (then everything is shown)."""
    tops = {t for t, a in folder_targets().items() if a}
    return tops or None


BUCKET = re.compile(r"[0-9a-f]{2}", re.I)
FILE_SORTS = {
    "new": (lambda e: e[3], True),
    "old": (lambda e: e[3], False),
    "size": (lambda e: e[2], True),
    "name": (lambda e: e[0].rsplit("/", 1)[-1].lower(), False),
}


def folder_files(
    path="", status="all", kind="all", sort="new", offset=0, limit=60, day="", tz_offset_min=0, group="folders"
):
    """Every photo and video in a folder and the folders inside it, backed up yet or not."""
    prefix = path + "/" if path else ""
    backed = None if path else backed_folders()
    entries = [
        e for e in state["index"] if e[0].startswith(prefix) and (backed is None or e[0].split("/", 1)[0] in backed)
    ]
    if group == "devices" and not path:
        entries = [e for e in entries if e[0].count("/") >= 2]
    if day:
        try:
            day_number = int(calendar.timegm(time.strptime(day, "%Y-%m-%d")) // 86400)
            if time.strftime("%Y-%m-%d", time.gmtime(day_number * 86400)) != day:
                raise ValueError
        except (ValueError, OverflowError):
            raise ValueError("bad day") from None
        entries = [e for e in entries if int((e[3] + tz_offset_min * 60) // 86400) == day_number]
    states = {
        rel(p): (st, err)
        for p, st, err in q(
            "SELECT path, status, error FROM files WHERE path LIKE ? ESCAPE '\\'",
            (str(MEDIA / prefix).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",),
        )
    }

    def state_of(e):
        st = states.get(e[0], (None, ""))[0]
        if e[4] or st in DONE:  # backed up at the last scan, or uploaded since
            return "backed_up"
        return "failed" if st == "failed" else "waiting"

    counts = {"all": len(entries), "backed_up": 0, "waiting": 0, "failed": 0, "photo": 0, "video": 0}
    for e in entries:
        counts[state_of(e)] += 1
        counts["video" if e[1] else "photo"] += 1
    picked = [
        e
        for e in entries
        if (status == "all" or state_of(e) == status) and (kind == "all" or (kind == "video") == e[1])
    ]
    key, reverse = FILE_SORTS.get(sort, FILE_SORTS["new"])
    picked.sort(key=key, reverse=reverse)
    return {
        "path": path,
        "total": len(picked),
        "counts": counts,
        "items": [
            {
                "name": e[0],
                "kind": "video" if e[1] else "photo",
                "size": e[2],
                "added": e[3],
                "mtime": int(e[5]) if len(e) > 5 else 0,
                "state": state_of(e),
                "error": states.get(e[0], (None, ""))[1] or "",
            }
            for e in picked[offset : offset + limit]
        ],
    }


def _is_missing(e):
    """True when Google says the item is gone (HTTP 404): a stale media key, or a duplicate of an
    item just freed. Freeing it is pointless, and it should not be treated as a failure."""
    import requests

    return isinstance(e, requests.HTTPError) and e.response is not None and e.response.status_code == 404


def outage_kind(e):
    """Return a message if the error is about the login, network or Google, not the file."""
    import requests

    if isinstance(e, requests.HTTPError) and e.response is not None:
        code = e.response.status_code
        if code in (401, 403):
            return "Google rejected the login. Paste a fresh androidId= line."
        if code == 429 or code >= 500:
            return f"Google is busy or down (HTTP {code}). Will retry next scan."
    if isinstance(e, (requests.ConnectionError, requests.Timeout)):
        return "No connection to Google. Will retry next scan."
    if any(s in str(e) for s in ("BadAuthentication", "NeedsBrowser")):
        return "Google rejected the login. Paste a fresh androidId= line."
    return ""


def album_name(path, mode):
    """Album for a file: "top" = first folder below the top-level folder, "parent" = the file's own folder."""
    parts = rel(path).split("/")[:-1]
    if mode == "off" or not parts:
        return None
    if mode == "top":
        return parts[1] if len(parts) > 1 else parts[0]
    return parts[-1]


albums_lock = threading.Lock()  # uploads and the date worker both add to albums


_album_error = [None]  # the last error add_to_album saw (album_add looks at it)


def add_to_album(client, account, name, keys, quiet=False):
    """Add media to an album, creating it the first time (or again if it was deleted in Google
    Photos). Returns True if it worked. `quiet`: the caller reports failures."""
    _album_error[0] = None
    with albums_lock:
        row = q("SELECT album_key FROM albums WHERE account=? AND name=?", (account, name))
        try:
            try:
                if not row:
                    raise LookupError
                client.api.add_media_to_album(album_media_key=row[0][0], media_keys=keys)
            except Exception as e:  # no album yet, or it was deleted in Google Photos: make a new one
                if outage_kind(e):
                    raise
                key = client.api.create_album(album_name=name, media_keys=keys)
                q("INSERT OR REPLACE INTO albums VALUES (?,?,?)", (account, name, key))
            log(f"Added {len(keys)} item(s) to album {name}", "album")
            return True
        except Exception as e:
            _album_error[0] = e
            if not quiet:
                log(f"Could not update album {name}: {type(e).__name__}: {e}", "error")
            return False


_VISIBLE = "COALESCE(trash_timestamp, 0) = 0 AND COALESCE(is_locked, 0) = 0"
_album_refused = {}  # (account, media key) -> when Google refused it; tried again later
ALBUM_RETRY = 6 * 3600


def _usable_keys(account, items):
    """Check album items [(path, media key)] against the account's library list. A key Google no
    longer has (the item was replaced, by Free storage for example) is swapped for the item's
    current one, found by its fingerprint. Items gone from Google, or in its Trash or Locked folder,
    are left out once the list was read after their upload. Returns (items to add, paths left out)."""
    refreshed = float(meta_get(f"library_refreshed:{account}") or 0)
    if not refreshed:
        return items, []
    ok = set()
    keys = [k for _, k in items]
    for i in range(0, len(keys), 500):
        part = keys[i : i + 500]
        rows = _library_rows(
            account,
            f"SELECT media_key FROM remote_media WHERE media_key IN ({','.join('?' * len(part))}) AND {_VISIBLE}",
            part,
        )
        if rows is None:  # no list here yet
            return items, []
        ok |= {r[0] for r in rows}
    missing = [p for p, k in items if k not in ok]
    if not missing:
        return items, []
    info = {
        p: (sha, up)
        for p, sha, up in q(
            "SELECT path, sha1, updated FROM files WHERE path IN (SELECT value FROM json_each(?))",
            (json.dumps(missing),),
        )
    }
    dedup = {p: _b64_hash(bytes.fromhex(sha)) for p, (sha, _) in info.items() if sha}
    current = {}
    wanted = sorted(set(dedup.values()))
    for i in range(0, len(wanted), 500):
        part = wanted[i : i + 500]
        for d, k in (
            _library_rows(
                account,
                f"""SELECT dedup_key, media_key FROM remote_media WHERE dedup_key IN ({",".join("?" * len(part))})
                AND {_VISIBLE} ORDER BY is_canonical""",  # the main copy last, so it wins
                part,
            )
            or []
        ):
            current[d] = k
    out, gone = [], []
    for p, k in items:
        if k in ok:
            out.append((p, k))
        elif dedup.get(p) in current:
            new = current[dedup[p]]
            out.append((p, new))
            q("UPDATE files SET media_key=? WHERE path=? AND media_key=?", (new, p, k))
        elif refreshed > (info.get(p, (None, 0))[1] or 0):
            gone.append(p)
        else:
            out.append((p, k))  # uploaded after the list was read: try it as it is
    return out, gone


def album_add(client, account, name, items):
    """Add files [(path, media key)] to album `name`. One item Google can't find makes it refuse a
    whole batch, so a refused batch is split to find it, and the rest still goes in.
    Returns (paths added, paths left out, ok); ok is False when nothing could be added."""
    now = time.time()
    items = [(p, k) for p, k in items if now - _album_refused.get((account, k), 0) > ALBUM_RETRY]
    items, gone = _usable_keys(account, items)
    added, refused, calls = [], [], [0]

    def put(part):
        calls[0] += 1
        if add_to_album(client, account, name, [k for _, k in part], quiet=True):
            added.extend(part)
            return
        if outage_kind(_album_error[0]) or len(part) == 1 or calls[0] >= 24:
            refused.extend(part)
            return
        put(part[: len(part) // 2])
        put(part[len(part) // 2 :])

    if items:
        put(items)
    err = _album_error[0]
    if refused and (added or len(refused) < len(items)):
        for _, k in refused:
            _album_refused[(account, k)] = now
        _refresh_requests.append(account)  # read the library: their current keys are found then
        log(f"Album {name}: Google Photos couldn't find {len(refused)} item(s); trying them again later", "album")
    ok = bool(added) or not items
    if not ok and items and err is not None:
        log(f"Could not update album {name}: {type(err).__name__}: {err}", "error")
    return [p for p, _ in added], gone + [p for p, _ in refused], ok


def flush_albums(client, account, queued):
    """Add uploaded media to their albums, creating albums the first time."""
    for name, keys in list(queued.items()):
        if keys:
            add_to_album(client, account, name, keys)
        queued[name] = []


def alert(title, message, tags="", event=None):
    cfg = get_settings()["notify"]
    if event and not cfg.get(event, True):
        return
    notify.send(cfg, title, message, tags, on_done=lambda r: _alert_result(title, r))


def _alert_result(title, result):
    bad = {k: v for k, v in result.items() if v != "ok"}
    if bad:
        log(f"Alert '{title}' failed: {bad}", "error")


def upload_all(client, todo, account="", st=None):
    """Upload files for one account. Stops early for pause, schedule or outages."""
    from rich.progress import Progress

    st = st or get_settings()
    if not account and todo and len(todo[0]) > 3:
        account = todo[0][3]  # every item in one call belongs to the same account
    state.update(phase="uploading", done=0, total=len(todo))
    queued = collections.defaultdict(list)
    rnd = {"files": 0, "bytes": 0}
    workers = max(1, min(UPLOAD_MAX_WORKERS, int(st.get("upload_workers") or 1)))
    items = UploadQueue(todo)
    lock = threading.RLock()  # counters, the queue and album batches are shared by the workers
    stop = threading.Event()  # pause, upload window or an outage: no new file starts
    state["uploading"] = {}

    def next_item():
        with lock:
            if stop.is_set():
                return None
            if PAUSE_FILE.exists():
                log("Paused", "pause")
                stop.set()
                return None
            if not upload_allowed(st):
                log("Upload window closed; continuing when it opens", "pause")
                stop.set()
                return None
            return next(items, None)

    def run():
        while (item := next_item()) is not None:
            one(*item)

    def one(path, size, mtime, *rest):
        """Upload one file (on a worker thread when several run at once)."""
        copy = len(rest) > 1 and rest[1]  # another account's copy, not the folder's main backup
        name = rel(path)
        cur = {
            "name": name,
            "bytes_done": 0,
            "bytes_total": size,
            "step": "hashing",
            "kind": "video" if ext(name) in VIDEO_EXTS else "photo",
            "account": account,
        }
        with lock:
            state["current"] = cur
            state["uploading"][name] = cur
        phases = []
        throttle = Throttle(st["speed_limit_mbps"] / workers)  # the limit is shared by the workers
        started = time.monotonic()

        def progress(ev, cur=cur, phases=phases, throttle=throttle):
            phase = ev.get("phase")
            phases.append(phase)
            cur["step"] = phase
            if phase == "uploading":
                cur["bytes_done"] = ev.get("bytes_completed") or 0
                throttle(cur["bytes_done"])

        try:
            digest = media.sha1_file(path, on_progress=lambda n, cur=cur: cur.update(bytes_done=n))
            cur.update(step="checking", bytes_done=0)
            # No date inside the file: Google uses the one sent with the upload, so send the
            # best one known (a date the user picked, or the earliest identical copy here).
            when = None
            if date_facts(path, size, mtime) == 0:
                remember_sha1(path, digest.hex())
                # Labelled before upload, so a new Snapchat save gets its estimated date.
                if source_facts(path, size, mtime) and estimated_date(path) is None and _is_snap(path):
                    refresh_estimates()
                when = best_date(path, size, mtime, digest.hex())[0]
            # Single-file upload raises on failure; the batch upload() only logs errors.
            # gpmc is pinned in pyproject.toml, so this internal method stays stable.
            with Progress(disable=True) as bar, upload_date(client, when, mtime):
                result = client._upload_file(
                    Path(path), digest, bar, force_upload=False, use_quota=False, saver=False,
                    progress_callback=progress,
                )  # fmt: skip
            key = next(iter(result.values()), "")
            if not key:
                raise RuntimeError("Google returned no media key")
            status = "exists" if "skipped" in phases else "uploaded"
            if copy:
                q(
                    """INSERT OR REPLACE INTO copies
                       (path, account, size, mtime, status, media_key, error, attempts, updated)
                       VALUES (?,?,?,?,?,?,'',0,?)""",
                    (path, account, size, mtime, status, key, time.time()),
                )
            else:
                q(
                    """INSERT OR REPLACE INTO files
                       (path, size, mtime, status, media_key, error, attempts, updated, account, sha1)
                       VALUES (?,?,?,?,?,'',0,?,?,?)""",
                    (path, size, mtime, status, key, time.time(), account, digest.hex()),
                )
                with lock:
                    state["library"]["synced_files"] += 1
                    state["library"]["synced_bytes"] += size
                if when is not None and status == "uploaded":
                    sq("UPDATE media_dates SET sent=? WHERE path=?", (int(when), path))
            state["login_failed"].discard(account)
            if (album := album_name(path, st["albums"])) is not None:
                with lock:
                    queued[album].append(key)
                    if len(queued[album]) >= 100:
                        flush_albums(client, account, queued)
            ms = int((time.monotonic() - started) * 1000)
            save_upload(name, cur["kind"], size, ms, status, account)
            if status == "uploaded":
                with lock:
                    state["session"]["uploaded_files"] += 1
                    state["session"]["uploaded_bytes"] += size
                    rnd["files"] += 1
                    rnd["bytes"] += size
                if cur["kind"] == "video":
                    add_stats(videos=1, video_bytes=size, video_ms=ms)
                else:
                    add_stats(photos=1, photo_bytes=size, photo_ms=ms)
                to = f" to {account}" if copy else ""
                log(f"Uploaded {name}{to} ({fmt_bytes(size)} in {fmt_secs(ms / 1000)})", "uploaded", name)
            else:
                add_stats(existing=1)
                log(f"Already in Google Photos{f' ({account})' if copy else ''}: {name}", "exists", name)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"[:500]
            problem = outage_kind(e)
            if problem:
                # Not this file's fault: stop the round and try again later without
                # using up the file's retries.
                with lock:
                    first = not stop.is_set()
                    stop.set()
                if first:
                    state["error"] = problem if not account else f"{account}: {problem}"
                    log(f"Stopped this round: {state['error']}", "error")
                if first and "login" in problem and account not in state["login_failed"]:
                    state["login_failed"].add(account)
                    alert("GP Sync: login rejected", f"{account}: {problem}", "warning", "on_login")
                return
            attempts = (
                q(
                    """INSERT INTO copies (path,account,size,mtime,status,error,attempts,updated)
                   VALUES (?,?,?,?,'failed',?,1,?)
                   ON CONFLICT(path, account) DO UPDATE SET size=excluded.size, mtime=excluded.mtime,
                   status='failed', error=excluded.error, attempts=copies.attempts+1, updated=excluded.updated
                   RETURNING attempts""",
                    (path, account, size, mtime, err, time.time()),
                )[0][0]
                if copy
                else q(
                    """INSERT INTO files (path,size,mtime,status,error,attempts,updated,account)
                   VALUES (?,?,?,'failed',?,1,?,?)
                   ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime, status='failed',
                   error=excluded.error, attempts=files.attempts+1, updated=excluded.updated,
                   account=excluded.account
                   RETURNING attempts""",
                    (path, size, mtime, err, time.time(), account),
                )[0][0]
            )
            add_stats(failed=1)
            save_upload(name, cur["kind"], size, int((time.monotonic() - started) * 1000), "failed", account)
            log(f"Failed {name}{f' ({account})' if copy else ''}: {err}", "failed", name)
            if attempts >= MAX_ATTEMPTS:
                alert("GP Sync: gave up on a file", f"{name}\n{err}", "x", "on_gave_up")
        finally:
            with lock:
                state["uploading"].pop(name, None)
                state["current"] = next(iter(state["uploading"].values()), None)
        with lock:
            state["done"] += 1

    try:
        if workers == 1:
            run()
        else:  # several files at once, each on its own thread, sharing the account's login
            threads = [threading.Thread(target=run, daemon=True, name=f"upload {n}") for n in range(workers)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
    finally:
        state["uploading"] = {}
        flush_albums(client, account, queued)
        if rnd["files"] and rnd["files"] >= st["notify"]["round_min"]:
            alert(
                "GP Sync: backup done",
                f"Uploaded {rnd['files']} file(s), {fmt_bytes(rnd['bytes'])}" + (f" to {account}" if account else ""),
                "white_check_mark",
                "on_round",
            )


def fmt_secs(secs):
    secs = round(secs)
    if secs < 60:
        return f"{max(secs, 1)} s"
    if secs < 3600:
        return f"{secs // 60} min {secs % 60} s"
    return f"{secs // 3600} h {secs % 3600 // 60} min"


def fmt_bytes(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n} B"


def run_round():
    """One scan and, if allowed, uploads for every account."""
    st = get_settings()
    accs = dict(accounts())
    state["phase"] = "scanning"
    notify_change()
    todo = scan(st)
    state["last_scan"] = time.time()
    if not accs or PAUSE_FILE.exists():
        # Counted the library, but nothing is uploaded until an account is connected or uploads resume.
        state.update(phase="setup" if not accs else "paused")
        if not accs:
            state["error"] = ""
        notify_change()
        return
    if todo and not upload_allowed(st):
        state["phase"] = "scheduled"
        state["next_window"] = next_window(st)
        return
    if todo:
        log(f"Found {len(todo)} new file(s)", "scan")
        state["error"] = ""
        by_account = collections.defaultdict(list)
        for item in todo:
            by_account[item[3]].append(item)
        for account, items in by_account.items():
            if account in accs:
                # The account's one login, shared with its other work (gpmc renews it when due),
                # instead of signing in to Google again every round.
                upload_all(_reclaim_client(account), items, account, st)
            if PAUSE_FILE.exists() or not upload_allowed(st):
                break
    state["phase"] = "paused" if PAUSE_FILE.exists() else "scheduled" if not upload_allowed(st) else "idle"
    if todo:
        background_work.set()
        media.release_memory()
    notify_change()


def weekly_summary(force=False, now=None):
    """Every Monday from 09:00 (viewer's time zone): last week's totals to the alert channels."""
    st = get_settings()
    if not (force or st["notify"]["weekly"]) or not notify.channels(st["notify"]):
        return None
    now = now or time.time()
    offset = st["tz_offset_min"] * 60
    local_day = int((now + offset) // 86400)
    monday = local_day - (local_day + 3) % 7  # 1970-01-01 was a Thursday
    due = monday * 86400 - offset + 9 * 3600
    if not force and (now < due or float(meta_get("weekly_sent") or 0) >= due):
        return None
    days = {d["day"]: d for d in history(8, st["tz_offset_min"])["data"]}
    week = [days.get(time.strftime("%Y-%m-%d", time.gmtime((monday - i) * 86400)), {}) for i in range(1, 8)]
    s = {c: sum(d.get(c, 0) for d in week) for c in STAT_COLUMNS}
    message = (
        f"Last week: {s['photos']} photos and {s['videos']} videos backed up "
        f"({fmt_bytes(s['photo_bytes'] + s['video_bytes'])}).\n"
        f"Network: {fmt_bytes(s['net_sent'])} sent, {fmt_bytes(s['net_received'])} received.\n"
        f"Library: {state['library']['synced_files']} of {state['library']['files']} files backed up."
    )
    meta_set("weekly_sent", str(now))
    alert("GP Sync: weekly summary", message, "bar_chart")
    return message


def worker():
    while True:
        try:
            run_round()
            weekly_summary()
        except Exception as e:
            state.update(phase="error", error=f"{type(e).__name__}: {e}")
            traceback.print_exc()
            log("Error: " + traceback.format_exc().strip().splitlines()[-1], "error")
        wait = INTERVAL
        if state["phase"] == "scheduled" and state["next_window"]:
            wait = max(30, min(INTERVAL, state["next_window"] - time.time() + 5))
        wake.wait(wait)
        wake.clear()


# ---------------------------------------------------------------- duplicates, thumbnails, health


IDLE_PHASES = ("idle", "setup", "scheduled", "paused")


def hash_some(limit=20):
    """Fingerprint up to `limit` uploaded files that have no SHA-1 yet. Returns how many were done."""
    rows = q(
        "SELECT path, size, mtime FROM files WHERE sha1 IS NULL AND status IN ('uploaded','exists') LIMIT ?",
        (limit,),
    )
    for path, size, mtime in rows:
        if state["phase"] not in IDLE_PHASES:
            break
        try:
            fst = os.stat(path)
            # Changed files get a fresh fingerprint when they are uploaded again.
            changed = fst.st_size != size or fst.st_mtime != mtime
            digest = "" if changed else media.sha1_file(path).hex()
        except OSError:
            digest = ""  # gone: mark so we don't retry forever
        q("UPDATE files SET sha1=? WHERE path=? AND sha1 IS NULL", (digest, path))
    return len(rows)


_thumbs_checked = set()  # files whose thumbnail exists (or can't be made), this run


def thumb_some(limit=12):
    """Make missing photo thumbnails ahead of time, so the Library scrolls without waiting.

    Returns how many were made."""
    made = 0
    for (path,) in q("SELECT path FROM files WHERE status IN ('uploaded','exists') ORDER BY updated DESC"):
        if path in _thumbs_checked or ext(path) not in THUMB_EXTS:
            continue
        if made >= limit or state["phase"] not in IDLE_PHASES:
            break
        name = rel(path)
        try:
            p = safe_media_path(name)
            if p and not media.thumb_path(THUMBS, str(p), p.stat().st_mtime).exists():
                thumbnail(name)
                made += 1
        except Exception as e:
            print(f"Thumbnail failed for {name}: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        _thumbs_checked.add(path)
    if made:
        media.release_memory()
    return made


def hasher():
    """When idle: make missing thumbnails, and fingerprint older uploads that have none yet so the
    duplicate finder sees them. Runs at low priority and backs off when there is nothing to do."""
    with contextlib.suppress(OSError, AttributeError):
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)  # this thread only
    while True:
        busy = state["phase"] not in IDLE_PHASES  # check again soon
        try:
            if not busy:
                busy = thumb_some() > 0
                busy = dates_some() > 0 or busy
                if get_settings()["hash_existing"]:
                    busy = hash_some() > 0 or busy
        except Exception as e:
            print(f"Hasher: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        background_work.wait(20 if busy else 600)
        background_work.clear()


def duplicates(limit=200):
    rows = q(
        """SELECT sha1, COUNT(*), MAX(size), GROUP_CONCAT(path, char(10)) FROM files
           WHERE sha1 IS NOT NULL AND sha1 != '' GROUP BY sha1 HAVING COUNT(*) > 1
           ORDER BY MAX(size) * (COUNT(*) - 1) DESC LIMIT ?""",
        (limit,),
    )
    groups = [
        {"sha1": h, "count": n, "size": size, "wasted": size * (n - 1), "files": [rel(p) for p in paths.split("\n")]}
        for h, n, size, paths in rows
    ]
    hashed, total = q(
        "SELECT SUM(sha1 IS NOT NULL AND sha1 != ''), COUNT(*) FROM files WHERE status IN ('uploaded','exists')"
    )[0]
    return {"groups": groups, "wasted": sum(g["wasted"] for g in groups), "hashed": hashed or 0, "total": total}


def safe_media_path(name):
    """Resolve a dashboard file name to a real path inside the media folder, or None."""
    try:
        p = (MEDIA / name).resolve()
        root = MEDIA.resolve()
    except (OSError, ValueError):
        return None
    if root not in p.parents or not p.is_file() or ext(p.name) not in EXTS:
        return None
    return p


def thumbnail(name):
    """Path of a cached WebP thumbnail for a media file, made on first request."""
    p = safe_media_path(name)
    if not p:
        return None
    mtime = p.stat().st_mtime
    target = media.thumb_path(THUMBS, str(p), mtime)
    if target.exists():
        return target
    if ext(p.name) in THUMB_EXTS:
        return media.make_thumbnail(p, target)
    poster = q("SELECT poster FROM snap_items WHERE path=? AND poster IS NOT NULL", (str(p),))
    if poster and os.path.isfile(poster[0][0]):  # a Snapchat memory's first frame
        return media.make_thumbnail(poster[0][0], target)
    # Videos: ask Google for its thumbnail of the uploaded copy.
    row = q("SELECT media_key, account FROM files WHERE path=? AND media_key IS NOT NULL", (str(MEDIA / name),))
    accs = dict(accounts())
    if row and row[0][1] in accs:
        from gpmc.api import Api

        data = Api(accs[row[0][1]]).get_thumbnail(row[0][0], width=media.THUMB_SIZE)
        return media.save_bytes(data, target)
    return None


def google_thumb(name):
    """Thumbnail of an item that is only in Google (for the storage page)."""
    account, _, key = name.partition("\n")
    auth = dict(accounts()).get(account)
    if not auth or not re.fullmatch(r"[\w-]+", key):
        return None
    target = THUMBS / "google" / f"{hashlib.sha1(name.encode()).hexdigest()}.webp"
    if target.exists():
        return target
    from gpmc.api import Api

    return media.save_bytes(Api(auth).get_thumbnail(key, width=media.THUMB_SIZE), target)


PREVIEW_SIZE = 1600
MEDIA_TYPES = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp",
    "avif": "image/avif", "bmp": "image/bmp", "heic": "image/heic", "heif": "image/heif", "tif": "image/tiff",
    "tiff": "image/tiff", "mp4": "video/mp4", "m4v": "video/mp4", "mov": "video/quicktime", "webm": "video/webm",
    "mkv": "video/x-matroska", "3gp": "video/3gpp", "avi": "video/x-msvideo",
}  # fmt: skip


def preview(name):
    """A large WebP of a photo for the viewer (cached like thumbnails), or None."""
    p = safe_media_path(name)
    if not p or ext(p.name) not in THUMB_EXTS:
        return None
    target = media.thumb_path(THUMBS, str(p), f"{p.stat().st_mtime}-{PREVIEW_SIZE}")
    return target if target.exists() else media.make_thumbnail(p, target, PREVIEW_SIZE, 82)


def file_info(name):
    """Everything known about one file: on disk, in the upload state and in recent activity."""
    p = safe_media_path(name)
    full = str(MEDIA / name)
    row = q("SELECT size, status, media_key, account, updated, error, attempts, sha1 FROM files WHERE path=?", (full,))
    size, status, key, account, updated, error, attempts, sha1 = row[0] if row else (None,) * 8
    st = p.stat() if p else None
    timing = sq("SELECT time, ms, result FROM uploads WHERE name=? ORDER BY time DESC LIMIT 1", (name,))
    events = sq("SELECT time, kind, text FROM events WHERE name=? ORDER BY time DESC LIMIT 20", (name,))
    return {
        "name": name,
        "exists": bool(p),
        "kind": "video" if ext(name) in VIDEO_EXTS else "photo",
        "size": st.st_size if st else size,
        "modified": st.st_mtime if st else None,
        "status": status or ("waiting" if p else "missing"),
        "account": account or folder_map().get(top_folder(full), ""),
        "updated": updated,
        "error": error or "",
        "attempts": attempts or 0,
        "max_attempts": MAX_ATTEMPTS,
        "sha1": sha1 or "",
        "google_url": f"https://photos.google.com/photo/{key}" if key else "",
        "upload_ms": timing[0][1] if timing else None,
        "history": [{"time": t, "kind": k, "text": x} for t, k, x in events],
        "previewable": ext(name) in THUMB_EXTS,
        "playable": ext(name) in ("mp4", "m4v", "mov", "webm"),
    }


def retry_file(name):
    """Try one failed file again at the next scan."""
    n = q("UPDATE files SET attempts=0 WHERE path=? AND status='failed' RETURNING path", (str(MEDIA / name),))
    if n:
        log(f"Retrying {name}", "info", name)
        wake.set()
    return bool(n)


def list_files(query="", status="all", folder="", sort="new", limit=60, offset=0):
    where, args = [], []
    if query:
        where.append("path LIKE ? ESCAPE '\\'")
        args.append(f"%{like(query)}%")
    if status in ("uploaded", "exists", "failed"):
        where.append("status=?")
        args.append(status)
    undated = undated_paths()
    if status == "undated":  # no date of their own
        where.append("path IN (SELECT value FROM json_each(?))")
        args.append(json.dumps(sorted(undated)))
    if folder:
        where.append("path LIKE ? ESCAPE '\\'")
        args.append(f"{like(f'{MEDIA}/{folder}/')}%")
    order = {"new": "updated DESC", "old": "updated ASC", "name": "path ASC", "size": "size DESC"}.get(
        sort, "updated DESC"
    )
    sql_where = ("WHERE " + " AND ".join(where)) if where else ""
    total = q(f"SELECT COUNT(*) FROM files {sql_where}", args)[0][0]
    rows = q(
        f"""SELECT path, size, status, media_key, account, updated, error, attempts, mtime FROM files {sql_where}
            ORDER BY {order} LIMIT ? OFFSET ?""",
        (*args, int(limit), int(offset)),
    )
    items = [
        {
            "name": rel(p),
            "size": size,
            "status": s,
            "kind": "video" if ext(p) in VIDEO_EXTS else "photo",
            "account": acc,
            "updated": t,
            "error": err,
            "attempts": att,
            "google_url": f"https://photos.google.com/photo/{key}" if key else "",
            "mtime": int(mt or 0),
            "undated": p in undated,
        }
        for p, size, s, key, acc, t, err, att, mt in rows
    ]
    return {"total": total, "items": items}


def check_logins():
    """Ask Google for a token with each saved login. Returns {email: "ok" or error}."""
    from gpmc.api import Api

    out = {}
    for email, auth in accounts():
        try:
            Api(auth).bearer_token  # noqa: B018 - property fetches a token
            out[email] = "ok"
            state["login_failed"].discard(email)
        except Exception as e:
            out[email] = f"{type(e).__name__}: {e}"[:300]
    return out


_space_cache = [0.0, None]


def local_space():
    """Room on the disks here, read from the system (any host): where the photos are (MEDIA) and
    where the app keeps its data and downloads (DATA). `same`: both on one disk. Cached 30 s."""
    now = time.monotonic()
    if _space_cache[1] is not None and now - _space_cache[0] < 30:
        return _space_cache[1]

    def disk(path):
        try:
            u = shutil.disk_usage(path)
            return {"total": u.total, "used": u.total - u.free, "free": u.free}
        except OSError:
            return None

    media, data = disk(MEDIA), disk(DATA)
    try:
        same = os.stat(MEDIA).st_dev == os.stat(DATA).st_dev
    except OSError:
        same = False
    _space_cache[:] = [now, {"media": media, "data": data, "same": same}]
    return _space_cache[1]


def _dir_size(root):
    return sum(f.stat().st_size for f in root.rglob("*.webp")) if root.exists() else 0


def health():
    def disk(path):
        try:
            u = shutil.disk_usage(path)
            return {"total": u.total, "used": u.used, "free": u.free}
        except OSError:
            return None

    def size(p):
        return sum(f.stat().st_size for f in Path(p).parent.glob(Path(p).name + "*") if f.is_file())

    last_ok = dict(q("SELECT account, MAX(updated) FROM files WHERE status IN ('uploaded','exists') GROUP BY account"))
    paths = _account_paths()
    accs = [
        {
            "email": email,
            "login_saved": max((f.stat().st_mtime for f in paths.get(email, [])), default=None),
            "last_success": last_ok.get(email),
            "login_rejected": email in state["login_failed"],
        }
        for email, _ in accounts()
    ]
    thumbs = int(cache.remember("thumbs-bytes", 600, lambda: str(_dir_size(THUMBS)).encode()))
    return {
        "version": VERSION,
        "gpmc": _dist_version("gpmc"),
        "python": sys.version.split()[0],
        "stats_engine": STATS_ENGINE,
        "uptime": time.time() - STARTED,
        "disk_data": disk(DATA),
        "disk_media": disk(MEDIA),
        "db_bytes": size(DB_FILE),
        "stats_bytes": size(STATS_FILE),
        "thumbs_bytes": thumbs,
        "cache": cache.info(),
        "accounts": accs,
        "phase": state["phase"],
        "error": state["error"],
        "last_scan": state["last_scan"],
        "checks": {
            "media_readable": os.access(MEDIA, os.R_OK) and bool(list_top_folders()),
            "upload_folder": upload_info()["ready"],
            "turso": STATS_ENGINE.startswith("Turso"),
            "errors_24h": sq("SELECT COUNT(*) FROM logs WHERE level='error' AND time > ?", (time.time() - 86400,))[0][
                0
            ],
        },
    }


def diagnostics(hours=24, limit=3000):
    """Everything needed to look into a problem, as one JSON document: versions, checks, settings
    (secrets left out), what each worker is doing, and the log and activity of the last `hours`.
    Google logins are never included."""
    since = time.time() - hours * 3600
    iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))  # noqa: E731

    def counts(rows):
        return [dict(zip(("state", "error", "count"), r, strict=True)) for r in rows]

    accs = [a for a, _ in accounts()]
    library = {}
    for a in accs:
        rows = _library_rows(
            a,
            "SELECT COUNT(*), SUM(quota_charged_bytes > 0), SUM(is_micro_video = 1) FROM remote_media WHERE "
            + _VISIBLE,
        )
        refreshed = meta_get(f"library_refreshed:{a}")
        library[a] = {
            "read": iso(float(refreshed)) if refreshed else None,
            "items": rows[0][0] if rows else None,
            "counting_against_storage": rows[0][1] if rows else None,
            "motion_photos": rows[0][2] if rows else None,
        }
    return {
        "kind": "gp-sync diagnostics",
        "created": iso(time.time()),
        "hours": hours,
        "version": VERSION,
        "commit": os.environ.get("BUILD_COMMIT", ""),
        "health": health(),
        "status": status_json(),
        "settings": public_settings(),
        "library": library,
        "workers": {
            "free_storage": {
                a: {"error": t.get("error", ""), "working": _reclaim_working(a), "paused": reclaim_paused(a)}
                for a, t in list(reclaim_state.items())
            },
            "dates": {a: dict(t) for a, t in dates_state.items()},
            "copies": {
                f"{s_} -> {d}": {"error": t.get("error", ""), "working": _copy_working(s_, d)}
                for (s_, d), t in list(copy_state.items())
            },
            "sources": dict(sources_state),
            "jobs": background_jobs(),
        },
        "free_storage": counts(q("SELECT state, error, COUNT(*) FROM reclaim GROUP BY state, error")),
        "copies": counts(q("SELECT state, error, COUNT(*) FROM copyjobs GROUP BY state, error")),
        "date_fixes": counts(
            sq("SELECT fix, error, COUNT(*) FROM media_dates WHERE fix IS NOT NULL GROUP BY fix, error")
        ),
        "albums": [dict(zip(("account", "name"), r, strict=True)) for r in q("SELECT account, name FROM albums")],
        "failed_files": [
            {"path": rel(p_), "account": a, "error": e, "attempts": n}
            for p_, a, e, n in q(
                "SELECT path, account, error, attempts FROM files WHERE status='failed' ORDER BY updated DESC LIMIT 200"
            )
        ],
        "logs": [
            {"time": iso(t), "level": lv, "text": x}
            for t, lv, x in sq(
                "SELECT time, level, text FROM logs WHERE time > ? ORDER BY time DESC LIMIT ?", (since, int(limit))
            )
        ],
        "activity": [
            {"time": iso(t), "kind": k, "text": x, "file": n}
            for t, k, x, n in sq(
                "SELECT time, kind, text, name FROM events WHERE time > ? ORDER BY time DESC LIMIT ?",
                (since, int(limit)),
            )
        ],
    }


def status_json():
    st = get_settings()
    counts = dict(q("SELECT status, COUNT(*) FROM files GROUP BY status"))
    gave_up = q("SELECT COUNT(*) FROM files WHERE status='failed' AND attempts>=?", (MAX_ATTEMPTS,))[0][0]
    failed = [
        {"name": rel(p), "error": e, "attempts": a, "time": t}
        for p, e, a, t in q(
            "SELECT path,error,attempts,updated FROM files WHERE status='failed' ORDER BY updated DESC LIMIT 9"
        )
    ]
    up, down = [int(v) for v in net["up"]], [int(v) for v in net["down"]]
    recent = up[-30:]
    accs = [a for a, _ in accounts()]
    return {
        "version": VERSION,
        "host": HOST_NAME,
        "platform": PLATFORM,
        "space": local_space(),
        "auth": bool(PASSWORD),
        # The time zone days and times are shown in: chosen in Settings, or the system default.
        "tz": {
            "name": timezone_name(st),
            "offset_min": st["tz_offset_min"],
            "chosen": st["timezone"],
            "system": system_timezone(st),
        },
        "phase": state["phase"],
        "error": state["error"],
        "done": state["done"],
        "total": state["total"],
        "last_scan": state["last_scan"],
        "interval": INTERVAL // 60,
        "paused": PAUSE_FILE.exists(),
        "all_paused": all_paused(),
        "stats_engine": STATS_ENGINE,
        "account": accs[0] if accs else "",
        "accounts": accs,
        "login_failed": sorted(state["login_failed"]),
        "schedule": {**st["schedule"], "allowed": upload_allowed(st), "next_window": next_window(st)},
        "speed_limit_mbps": st["speed_limit_mbps"],
        "current": state["current"],
        "uploading": list(state.get("uploading", {}).values()),
        "upload_workers": st["upload_workers"],
        "library": state["library"],
        "folders": state["folders"],
        "session": state["session"],
        "max_accounts": MAX_ACCOUNTS,
        "copies": {
            acc: {"done": done or 0, "failed": failed or 0}
            for acc, done, failed in q(
                "SELECT account, SUM(status IN ('uploaded','exists')), SUM(status='failed') "
                "FROM copies GROUP BY account"
            )
        },
        "copies_pending": state.get("copies_pending", 0),
        "jobs": background_jobs(),
        "reclaim": reclaim_summary(),
        "copying": copy_summary(),
        # Files with no date of their own, and how many are queued to be fixed in Google.
        "dates": dict(
            zip(
                ("undated", "waiting"),
                sq(
                    """SELECT COALESCE(SUM(inside=0), 0), COALESCE(SUM(fix IN ('waiting','in_google')), 0)
                       FROM media_dates"""
                )[0],
                strict=True,
            )
        ),
        "undated_asked": st["undated"]["asked"],
        "counts": {
            "uploaded": counts.get("uploaded", 0),
            "exists": counts.get("exists", 0),
            "failed": counts.get("failed", 0),
            "gave_up": gave_up,
        },
        "net": {
            "up": up,
            "down": down,
            "up_now": up[-1] if up else 0,
            "down_now": down[-1] if down else 0,
            "up_avg": sum(recent) / len(recent) if recent else 0,
            "sent": net["sent"],
            "received": net["received"],
        },
        "failed": failed,
        "pending": state["pending"][:8],
        "settle_seconds": SETTLE,
        "log": [{"time": t, "kind": k, "text": m, "name": n} for t, k, m, n in list(log_lines)[-12:]][::-1],
        "resources": resources_json(),
        "now": time.time(),
    }


def background_jobs():
    """What runs in the background right now, across accounts: library reads, freeing storage,
    fixing dates and copies. Each account (and copy pair) works on its own, side by side."""
    jobs = [{"kind": "library", "account": a} for a in sorted(_refreshing)]

    def add(kind, cur, **who):
        if cur:
            name = cur.get("name") or cur.get("key") or ""
            name = rel(name) if name.startswith("/") else name
            step = {"step": cur.get("step", ""), "done": cur.get("done", 0), "total": cur.get("total", 0)}
            jobs.append({"kind": kind, **who, "name": name, **step})

    for account in list(reclaim_state):
        for cur in _reclaim_working(account):
            add("storage", cur, account=account)
    for account, t in list(dates_state.items()):
        add("dates", t.get("current"), account=account)
    for src, dst in list(copy_state):
        for cur in _copy_working(src, dst):
            add("copy", cur, account=src, to=dst)
    return jobs


_status_memo = {"at": 0.0, "key": None, "body": b""}


def status_body():
    """status_json() as bytes, shared by every open dashboard for up to a second."""
    key = (cache.generation(), changes[0])
    now = time.monotonic()
    m = _status_memo
    if m["body"] and m["key"] == key and now - m["at"] < 1:
        return m["body"]
    body = json.dumps(status_json()).encode()
    m.update(at=now, key=key, body=body)
    return body


# ---------------------------------------------------------------- free up Google storage
# Photos and videos that count against the Google storage quota (for example ones the phone's
# Google Photos app backed up) are downloaded and checked against Google's own fingerprint. Only
# then is Google's copy deleted (Trash, then out of the Trash) and the original uploaded again the
# way all GP Sync uploads go, which doesn't count, and checked. One item at a time.

RECLAIM_DIR = DATA / "reclaim"
GPMC_HOME = Path.home() / ".gpmc"  # where gpmc keeps its copy of each account's library list
RECLAIM_KINDS = {1: "photo", 2: "video"}
reclaim_wake = threading.Event()
# Progress and the last error, per account: every account has a worker of its own, so one account's
# long library read or download never holds up another's.
reclaim_state = collections.defaultdict(lambda: {"current": None, "error": "", "jobs": {}})
_refreshing = set()  # accounts whose library list is being read


def reclaim_blocked(account):
    """True after Google counted one of our re-uploads: then nothing more is moved."""
    return bool(q("SELECT 1 FROM reclaim WHERE account=? AND state='counts' LIMIT 1", (account,)))


def _b64_hash(digest):
    """Google's dedup key: the file's SHA-1 in URL-safe base64."""
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _library_db(account):
    return GPMC_HOME / account / "storage.db"


def _library_rows(account, sql, args=()):
    path = _library_db(account)
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    try:
        return conn.execute(sql, args).fetchall()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def _seconds(ts):
    ts = int(ts or 0)
    return ts / 1000 if ts > 10**11 else ts  # Google sends milliseconds


def storage_report(account, limit=500, kind="all"):
    """What counts against Google storage in an account, biggest first, with progress."""
    only = kind  # the kind to list; a loop below reuses the name `kind` for each row's type
    refreshed = meta_get(f"library_refreshed:{account}")
    out = {
        "account": account,
        "refreshed": float(refreshed) if refreshed else None,
        "refreshing": account in _refreshing,
        "error": reclaim_state[account]["error"],
        "blocked": reclaim_blocked(account),
        "current": next(iter(_reclaim_working(account)), None),
        "working": _reclaim_working(account),
        "paused": reclaim_paused(account),
        "check": check_info(account),
        "prefs": get_settings()["reclaim"],
        "summary": storage_summary(),
        "free_space": shutil.disk_usage(DATA).free if DATA.exists() else 0,
    }
    rows = _library_rows(
        account,
        # The same photo appears once as the library item and again for each album it is in, all
        # with the same dedup_key and the same quota_charged_bytes. Google charges it once, so each
        # item is counted once here too (the canonical copy, which has no album, comes first).
        """SELECT media_key, dedup_key, file_name, type, size_bytes, quota_charged_bytes, utc_timestamp
           FROM remote_media WHERE origin = 'self' AND COALESCE(trash_timestamp, 0) = 0
           AND COALESCE(is_locked, 0) = 0 ORDER BY is_canonical DESC, content_version""",
    )
    jobs = {
        r[0]: r
        for r in q(
            """SELECT media_key, dedup_key, name, kind, size, quota, taken, state, error, new_key, updated
               FROM reclaim WHERE account=?""",
            (account,),
        )
    }
    ours = {
        _b64_hash(bytes.fromhex(h)) for (h,) in q("SELECT sha1 FROM files WHERE sha1 != '' AND account=?", (account,))
    }
    items, counted, counted_bytes, total, ours_counted, ours_seen = [], 0, 0, 0, 0, 0
    kinds = {k: {"count": 0, "bytes": 0} for k in RECLAIM_TYPES}
    seen = set()  # dedup_keys already counted: each photo once, not once per album
    for key, dedup, name, kind, size, quota, taken in rows or []:
        if dedup in seen:
            continue
        seen.add(dedup)
        total += 1
        if dedup in ours:
            ours_seen += 1
            ours_counted += bool(quota)
        if quota:
            counted += 1
            counted_bytes += quota
            k = kinds[RECLAIM_KINDS.get(kind, "photo")]
            k["count"] += 1
            k["bytes"] += quota
            items.append((key, dedup, name, RECLAIM_KINDS.get(kind, "photo"), size, quota, _seconds(taken)))
    listed = {i[0] for i in items}
    for key, dedup, name, kind, size, quota, taken, *_ in jobs.values():
        if key not in listed:  # deleted in Google already, or done
            items.append((key, dedup, name, kind, size, quota, taken))
    rank = {"queued": 0, "downloaded": 0, "in_google": 0, "checking": 0, "failed": 1, "counts": 1,
            "done": 3, "counted": 3}  # fmt: skip
    # Under way first (so they are always on screen), then what went wrong, then the rest, biggest first.
    items.sort(key=lambda i: (rank.get(jobs[i[0]][7], 2) if i[0] in jobs else 2, -(i[5] or 0), i[2] or ""))

    def job(key):
        j = jobs.get(key)
        return {"state": j[7], "error": j[8] or "", "new_key": j[9] or "", "updated": j[10]} if j else {"state": ""}

    done = q("SELECT COUNT(*), SUM(quota) FROM reclaim WHERE account=? AND state='done'", (account,))[0]
    sql = "SELECT COUNT(*) FROM files WHERE account=? AND status IN ('uploaded','exists')"
    backed_up = q(sql, (account,))[0][0]
    left = q(
        """SELECT COUNT(*), SUM(quota) FROM reclaim WHERE account=?
           AND state IN ('queued', 'downloaded', 'in_google', 'checking')""",
        (account,),
    )[0]
    # Speed over the last hour: storage freed between the first start and the last finish.
    recent = q(
        """SELECT SUM(quota), MIN(started), MAX(finished) FROM reclaim WHERE account=? AND state='done'
           AND started IS NOT NULL AND finished > ?""",
        (account, time.time() - 3600),
    )[0]
    span = (recent[2] - recent[1]) if recent[1] and recent[2] else 0
    rate = (recent[0] or 0) / span if span > 0 else 0
    # Only the chosen kind's items are listed, so "Videos" still fills the list even when the biggest
    # items overall are photos. The totals above stay for all kinds.
    shown_items = [i for i in items if only in ("all", i[3])][:limit]
    return {
        **out,
        "listed": rows is not None,
        "total": total,
        "counted": counted,
        "counted_bytes": counted_bytes,
        "kinds": kinds,
        "ours": {
            "seen": ours_seen,
            "counted": ours_counted,
            # Files GP Sync backed up to this account (0: its folders go to other accounts).
            "uploaded": backed_up,
        },
        "freed": {"count": done[0], "bytes": done[1] or 0},
        # Still to free, and how fast it went in the last hour (bytes of storage freed per second).
        "left": {"count": left[0], "bytes": left[1] or 0, "rate": rate},
        "items": [
            {
                "key": key,
                "name": name,
                "kind": kind,
                "size": size,
                "quota": quota,
                "taken": taken,
                "google_url": f"https://photos.google.com/photo/{key}",
                **job(key),
            }
            for key, _dedup, name, kind, size, quota, taken in shown_items
        ],
    }


# Items Free storage may move: the account's own, counting against storage, not in the Trash or
# the Locked folder.
_MOVABLE = """quota_charged_bytes > 0 AND origin = 'self' AND COALESCE(trash_timestamp, 0) = 0
              AND COALESCE(is_locked, 0) = 0"""
RECLAIM_TYPES = {"photo": 1, "video": 2}


def reclaim_queue_all(account, kind="all", keep=False):
    """Queue everything that counts against storage: only photos, only videos, or both."""
    if kind not in ("all", *RECLAIM_TYPES):
        raise ValueError("kind must be photo, video or all")
    where, args = _MOVABLE, ()
    if kind != "all":
        where += " AND type = ?"
        args = (RECLAIM_TYPES[kind],)
    # One media_key per item (the canonical copy): albums repeat the same photo, and freeing works
    # on the whole item anyway. SQLite returns the bare column from the row holding MAX(is_canonical).
    keys = [
        r[0]
        for r in _library_rows(
            account,
            f"SELECT media_key, MAX(is_canonical) FROM remote_media WHERE {where} GROUP BY dedup_key",
            args,
        )
        or []
    ]
    busy = {k for (k,) in q("SELECT media_key FROM reclaim WHERE account=? AND state NOT IN ('failed', 'cancelled')",
                            (account,))}  # fmt: skip
    return reclaim_queue(account, [k for k in keys if k not in busy], keep)


def reclaim_queue(account, keys, keep=False):
    """Ask for items to be downloaded (biggest first, one at a time)."""
    wanted = set(keys)
    rows = _library_rows(
        account,
        f"""SELECT media_key, dedup_key, file_name, type, size_bytes, quota_charged_bytes, utc_timestamp,
                  is_favorite, is_archived, caption FROM remote_media WHERE {_MOVABLE}""",
    )
    n = 0
    for key, dedup, name, kind, size, quota, taken, fav, arch, cap in rows or []:
        if key not in wanted:
            continue
        q(
            """INSERT INTO reclaim (account, media_key, dedup_key, name, kind, size, quota, taken, favorite,
                                    archived, caption, state, updated, keep)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,'queued',?,?)
               ON CONFLICT(account, media_key) DO UPDATE SET state='queued', error='', keep=excluded.keep
               WHERE state IN ('failed', 'cancelled')""",
            (account, key, dedup, name, RECLAIM_KINDS.get(kind, "photo"), size, quota, _seconds(taken),
             int(bool(fav)), int(bool(arch)), "" if _junk_caption(cap) else cap or "", time.time(), int(bool(keep))),
        )  # fmt: skip
        n += 1
    reclaim_wake.set()
    return {"queued": n}


def reclaim_cancel(account, keys):
    """Forget items that are waiting (their downloaded copy is removed). Never touches Google."""
    n = 0
    for key in keys:
        rows = q(
            """SELECT local FROM reclaim WHERE account=? AND media_key=?
               AND state IN ('queued', 'downloaded', 'in_google', 'failed')""",
            (account, key),
        )
        if not rows:
            continue
        if rows[0][0]:
            shutil.rmtree(Path(rows[0][0]).parent, ignore_errors=True)
        q("DELETE FROM reclaim WHERE account=? AND media_key=?", (account, key))
        n += 1
    return {"cancelled": n}


_clients = {}  # account -> (login, gpmc Client): one login per account, renewed by gpmc when due
_clients_lock = threading.Lock()


def _reclaim_client(account):
    from gpmc import Client

    auth = dict(accounts()).get(account)
    if not auth:
        raise RuntimeError(f"{account} is not connected")
    with _clients_lock:
        known = _clients.get(account)
        if not known or known[0] != auth:  # new, or the login was replaced
            known = _clients[account] = (auth, Client(auth_data=auth, log_level="WARNING"))
        return known[1]


def _download_urls(response):
    """Download links in a PhotosPrepareDownload answer, Google's original first."""
    found = []

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, (*path, str(k)))
        elif isinstance(node, list):
            for v in node:
                walk(v, path)
        elif isinstance(node, (str, bytes)):
            text = node.decode(errors="ignore") if isinstance(node, bytes) else node
            host = urllib.parse.urlparse(text).hostname or ""
            if text.startswith("https://") and (
                host.endswith(".googleusercontent.com") or host.endswith(".google.com")
            ):
                found.append((0 if path[-4:] == ("1", "5", "2", "6") else 1, text))

    walk(response, ())
    return [u for _, u in sorted(found, key=lambda x: x[0])][:3]


_library_locks = {}
_library_locks_guard = threading.Lock()


def _library_lock(account):
    with _library_locks_guard:
        return _library_locks.setdefault(account, threading.Lock())


def update_library(client, account):
    """Bring this server's copy of the account's library list up to date. One read per account at a
    time: its workers (free storage, fix dates, copies) would otherwise write the same list at once."""
    with _library_lock(account):
        client.update_cache(show_progress=False)
        meta_set(f"library_refreshed:{account}", str(time.time()))


_disk_lock = threading.Lock()
_disk_reserved = [0]  # bytes promised to downloads in progress (several accounts download at once)


@contextlib.contextmanager
def _disk_space(size):
    """Reserve room for one download, counting the ones already under way, or refuse."""
    need = int((size or 0) * 1.1) + 512 * 1024**2
    with _disk_lock:
        if shutil.disk_usage(DATA).free - _disk_reserved[0] < need:
            raise RuntimeError("Not enough free disk space for this file")
        _disk_reserved[0] += need
    try:
        yield
    finally:
        with _disk_lock:
            _disk_reserved[0] -= need


def _download(client, account, key, dedup, name, size, taken, folder=None, track=None):
    """Download one item and prove it is Google's own copy (same SHA-1). Returns (path, the file's
    dedup key).

    The SHA-1 must be the item's dedup key, or another checksum Google lists for the same item: a
    few videos download as a copy Google changed slightly (same size, an edited date inside), and
    Google reports that copy's checksum too. `track` is the dict whose "current" shows progress."""
    track = reclaim_state[account] if track is None else track
    with _disk_space(size):
        found = _from_local(key, dedup, name, size, taken, folder or RECLAIM_DIR / account / key, track)
        if found:
            return found
        return _fetch(client, account, key, dedup, name, size, taken, folder, track)


def _local_copies(dedup):
    """Files on this server with the item's exact SHA-1 (its dedup key), from GP Sync's own records."""
    try:
        sha1 = _digest(dedup).hex()
    except (ValueError, TypeError):
        return []
    return [p for (p,) in q("SELECT path FROM files WHERE sha1=? ORDER BY updated DESC", (sha1,))]


def _from_local(key, dedup, name, size, taken, folder, track):
    """Use a file already on this server instead of downloading it from Google: copied into the work
    folder (never moved, so the original stays exactly as it was) and checked again while copying.
    Returns (path, dedup key), or None when no file here matches byte for byte."""
    for source in _local_copies(dedup):
        try:
            if os.path.getsize(source) != size and size:
                continue  # changed since it was fingerprinted
        except OSError:
            continue
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / (clean_name(name) or f"{key}.bin")
        tmp = target.with_suffix(target.suffix + ".part")
        digest = hashlib.sha1()
        done = 0
        try:
            stop = track.get("stop")
            with open(source, "rb") as src, open(tmp, "wb") as f:
                while block := src.read(1024 * 1024):
                    if stop and stop():
                        raise Paused
                    f.write(block)
                    digest.update(block)
                    done += len(block)
                    track["current"] = {**(track.get("current") or {}), "key": key, "step": "local",
                                        "done": done, "total": size}  # fmt: skip
        except OSError:
            tmp.unlink(missing_ok=True)
            continue
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        if _b64_hash(digest.digest()) != dedup:
            tmp.unlink(missing_ok=True)
            continue
        tmp.replace(target)
        if taken:
            os.utime(target, (taken, taken))  # only the copy's time changes, never the original's
        track["from_local"] = True
        log(f"Used the copy of {name} already on this server instead of downloading it", "local")
        return target, dedup
    return None


def _item_hashes(response, dedup):
    """The dedup key, then any other checksum Google lists for the item in a download answer."""
    found = [dedup]
    with contextlib.suppress(AttributeError, KeyError, TypeError):  # another shape: only the dedup key
        for h in (response["1"]["2"].get("13") or {}).values():
            if isinstance(h, (bytes, bytearray)) and len(h) == 20 and _b64_hash(h) not in found:
                found.append(_b64_hash(h))
    return found


class Paused(Exception):
    """The user paused this work: a download in progress stops, and the item stays queued."""


def _stop_check(paused):
    """`paused()` asked at most once a second: called for every downloaded block."""
    last = [0.0, False]

    def check():
        now = time.monotonic()
        if now - last[0] >= 1:
            last[:] = [now, paused()]
        return last[1]

    return check


def _fetch(client, account, key, dedup, name, size, taken, folder, track):
    import requests

    folder = folder or RECLAIM_DIR / account / key
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (clean_name(name) or f"{key}.bin")
    tmp = target.with_suffix(target.suffix + ".part")
    headers = {"Authorization": f"Bearer {client.api.bearer_token}", "User-Agent": client.api.user_agent}
    response = client.api.get_download_urls(key)
    urls = _download_urls(response)
    if not urls:
        raise RuntimeError("Google gave no download link")
    valid = _item_hashes(response, dedup)
    for url in urls:
        digest = hashlib.sha1()
        done = 0
        try:
            with requests.get(url, headers=headers, stream=True, timeout=60) as r, open(tmp, "wb") as f:
                r.raise_for_status()
                stop = track.get("stop")
                for block in r.iter_content(1024 * 1024):
                    if stop and stop():
                        raise Paused
                    f.write(block)
                    digest.update(block)
                    done += len(block)
                    track["current"] = {**(track.get("current") or {}), "key": key, "step": "downloading",
                                        "done": done, "total": size}  # fmt: skip
        except BaseException:
            tmp.unlink(missing_ok=True)  # a broken download would otherwise fill the disk
            raise
        got = _b64_hash(digest.digest())
        if got in valid:
            tmp.replace(target)
            if taken:
                os.utime(target, (taken, taken))  # the upload uses the file time as its date
            return target, got
    tmp.unlink(missing_ok=True)
    raise RuntimeError("The download is not exactly Google's original, so it was not used")


def _set_reclaim(account, key, state_, error="", **cols):
    sets = ", ".join(f"{c}=?" for c in cols)
    q(
        f"UPDATE reclaim SET state=?, error=?, updated=?{', ' + sets if sets else ''} WHERE account=? AND media_key=?",
        (state_, error, time.time(), *cols.values(), account, key),
    )
    notify_change()


def _digest(dedup):
    return base64.urlsafe_b64decode(dedup + "=" * (-len(dedup) % 4))


def all_paused():
    """Every job is paused: uploads, copies (and Keep in sync), and Free storage and Fix dates in
    every account. Each job keeps its own pause; Overview's Pause everything sets them all."""
    accs = [a for a, _ in accounts()]
    return (
        PAUSE_FILE.exists()
        and meta_get("copy_paused") == "1"
        and all(reclaim_paused(a) and dates_paused(a) for a in accs)
    )


def pause_all(paused):
    """Overview's Pause everything / Resume everything: sets every job's own pause."""
    flag = "1" if paused else "0"
    if paused:
        PAUSE_FILE.touch()
        if state["phase"] != "uploading":
            state["phase"] = "paused"
    elif PAUSE_FILE.exists():
        PAUSE_FILE.unlink()
    meta_set("copy_paused", flag)
    for a, _ in accounts():
        meta_set(f"reclaim_paused:{a}", flag)
        meta_set(f"dates_paused:{a}", flag)
    log("Paused everything: items under way finish their step" if paused else "Resumed everything",
        "pause" if paused else "resume")  # fmt: skip
    for ev in (wake, reclaim_wake, copy_wake, mirror_wake):
        ev.set()
    notify_change()


def split_pause():
    """1.5.8 paused every job with the uploads pause; since 1.5.9 each job has its own again. Keep
    what was paused then paused (once)."""
    if meta_get("pause_split"):
        return
    if PAUSE_FILE.exists():
        meta_set("copy_paused", "1")
        for a, _ in accounts():
            meta_set(f"reclaim_paused:{a}", "1")
            meta_set(f"dates_paused:{a}", "1")
    meta_set("pause_split", "1")


def reclaim_paused(account):
    """The user paused Free storage for this account: items under way finish their step, nothing new starts."""
    return meta_get(f"reclaim_paused:{account}") == "1"


# Items being worked on, several per account at once (Settings: reclaim.workers). Each item's steps
# are saved as they happen, so a restart or an update resumes where it stopped.
_reclaim_pool = concurrent.futures.ThreadPoolExecutor(
    RECLAIM_MAX_WORKERS * 5, thread_name_prefix="free storage"
)  # 4 per account, several accounts
_reclaim_jobs = {}  # (account, media key) -> Future
_reclaim_jobs_lock = threading.Lock()


def _reclaim_working(account):
    """Progress of every item being worked on in an account, in the order they started."""
    with _reclaim_jobs_lock:
        return [dict(t["current"]) for t in reclaim_state[account]["jobs"].values() if t.get("current")]


def _reclaim_item(account, row, make_client):
    """Move one item a step on, on a worker thread: download it, or replace it in Google.
    Returns True if it did something."""
    key, dedup, name, size, taken, st_, local, fav, arch, cap, local_hash = row
    track = {"current": None, "stop": _stop_check(lambda: reclaim_paused(account))}
    with _reclaim_jobs_lock:
        reclaim_state[account]["jobs"][key] = track
    try:
        client = make_client(account)
        if st_ == "queued":
            track["current"] = {"key": key, "step": "downloading", "done": 0, "total": size, "name": name}
            q("UPDATE reclaim SET started=COALESCE(started, ?) WHERE account=? AND media_key=?",
              (time.time(), account, key))  # fmt: skip
            path, got = _download(client, account, key, dedup, name, size, taken, track=track)
            _set_reclaim(account, key, "downloaded", local=str(path), local_hash="" if got == dedup else got,
                         downloaded_at=time.time())  # fmt: skip
            reclaim_state[account]["error"] = ""
            if not track.get("from_local"):
                edited = " (Google's edited copy)" if got != dedup else ""
                log(f"Downloaded {name} from Google ({fmt_bytes(size)}){edited} to free its storage", "downloaded")
            return True
        have = local_hash or dedup  # the file here: Google's original, or the copy Google serves now
        hashes = list(dict.fromkeys([dedup, have]))
        in_google = any(client.api.find_remote_media_by_hash(_digest(h)) for h in hashes)
        if not _local_copy_ok(local, have):
            if in_google:  # the copy here is gone or damaged: download it again
                _set_reclaim(account, key, "queued", local="", local_hash="")
            else:
                _set_reclaim(account, key, "failed", "Google no longer has it and the copy here is gone")
            return True
        if in_google:
            # The copy here was just checked again, byte for byte: only now is Google's deleted.
            track["current"] = {"key": key, "step": "deleting", "done": 0, "total": size, "name": name}
            if not _delete_in_google(client, dedup, *hashes):
                _set_reclaim(account, key, "in_google", "Google hasn't deleted it yet: trying again soon")
                return True
            log(f"Deleted {name} in Google Photos ({account}) to upload it again for free", "deleted")
        track["current"] = {"key": key, "step": "uploading", "done": 0, "total": size, "name": name}
        result = _reupload(client, Path(local), have, size, key, track, when=taken)
        if result is None:
            _set_reclaim(account, key, "in_google", "Google still has it: trying again soon")
            return True
        _restore_details(client, have, fav, arch, cap)
        _set_reclaim(account, key, "checking", new_key=result, checks=0, uploaded_at=time.time())
        check_lanes.wake([account])
        return True
    except Paused:  # stopped mid-download: nothing changed in Google, it waits in the queue
        log(f"Paused: stopped downloading {name}; it stays queued", "pause")
        return False
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"[:300]
        print(f"Free storage: {name}: {msg}", file=sys.stderr, flush=True)
        if st_ == "queued":
            if outage := outage_kind(e):  # no internet or Google is down: try again in a minute
                reclaim_state[account]["error"] = outage
                reclaim_state[account]["outage_until"] = time.time() + 60
                return False
            if _is_missing(e):  # gone from Google (stale key, or a duplicate already freed): drop it
                q("DELETE FROM reclaim WHERE account=? AND media_key=?", (account, key))
                log(f"{name} is no longer in Google Photos, so it was skipped", "info")
                return True
            _set_reclaim(account, key, "failed", msg)
            return True
        _set_reclaim(account, key, st_, msg)  # tried again in 2 minutes
        return False
    finally:
        track["current"] = None
        with _reclaim_jobs_lock:
            reclaim_state[account]["jobs"].pop(key, None)


def _reclaim_finished(account, key):
    with _reclaim_jobs_lock:
        _reclaim_jobs.pop((account, key), None)
    notify_change()
    with contextlib.suppress(NameError):  # the account's lane can start the next item now
        account_lanes.nudge(account)


def reclaim_step(make_client=None, account=None, wait=True):
    """Start the next items (for one account, or all), up to Settings' reclaim.workers at once per
    account. `wait`: return once they finish (tests); the background lanes don't wait, and a
    finished item wakes its lane so the next one starts. Returns True if there was something to do."""
    make_client = make_client or _reclaim_client
    now = time.time()
    started = False
    rows = q(
        """SELECT account, media_key, dedup_key, name, size, taken, state, local, favorite, archived, caption,
                  local_hash FROM reclaim WHERE state IN ('queued', 'downloaded', 'in_google')
                  AND (? IS NULL OR account=?)
           ORDER BY state = 'queued', quota DESC""",
        (account, account),
    )
    workers = get_settings()["reclaim"]["workers"]
    per = {}  # account -> what holds it back, worked out once per step
    for acc, key, dedup, name, size, taken, st_, local, fav, arch, cap, local_hash in rows:
        if acc not in per:
            with _reclaim_jobs_lock:
                running = sum(1 for a, _ in _reclaim_jobs if a == acc)
            held = reclaim_paused(acc)
            per[acc] = {"slots": 0 if held else workers - running, "blocked": None, "ours": None}
        p = per[acc]
        if p["slots"] <= 0:
            continue
        with _reclaim_jobs_lock:
            if (acc, key) in _reclaim_jobs:
                continue
        if st_ == "queued":
            if now < reclaim_state[acc].get("outage_until", 0):
                continue  # Google or the internet was down a moment ago
        else:
            if p["blocked"] is None:
                p["blocked"] = reclaim_blocked(acc)
            if p["blocked"] or now - _reclaim_checked.get(key, 0) < 100:
                continue  # tried less than 2 minutes ago
            _reclaim_checked[key] = now
            if p["ours"] is None:
                p["ours"] = _own_uploads_count(acc)
            if p["ours"]:
                why = "GP Sync's own uploads count against storage in this account: nothing deleted"
                _set_reclaim(acc, key, st_, why)
                continue
        row = (key, dedup, name, size, taken, st_, local, fav, arch, cap, local_hash)
        with _reclaim_jobs_lock:
            fut = _reclaim_jobs[(acc, key)] = _reclaim_pool.submit(_reclaim_item, acc, row, make_client)
        fut.add_done_callback(lambda _f, a=acc, k=key: _reclaim_finished(a, k))
        p["slots"] -= 1
        started = True
    with _reclaim_jobs_lock:
        mine = [f for (a, _), f in _reclaim_jobs.items() if account is None or a == account]
    if not wait:
        return started or bool(mine)
    done, _ = concurrent.futures.wait(mine)
    return any(f.result() for f in done if not f.exception())


_reclaim_checked = {}  # media key -> when Google was last asked about it


def busy_account(kind="storage"):
    """The account a page opens on when none is named: one with work under way, else the main one."""
    accs = [a for a, _ in accounts()]
    if kind == "storage":
        busy = [a for a, v in reclaim_summary().items() if v["queued"] and not v["paused"]]
        busy.sort(key=lambda a: -len(_reclaim_working(a)))
    else:
        busy = [a for a, t in dates_state.items() if t.get("current")]
    return next((a for a in busy if a in accs), accs[0] if accs else "")


def reclaim_summary():
    """Free storage in every account, for the status: what waits, what runs, what was freed."""
    out = {}
    for acc, st_, n, b in q("SELECT account, state, COUNT(*), SUM(quota) FROM reclaim GROUP BY account, state"):
        a = out.setdefault(acc, {"queued": 0, "working": 0, "done": 0, "freed": 0, "failed": 0, "paused": False})
        if st_ in ("queued", "downloaded", "in_google", "checking"):
            a["queued"] += n
        elif st_ == "done":
            a["done"], a["freed"] = n, b or 0
        elif st_ in ("failed", "counts"):
            a["failed"] += n
    for acc, a in out.items():
        a["working"] = len(_reclaim_working(acc))
        a["paused"] = reclaim_paused(acc)
    return out


_counted_cache = {}  # account -> (library file times, bytes counted against storage)


def counted_bytes(account):
    """Bytes that count against Google storage in an account (each item once, not once per album),
    from its library list here. None when the library hasn't been read. Worked out again only when
    the library list changes."""
    path = _library_db(account)
    try:
        files = tuple(
            (st.st_mtime_ns, st.st_size) for f in (path, Path(f"{path}-wal")) if f.exists() for st in [os.stat(f)]
        )
    except OSError:
        files = ()
    if not files:
        return None
    stamp = (files, meta_get(f"library_refreshed:{account}"))  # every library read sets the second
    cached = _counted_cache.get(account)
    if cached and cached[0] == stamp:
        return cached[1]
    rows = _library_rows(
        account,
        """SELECT COALESCE(SUM(q), 0) FROM (SELECT MAX(quota_charged_bytes) AS q FROM remote_media
           WHERE origin = 'self' AND COALESCE(trash_timestamp, 0) = 0 AND COALESCE(is_locked, 0) = 0
           GROUP BY dedup_key)""",
    )
    value = None if rows is None else int(rows[0][0] or 0)
    _counted_cache[account] = (stamp, value)
    return value


def storage_summary():
    """Free storage for every connected account, for the account list: what still counts against
    storage, and how freeing is going."""
    out = reclaim_summary()
    blank = {"queued": 0, "working": 0, "done": 0, "freed": 0, "failed": 0, "paused": False}
    for acc, _ in accounts():
        out.setdefault(acc, {**blank, "paused": reclaim_paused(acc)})["counted"] = counted_bytes(acc)
    return out


def _active_seconds(spans):
    """Time something was running, counting overlaps once: several items move at once, so adding up
    each item's own time would count the same minutes several times."""
    total, end = 0.0, None
    for start, stop in sorted(spans):
        if end is None or start > end:
            total += stop - start
            end = stop
        elif stop > end:
            total += stop - end
            end = stop
    return total


def freed_report(account, limit=1000):
    """What Free storage freed in an account: totals, time, per day, and each item, newest first."""
    tz = get_settings()["tz_offset_min"] * 60
    rows = q(
        """SELECT media_key, new_key, name, kind, quota, size, taken, started, COALESCE(finished, updated),
                  downloaded_at, uploaded_at
           FROM reclaim WHERE account=? AND state='done' ORDER BY COALESCE(finished, updated) DESC""",
        (account,),
    )
    kept = dict(q("SELECT media_key, id FROM downloads WHERE account=?", (account,)))
    kinds = {k: {"count": 0, "bytes": 0} for k in RECLAIM_TYPES}
    days, spans, durations, phases = {}, [], [], {"download": [], "upload": [], "check": []}
    day_spans = collections.defaultdict(list)
    items = []
    for old, new, name, kind, quota, size, taken, started, finished, got, sent in rows:
        k = kinds.get(kind) or kinds["photo"]
        k["count"] += 1
        k["bytes"] += quota or 0
        took = finished - started if started and finished and finished >= started else None
        if took is not None:
            spans.append((started, finished))
            durations.append((took, name, quota or 0))
            if started < (got or 0) <= (sent or 0) <= finished:  # each step, for items timed from 1.5.5
                phases["download"].append(got - started)
                phases["upload"].append(sent - got)
                phases["check"].append(finished - sent)
        if finished:
            day = time.strftime("%Y-%m-%d", time.gmtime(finished + tz))
            d = days.setdefault(day, {"day": day, "count": 0, "bytes": 0, "active": 0})
            d["count"] += 1
            d["bytes"] += quota or 0
            if took is not None:
                day_spans[day].append((started, finished))
        if len(items) < limit:
            items.append({"key": new or old, "old_key": old, "name": name, "kind": kind, "bytes": quota or 0,
                          "size": size or 0, "taken": taken, "finished": finished, "seconds": took,
                          "download": kept.get(new or ""),
                          "google_url": f"https://photos.google.com/photo/{new}" if new else ""})  # fmt: skip
    for day, sp in day_spans.items():
        days[day]["active"] = _active_seconds(sp)
    total = sum(k["bytes"] for k in kinds.values())
    active = _active_seconds(spans)
    timed_bytes = sum(b for _, _, b in durations)
    durations.sort()

    def item(d):
        return {"seconds": d[0], "name": d[1], "bytes": d[2]}

    return {
        "account": account,
        "count": len(rows),
        "bytes": total,
        "kinds": kinds,
        "seconds": sum(d[0] for d in durations),  # each item's own time, added up
        "active": active,  # time Free storage was actually running (overlaps counted once)
        "timed": len(durations),
        "timing": {
            "average": sum(d[0] for d in durations) / len(durations) if durations else 0,
            "median": durations[len(durations) // 2][0] if durations else 0,
            "fastest": [item(d) for d in durations[:5]],
            "slowest": [item(d) for d in durations[::-1][:5]],
            "rate": timed_bytes / active if active else 0,  # storage freed per second of running time
            "per_hour": len(durations) / (active / 3600) if active else 0,
            "phases": {k: (sum(v) / len(v) if v else None) for k, v in phases.items()},
            "phased": len(phases["download"]),
        },
        "first": rows[-1][8] if rows else None,
        "last": rows[0][8] if rows else None,
        "days": sorted(days.values(), key=lambda d: d["day"]),
        "items": items,
    }


def _junk_caption(cap):
    """gpmc lists an item without a caption as "{}" (an empty field): that is not a caption."""
    return isinstance(cap, str) and cap.startswith("{") and cap.endswith("}")


def caption_repair_step(account, make_client=None):
    """Free storage and account copies used to set that "{}" as the caption of items they uploaded
    again. Clear it once on each of those items. Returns True if it did something."""
    make_client = make_client or _reclaim_client
    todo = [
        ("reclaim", key, have)
        for key, have in q(
            """SELECT media_key, COALESCE(NULLIF(local_hash, ''), dedup_key) FROM reclaim
               WHERE account=? AND state='done' AND caption LIKE '{%}' LIMIT 50""",
            (account,),
        )
    ] + [
        ("copyjobs", key, dedup)
        for key, dedup in q(
            """SELECT media_key, dedup_key FROM copyjobs
               WHERE dst=? AND state IN ('copied', 'done') AND caption LIKE '{%}' LIMIT 50""",
            (account,),
        )
    ]
    if not todo:
        return False
    client = make_client(account)
    for table, key, dedup in todo:
        try:
            client.api.set_item_caption(dedup_key=dedup, caption="")
        except Exception as e:
            if outage_kind(e):
                return False
            print(f"Captions: couldn't clear {key}: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        who = "account" if table == "reclaim" else "dst"
        q(f"UPDATE {table} SET caption='' WHERE {who}=? AND media_key=?", (account, key))
    log(f'Cleared the stray "{{}}" caption on {len(todo)} re-uploaded item(s) ({account})', "info")
    return True


def _reupload(client, path, dedup, size, key, track, when=None):
    """Upload the downloaded original the free way, dated `when` (unix seconds: the date Google
    showed for it, so items without a date inside keep their place in the timeline). Returns the new
    media key, or None if Google still has the old one (then nothing is uploaded)."""
    from rich.progress import Progress

    phases = []

    def progress(ev):
        phases.append(ev.get("phase"))
        if ev.get("phase") == "uploading":
            track["current"] = {
                **(track.get("current") or {}),
                "key": key,
                "step": "uploading",
                "done": ev.get("bytes_completed") or 0,
                "total": size,
            }

    digest = base64.urlsafe_b64decode(dedup + "=" * (-len(dedup) % 4))
    with Progress(disable=True) as bar, upload_date(client, when):
        result = client._upload_file(
            path, digest, bar, force_upload=False, use_quota=False, saver=False, progress_callback=progress
        )
    if "skipped" in phases:
        return None
    return next(iter(result.values()), "") or None


def _local_copy_ok(local, dedup):
    """The downloaded copy is still here and still exactly Google's original."""
    try:
        return bool(local) and Path(local).is_file() and _b64_hash(media.sha1_file(local)) == dedup
    except OSError:
        return False


def _delete_in_google(client, dedup, *also):
    """Delete one item in Google Photos for good: into Google's Trash, then out of it (an item in the
    Trash would block the new upload). Only called once the original is safe here, checked byte for
    byte. Returns True once Google no longer has it, by its dedup key or the other checksums `also`."""
    digests = [_digest(d) for d in dict.fromkeys([dedup, *also]) if d]
    try:
        client.api.move_remote_media_to_trash([dedup])
    except Exception as e:  # already in the Trash (deleted by hand): deleting it from there still works
        if outage_kind(e):
            raise
    client.api.delete_remote_media_permanently([dedup])
    for wait in (0, 1, 2, 4):  # Google can take a moment to forget it
        time.sleep(wait)
        if not any(client.api.find_remote_media_by_hash(d) for d in digests):
            return True
    return False


def _own_uploads_count(account):
    """True when GP Sync's own uploads count against storage in this account: freeing would only
    move the cost, so nothing is deleted there."""
    ours = [
        _b64_hash(bytes.fromhex(h)) for (h,) in q("SELECT sha1 FROM files WHERE sha1 != '' AND account=?", (account,))
    ]
    for i in range(0, len(ours), 500):
        part = ours[i : i + 500]
        rows = _library_rows(
            account,
            f"""SELECT 1 FROM remote_media WHERE quota_charged_bytes > 0 AND COALESCE(trash_timestamp, 0) = 0
                AND dedup_key IN ({",".join("?" * len(part))}) LIMIT 1""",
            part,
        )
        if rows:
            return True
    return False


def _restore_details(client, dedup, favorite, archived, caption):
    """Favourite, archived and caption come back (they belong to the file, not to an album)."""
    for step in (
        lambda: favorite and client.api.set_favorite(dedup, True),
        lambda: archived and client.api.set_archived([dedup], True),
        lambda: (
            caption and not _junk_caption(caption) and client.api.set_item_caption(dedup_key=dedup, caption=caption)
        ),
    ):
        try:
            step()
        except Exception as e:
            print(f"Free storage: couldn't restore details: {type(e).__name__}: {e}", file=sys.stderr, flush=True)


DOWNLOADS_DIR = DATA / "downloads"


def _keep_download(account, path, media_key, size, taken):
    """Move a freed item's downloaded copy into Downloads (per account and year), keeping its name."""
    if not path.is_file():
        return
    year = time.strftime("%Y", time.gmtime(taken)) if taken else "undated"
    folder = DOWNLOADS_DIR / account / year
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / path.name
    n = 1
    while target.exists():
        target = folder / f"{path.stem} ({n}){path.suffix}"
        n += 1
    shutil.move(str(path), target)
    kind = "video" if path.suffix.lower().lstrip(".") in VIDEO_EXTS else "photo"
    q(
        "INSERT INTO downloads (account, name, kind, size, taken, path, media_key, added) VALUES (?,?,?,?,?,?,?,?)",
        (account, target.name, kind, size, taken, str(target), media_key, time.time()),
    )


def downloads_report(account="", limit=500):
    """Copies kept on this server, per account, newest first."""
    totals = {}
    for acc, kind, n, b in q("SELECT account, kind, COUNT(*), SUM(size) FROM downloads GROUP BY account, kind"):
        t = totals.setdefault(acc, {"photos": 0, "videos": 0, "bytes": 0})
        t["videos" if kind == "video" else "photos"] += n
        t["bytes"] += b or 0
    rows = q(
        """SELECT id, account, name, kind, size, taken, added FROM downloads WHERE (? = '' OR account = ?)
           ORDER BY added DESC LIMIT ?""",
        (account, account, int(limit)),
    )
    return {
        "folder": str(DOWNLOADS_DIR),
        "accounts": totals,
        "items": [dict(zip(("id", "account", "name", "kind", "size", "taken", "added"), r, strict=True)) for r in rows],
    }


def downloads_delete(ids=None, account=None):
    """Delete kept copies (by id, or all of one account). Never touches Google."""
    if ids:
        rows = q(
            "SELECT id, path FROM downloads WHERE id IN (SELECT value FROM json_each(?))",
            (json.dumps([int(i) for i in ids]),),
        )
    elif account:
        rows = q("SELECT id, path FROM downloads WHERE account=?", (account,))
    else:
        rows = []
    for i, path in rows:
        with contextlib.suppress(OSError):
            Path(path).unlink()
        q("DELETE FROM downloads WHERE id=?", (i,))
    return {"deleted": len(rows)}


def download_path(i):
    """The file of one kept copy, if it is still there."""
    rows = q("SELECT path FROM downloads WHERE id=?", (int(i),))
    p = Path(rows[0][0]) if rows else None
    return p if p and p.is_file() and DOWNLOADS_DIR in p.parents else None


def refresh_library(account, make_client=None):
    """Read the account's library list from Google (the first time can take a few minutes)."""
    _refreshing.add(account)
    reclaim_state[account]["error"] = ""
    notify_change()
    try:
        update_library((make_client or _reclaim_client)(account), account)
    except Exception as e:
        reclaim_state[account]["error"] = f"{type(e).__name__}: {e}"[:300]
    finally:
        _refreshing.discard(account)
        notify_change()


_refresh_requests = collections.deque()
_refresh_lock = threading.Lock()


def _take_refresh(account):
    """True (once) if someone asked for this account's library to be read."""
    with _refresh_lock:
        if account not in _refresh_requests:
            return False
        while account in _refresh_requests:
            _refresh_requests.remove(account)
        return True


class Lanes:
    """Background work in lanes that run side by side: one thread per key (an account, or a pair of
    accounts), so one account's work never waits for another's. A lane starts when woken and stops
    after a quiet spell; whoever calls wake() keeps lanes with pending work alive."""

    def __init__(self, name, step, idle=600):
        self.name, self.step, self.idle = name, step, idle
        self.threads, self.wakes = {}, {}
        self.lock = threading.Lock()

    def wake(self, keys):
        for key in keys:
            with self.lock:  # the event is set under the lock, so a lane never stops after a wake
                ev = self.wakes.setdefault(key, threading.Event())
                lane = self.threads.get(key)
                if lane is None or not lane.is_alive():
                    lane = threading.Thread(target=self._run, args=(key,), daemon=True, name=f"{self.name} {key}")
                    self.threads[key] = lane
                    lane.start()
                ev.set()

    def nudge(self, key):
        """Wake a lane that is running, without starting one."""
        with self.lock:
            lane, ev = self.threads.get(key), self.wakes.get(key)
            if lane is not None and lane.is_alive() and ev is not None:
                ev.set()

    def _run(self, key):
        ev = self.wakes[key]
        quiet = 0
        while True:
            ev.clear()
            try:
                busy = self.step(key)
            except Exception as e:
                print(f"{self.name} ({key}): {type(e).__name__}: {e}", file=sys.stderr, flush=True)
                busy = False
            if busy:
                quiet = 0
            elif quiet >= self.idle:
                with self.lock:
                    if not ev.is_set():
                        self.threads.pop(key, None)
                        return
            if not ev.wait(1 if busy else 60) and not busy:
                quiet += 60


def _account_step(account):
    """One account's Google work: read its library when asked, free storage, fix dates."""
    busy = False
    while _take_refresh(account):
        refresh_library(account)
        busy = True
    busy = reclaim_step(account=account, wait=False) or busy
    try:
        busy = caption_repair_step(account) or busy
    except Exception as e:
        print(f"Captions ({account}): {type(e).__name__}: {e}", file=sys.stderr, flush=True)
    try:
        busy = snapchat_work(account) or busy
    except Exception as e:
        print(f"Snapchat ({account}): {type(e).__name__}: {e}", file=sys.stderr, flush=True)
    return datefix_step(only=account) or busy


account_lanes = Lanes("Google work", _account_step)


def reclaim_worker():
    """Keeps a lane per connected account going (free storage, fix dates, library reads, all side by
    side across accounts), and adds albums and captions for every account."""
    with contextlib.suppress(Exception):  # reset work cut short by a restart
        q("UPDATE reclaim SET state='queued' WHERE state='downloading'")
    with contextlib.suppress(Exception):
        # Items queued before 1.5.2 kept gpmc's "{}" (no caption) as their caption: drop it.
        q("UPDATE reclaim SET caption='' WHERE caption LIKE '{%}' AND state != 'done'")
        q("UPDATE copyjobs SET caption='' WHERE caption LIKE '{%}' AND state NOT IN ('copied', 'done')")
        # Downloads refused before Google's second checksum was known: they can work now, once.
        if not meta_get("reclaim_requeued_edited"):
            q("""UPDATE reclaim SET state='queued', error='' WHERE state='failed'
                 AND error LIKE '%not exactly Google''s original%'""")
            meta_set("reclaim_requeued_edited", "1")
    while True:
        reclaim_wake.clear()
        account_lanes.wake(dict.fromkeys([*(a for a, _ in accounts()), *list(_refresh_requests)]))
        busy = False
        for step in (undated_album_step, sources_step):
            try:
                busy = step() or busy
            except Exception as e:
                print(f"Albums: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        reclaim_wake.wait(1 if busy else 120)


# ---------------------------------------------------------------- dates
# Google Photos dates a photo or video by the date stored inside it. Files with none (Snapchat
# saves, screenshots, chat pictures) get the date sent with the upload, which gpmc takes from the
# file's modified time: often only when the file was copied here. So for those GP Sync sends a
# better date (one the user picked, or the earliest identical copy), puts them in an album of
# their own, and can fix ones already uploaded: GP Sync replaces Google's copy with one uploaded
# again with the right date. Facts about dates live in the Turso database.

DATE_SLACK = 120  # seconds Google's date may be off before we call it wrong
SAME_SIZE_HASHES = 50  # at most this many same-size files are fingerprinted to find copies of one file
dates_state = collections.defaultdict(lambda: {"current": None, "error": ""})  # per account
_dates_checked = {}  # path -> when this file was last tried
_dates_checked_lock = threading.Lock()


def date_facts(path, size, mtime):
    """Whether a file keeps its own date: 1 yes, 0 no, None can't tell. Checked again when it changes."""
    rows = sq("SELECT size, mtime, inside FROM media_dates WHERE path=?", (path,))
    if rows and rows[0][0] == size and rows[0][1] == mtime:
        return rows[0][2]
    inside = media.has_date(path)
    inside = None if inside is None else int(inside)
    sq(
        """INSERT INTO media_dates (path, size, mtime, inside, updated) VALUES (?,?,?,?,?)
           ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime, inside=excluded.inside,
           sha1=NULL, updated=excluded.updated""",
        (path, size, mtime, inside, time.time()),
    )
    return inside


def remember_sha1(path, sha1hex):
    sq("UPDATE media_dates SET sha1=? WHERE path=?", (sha1hex, path))


def dates_some(limit=300):
    """While idle: check which files keep their own date and where they came from, and fingerprint
    the ones without a date (and same-size files) so identical copies are known. Only folders that
    are backed up are looked at. Returns how much was done."""
    known = {p: (s, m) for p, s, m in sq("SELECT path, size, mtime FROM media_dates")}
    known_src = {p: (s, m) for p, s, m in sq("SELECT path, size, mtime FROM provenance")}
    backed = backed_folders()
    n = 0
    sources_changed = False
    for name, _video, size, _added, _backed, mtime in list(state.get("index") or []):
        if backed is not None and name.split("/", 1)[0] not in backed:
            continue
        path = str(MEDIA / name)
        if path in _snap["paths"]:
            continue  # Snapchat memories have their date inside and are handled on the Snapchat page
        dated, sourced = known.get(path) == (size, mtime), known_src.get(path) == (size, mtime)
        if dated and sourced:
            continue
        if n >= limit or state["phase"] not in IDLE_PHASES:
            break
        if not dated:
            date_facts(path, size, mtime)
            n += 1
        if not sourced:
            source_facts(path, size, mtime)
            sources_changed = True
            n += 1
    if sources_changed:
        refresh_estimates()
    for path, size in sq("SELECT path, size FROM media_dates WHERE inside=0 AND sha1 IS NULL"):
        if n >= limit or state["phase"] not in IDLE_PHASES:
            break
        try:
            digest = media.sha1_file(path).hex()
        except OSError:
            continue
        remember_sha1(path, digest)
        same_content(path, size, digest)
        n += 10
    return n


def same_content(path, size, sha1hex, fingerprint=True):
    """Other files here with exactly the same content, as {path: modified time}."""
    out = dict(q("SELECT path, mtime FROM files WHERE sha1=? AND path!=?", (sha1hex, path)))
    for p, m in sq("SELECT path, mtime FROM media_dates WHERE sha1=? AND path!=?", (sha1hex, path)):
        out.setdefault(p, m)
    if not fingerprint:
        return out
    hashed = 0  # files not fingerprinted yet can only be copies if they have the same size
    for name, _video, s, _added, _backed, m in list(state.get("index") or []):
        p = str(MEDIA / name)
        if s != size or p == path or p in out:
            continue
        if hashed >= SAME_SIZE_HASHES:
            break
        date_facts(p, s, m)
        row = sq("SELECT sha1 FROM media_dates WHERE path=?", (p,))
        digest = row[0][0] if row and row[0][0] else None
        if digest is None:
            try:
                digest = media.sha1_file(p).hex()
            except OSError:
                continue
            remember_sha1(p, digest)
            hashed += 1
        if digest == sha1hex:
            out[p] = m
    return out


def best_date(path, size, mtime, sha1hex, fingerprint=True):
    """(date to give Google, where it comes from) for a file with no date of its own: a date the
    user picked ("manual"), the moment of the Snapchat memory it is a copy of ("snapchat"), a date
    in the file name ("filename", e.g. WhatsApp's IMG-20240504-WA0001),
    the estimated save date of a Snapchat item ("estimated"), the earliest identical copy here
    ("copy"), or the file's own time ("file")."""
    if sha1hex:
        picked = sq(
            "SELECT MIN(manual) FROM media_dates WHERE (path=? OR sha1=?) AND manual IS NOT NULL", (path, sha1hex)
        )
    else:
        picked = sq("SELECT manual FROM media_dates WHERE path=? AND manual IS NOT NULL", (path,))
    if picked and picked[0][0]:
        return float(picked[0][0]), "manual"
    if (snap := snapchat_time(path, sha1hex)) is not None:
        return snap, "snapchat"
    st = get_settings()
    named = provenance.name_date(path, st["tz_offset_min"])
    if named is not None and named <= time.time() + 86400:
        return named, "filename"
    if st["sources"]["estimate_dates"] and (est := estimated_date(path)) is not None:
        return est, "estimated"
    if sha1hex:
        times = [m for m in same_content(path, size, sha1hex, fingerprint).values() if m]
        if times and min(times) < mtime - 1:
            return float(min(times)), "copy"
    return float(mtime), "file"


_upload_when = threading.local()  # the date this thread's next commit sends, if any


@contextlib.contextmanager
def upload_date(client, when, mtime=None):
    """Upload with `when` (unix seconds) as the date, instead of the file's modified time that gpmc
    sends. One login (client) is shared by every worker of an account, which upload at the same
    time: the date is kept per thread, so it never reaches another worker's upload. Nothing changes
    when there is no date to send."""
    if when is None or (mtime is not None and int(when) == int(mtime)):
        yield
        return
    api = client.api
    original = getattr(api, "commit_upload", None)
    if original is None:  # not gpmc (tests): nothing to wrap
        yield
        return
    if not getattr(original, "_gp_dated", False):  # wrapped once, for good

        def commit(*args, **kwargs):
            when_ = getattr(_upload_when, "value", None)
            if when_ is not None:
                kwargs["upload_timestamp"] = int(when_)
            return original(*args, **kwargs)

        commit._gp_dated = True
        api.commit_upload = commit
    _upload_when.value = when
    try:
        yield
    finally:
        _upload_when.value = None


def _main_account(account):
    accs = accounts()
    return account or (accs[0][0] if accs else "")


def undated_paths():
    return {p for (p,) in sq("SELECT path FROM media_dates WHERE inside=0")}


def undated_album_step(make_client=None):
    """Put uploaded files without a date of their own in their album (Settings, Albums). Waits
    until the user has seen the choice. Returns True if anything was added."""
    cfg = get_settings()["undated"]
    if not cfg["album"] or not cfg["asked"]:
        return False
    # Items saved from an app that has an album of its own (Sources) go there instead.
    undated = undated_paths() - own_album_paths()
    if not undated:
        return False
    # Done is per file and account: counting paths alone missed new files when older entries
    # were no longer undated, and copies in a folder's other accounts.
    done = {(p, a) for p, a in sq("SELECT path, account FROM undated_album WHERE album=?", (cfg["name"],))}
    rows = q(
        """SELECT path, account, media_key FROM files WHERE status IN ('uploaded','exists') AND media_key != ''
           AND path IN (SELECT value FROM json_each(?))
           UNION ALL SELECT path, account, media_key FROM copies WHERE status IN ('uploaded','exists')
           AND media_key != '' AND path IN (SELECT value FROM json_each(?))""",
        (json.dumps(sorted(undated)),) * 2,
    )
    accs = dict(accounts())
    todo = collections.defaultdict(list)
    for path, account, key in rows:
        account = _main_account(account)
        if account in accs and key and (path, account) not in done:
            todo[account].append((path, key))
    did = False
    for account, items in todo.items():
        client = (make_client or _reclaim_client)(account)
        for i in range(0, min(len(items), 1000), 100):
            chunk = items[i : i + 100]
            added, _left_out, ok = album_add(client, account, cfg["name"], chunk)
            for path in added:
                sq("INSERT OR REPLACE INTO undated_album VALUES (?,?,?)", (path, account, cfg["name"]))
            did = did or bool(added)
            if not ok:
                return did
    return did


def _google_dates(account, dedups):
    """Google's date for each dedup key, from the account's library list (if it has been read)."""
    out = {}
    keys = list(dedups)
    for i in range(0, len(keys), 500):
        part = keys[i : i + 500]
        rows = _library_rows(
            account,
            f"""SELECT dedup_key, utc_timestamp, is_favorite, is_archived, caption, media_key FROM remote_media
                WHERE dedup_key IN ({",".join("?" * len(part))}) AND COALESCE(trash_timestamp, 0) = 0
                ORDER BY is_canonical""",
            part,
        )
        for dedup, ts, fav, arch, cap, key in rows or []:
            out[dedup] = (_seconds(ts), fav, arch, cap, key)
    return out


def _known_google_date(listed, refreshed, updated, status_, sent):
    """Return the date and evidence; an old file mtime alone is not a known Google date."""
    if listed and refreshed > (updated or 0):
        return listed[0], "google"
    if status_ == "uploaded" and sent is not None:
        return float(sent), "sent"
    if status_ == "exists" and listed:
        return listed[0], "google"
    return None, "unknown"


def dates_report(account):
    """Uploaded files with no date of their own: the date Google has, the date they should have,
    and the progress of fixing them."""
    account = account or _main_account("")
    refreshed = float(meta_get(f"library_refreshed:{account}") or 0)
    facts = {
        p: r
        for p, *r in sq(
            "SELECT path, sha1, manual, sent, fix, error, previous_google, previous_google_source "
            "FROM media_dates WHERE inside=0"
        )
    }
    first = _main_account("")
    rows = [
        r
        for r in q(
            """SELECT path, size, mtime, status, media_key, account, updated, sha1 FROM files
               WHERE status IN ('uploaded','exists') AND path IN (SELECT value FROM json_each(?))""",
            (json.dumps(sorted(facts)),),
        )
        if (r[5] or first) == account
    ]
    google = _google_dates(
        account, {_b64_hash(bytes.fromhex(r[7] or facts[r[0]][0])) for r in rows if r[7] or facts[r[0]][0]}
    )
    # Identical copies are one item in Google: list it once (the copy being fixed, else the uploaded one).
    rows.sort(key=lambda r: (not facts[r[0]][3], r[3] != "uploaded", r[0]))
    seen = set()
    items = []
    for path, size, mtime, status_, key, _acc, updated, sha1 in rows:
        sha1 = sha1 or facts[path][0] or ""
        if sha1 and sha1 in seen:
            continue
        seen.add(sha1)
        _, manual, sent, fix, error, previous_google, previous_source = facts[path]
        listed = google.get(_b64_hash(bytes.fromhex(sha1))) if sha1 else None
        in_google, google_source = _known_google_date(listed, refreshed, updated, status_, sent)
        if listed and (not key or refreshed > (updated or 0)):
            key = listed[4] or key
        best, source = best_date(path, size, mtime, sha1, fingerprint=False)
        if source not in ("manual", "filename") and in_google is not None and in_google < best - DATE_SLACK:
            # A file is copied here after it was taken, never before: an earlier date in Google
            # (from the phone's own backup, say) is closer to the truth than our file times.
            best, source = in_google, "google"
        items.append(
            {
                "name": rel(path),
                "kind": "video" if ext(path) in VIDEO_EXTS else "photo",
                "size": size,
                "google": in_google,
                "google_source": google_source,
                "previous_google": previous_google,
                "previous_google_source": previous_source or "unknown",
                "best": best,
                "source": source,
                "manual": manual,
                "wrong": in_google is not None and abs(in_google - best) > DATE_SLACK,
                "fix": fix or "",
                "error": error or "",
                "google_url": (
                    f"https://photos.google.com/photo/{urllib.parse.quote(str(key), safe='')}" if key else ""
                ),
            }
        )
    order = {"waiting": 0, "in_google": 0, "failed": 1}
    items.sort(key=lambda i: (order.get(i["fix"], 2), not i["wrong"], -(i["best"] or 0)))
    cfg = get_settings()["undated"]
    return {
        "account": account,
        "accounts": [a for a, _ in accounts()],
        "refreshed": refreshed or None,
        "refreshing": account in _refreshing,
        "paused": dates_paused(account),
        "items": items,
        "undated": len(items),
        "wrong": sum(i["wrong"] for i in items),
        "waiting": sum(i["fix"] in ("waiting", "in_google") for i in items),
        "fixed": sum(i["fix"] == "done" for i in items),
        "checked": sq("SELECT COUNT(*) FROM media_dates")[0][0],
        "album": cfg["name"] if cfg["album"] else "",
        "current": dates_state[account]["current"],
        "error": dates_state[account]["error"],
    }


def _date_target(name):
    """The files row for a dashboard file name, if it is an uploaded file with no date of its own."""
    path = str(MEDIA / name)
    if not clean_folder(name) or not q("SELECT 1 FROM files WHERE path=? AND status IN ('uploaded','exists')", (path,)):
        return None
    return path if sq("SELECT 1 FROM media_dates WHERE path=? AND inside=0", (path,)) else None


def dates_set(name, when):
    """Use the date the user picked (unix seconds), or None to go back to the automatic one."""
    path = _date_target(name)
    if not path:
        raise ValueError("Only uploaded files without a date of their own can get one")
    if when is not None:
        when = float(when)
        if not 0 < when < time.time() + 86400:
            raise ValueError("Pick a date in the past")
    sq("UPDATE media_dates SET manual=? WHERE path=?", (when, path))
    return {"ok": True}


def dates_fix(names):
    """Queue these files to be replaced in Google Photos with the right date."""
    n = 0
    for name in names:
        path = _date_target(name)
        if not path:
            continue
        sha1, account, status_, updated = q("SELECT sha1, account, status, updated FROM files WHERE path=?", (path,))[0]
        sha1 = sha1 or (sq("SELECT sha1 FROM media_dates WHERE path=?", (path,)) or [[None]])[0][0]
        if not sha1:
            sha1 = media.sha1_file(path).hex()
            remember_sha1(path, sha1)
        if _snap_replaces(sha1):
            continue  # the Snapchat page puts the memory in its place, with Snapchat's date
        # Favourite, archive and caption are read now: they are gone once Google's copy is deleted.
        found = _google_dates(_main_account(account), [_b64_hash(bytes.fromhex(sha1))]).get(
            _b64_hash(bytes.fromhex(sha1))
        )
        _, fav, arch, cap, _key = found or (0, 0, 0, "", "")
        sent, fix, previous, previous_source = sq(
            "SELECT sent, fix, previous_google, previous_google_source FROM media_dates WHERE path=?", (path,)
        )[0]
        refreshed = float(meta_get(f"library_refreshed:{_main_account(account)}") or 0)
        before, before_source = _known_google_date(found, refreshed, updated, status_, sent)
        if fix in ("waiting", "in_google", "failed"):
            before, before_source = previous, previous_source
        sq(
            """UPDATE media_dates SET fix='waiting', error='', sha1=?, favorite=?, archived=?, caption=?,
               previous_google=?, previous_google_source=?
               WHERE path=?""",
            (sha1, int(bool(fav)), int(bool(arch)), cap or "", before, before_source, path),
        )
        with _dates_checked_lock:
            _dates_checked.pop(path, None)
        n += 1
    reclaim_wake.set()
    return {"queued": n}


def dates_cancel(names):
    n = 0
    for name in names:
        path = str(MEDIA / name)
        if sq("SELECT 1 FROM media_dates WHERE path=? AND fix IN ('waiting','in_google','failed')", (path,)):
            sq("UPDATE media_dates SET fix=NULL, error='' WHERE path=?", (path,))
            n += 1
    return {"cancelled": n}


def dates_recheck(account, names=None):
    """Request an immediate check of selected waiting files, or all waiting files for an account."""
    if account not in dict(accounts()):
        raise ValueError("unknown account")
    waiting = {p for (p,) in sq("SELECT path FROM media_dates WHERE fix IN ('waiting','in_google')")}
    if names is not None:
        waiting &= {str(MEDIA / name) for name in names if clean_folder(name)}
    rows = q(
        """SELECT path, account FROM files WHERE status IN ('uploaded','exists')
           AND path IN (SELECT value FROM json_each(?))""",
        (json.dumps(sorted(waiting)),),
    )
    first = _main_account("")
    paths = [path for path, owner in rows if (owner or first) == account]
    with _dates_checked_lock:
        for path in paths:
            _dates_checked.pop(path, None)
    if paths:
        reclaim_wake.set()
    return {"queued": len(paths)}


def _set_fix(path, fix, error=""):
    sq("UPDATE media_dates SET fix=?, error=? WHERE path=?", (fix, error, path))
    notify_change()


def dates_paused(account):
    """The user paused Fix dates for this account: nothing more is deleted and uploaded again."""
    return meta_get(f"dates_paused:{account}") == "1"


def datefix_step(make_client=None, only=None):
    """Replace queued files in Google Photos with the right date: Google's copy is deleted (once the
    file here is checked to be the same) and the file uploaded again. A file Google still has is
    tried again after 2 minutes. `only`: just this account's files. Returns True if one was uploaded."""
    make_client = make_client or _reclaim_client
    rows = sq("SELECT path, sha1, favorite, archived, caption FROM media_dates WHERE fix IN ('waiting','in_google')")
    now = time.time()
    did = False
    for path, sha1, fav, arch, cap in rows:
        found = q("SELECT size, mtime, account FROM files WHERE path=? AND status IN ('uploaded','exists')", (path,))
        account = _main_account(found[0][2]) if found else ""
        if only is not None and account != only:
            continue  # another account's worker does it
        if dates_paused(account):
            continue
        with _dates_checked_lock:
            if now - _dates_checked.get(path, 0) < 100:
                continue
            _dates_checked[path] = now
        if not found or not sha1:
            _set_fix(path, "failed", "This file isn't on the server any more")
            continue
        size, mtime, _account = found[0]
        track = dates_state[account]
        name = rel(path)
        try:
            client = make_client(account)
            digest = bytes.fromhex(sha1)
            # Google's copy is replaced only when the file here is exactly what was uploaded.
            if media.sha1_file(path) != digest:
                _set_fix(path, "failed", "The file here changed after it was uploaded")
                continue
            if client.api.find_remote_media_by_hash(digest):
                track["current"] = {"key": name, "step": "deleting", "done": 0, "total": size}
                if not _delete_in_google(client, _b64_hash(digest)):
                    _set_fix(path, "in_google", "Google hasn't deleted it yet: trying again soon")
                    continue
                log(
                    f"Deleted {name} in Google Photos ({account}) to upload it again with the right date",
                    "deleted",
                    name,
                )
            when = best_date(path, size, mtime, sha1, fingerprint=False)[0]
            track["current"] = {"key": name, "step": "uploading", "done": 0, "total": size}
            key = _reupload(client, Path(path), _b64_hash(digest), size, name, track, when=when)
            if key is None:
                _set_fix(path, "in_google", "Google still has it: trying again soon")
                continue
            _restore_details(client, _b64_hash(digest), fav, arch, cap)
            q("UPDATE files SET media_key=?, status='uploaded', updated=? WHERE path=?", (key, time.time(), path))
            sq("UPDATE media_dates SET fix='done', error='', sent=? WHERE path=?", (int(when), path))
            sq("DELETE FROM undated_album WHERE path=? AND account=?", (path, account))  # added again
            if (album := album_name(path, get_settings()["albums"])) is not None:
                add_to_album(client, account, album, [key])
            track["error"] = ""
            log(
                f"Fixed the date of {name}: re-uploaded dated {time.strftime('%Y-%m-%d %H:%M', time.gmtime(when))} UTC",
                "fixed",
                name,
            )
            did = True
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"[:300]
            print(f"Fix dates: {name}: {msg}", file=sys.stderr, flush=True)
            if outage := outage_kind(e):
                track["error"] = outage
                return did
            sq("UPDATE media_dates SET error=? WHERE path=?", (msg, path))  # tried again in 2 minutes
        finally:
            track["current"] = None
    return did


# ---------------------------------------------------------------- where files came from
# Every backed-up file is labelled by provenance.detect: a camera, Snapchat, a screenshot, another
# app, or "saved from an app" when no app name survives. Items saved from apps can go into an album
# per app, get a searchable caption, or be archived (out of the timeline, still in albums and
# search). Snapchat saves also get an estimated save date. Nothing is done in Google until the user
# has seen the choice (settings sources.asked).

sources_state = {"error": "", "needs_library": False}
SOURCE_BATCH = 100
SOURCE_STEP_LIMIT = 300  # Google calls per step at most; the worker comes back a second later
_EFFECTIVE = "COALESCE(decision, source)"


def source_facts(path, size, mtime):
    """Detect where a file came from (again when it changed). The user's decision is kept."""
    rows = sq("SELECT size, mtime FROM provenance WHERE path=?", (path,))
    if rows and rows[0][0] == size and rows[0][1] == mtime:
        return False
    info = provenance.detect(path)
    sq(
        """INSERT INTO provenance (path, size, mtime, source, app, device, app_version, os_version, taken,
                                   confidence, reasons, updated)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime, source=excluded.source,
           app=excluded.app, device=excluded.device, app_version=excluded.app_version,
           os_version=excluded.os_version, taken=excluded.taken, confidence=excluded.confidence,
           reasons=excluded.reasons, updated=excluded.updated""",
        (path, size, mtime, info["source"], info["app"], info["device"], info["app_version"], info["os_version"],
         info["taken"], info["confidence"], json.dumps(info["reasons"]), time.time()),
    )  # fmt: skip
    return True


def refresh_estimates():
    """Place Snapchat saves in time with the camera photos taken on the same iOS versions."""
    rows = sq("SELECT path, source, device, os_version, taken, app_version, mtime FROM provenance")
    camera = [(d, o, t) for _p, s, d, o, t, _a, _m in rows if s == provenance.CAMERA and o and t]
    snaps = [(p, a, d, o, m) for p, s, d, o, _t, a, m in rows if s == "snapchat" and a and o]
    found = provenance.estimate_snapchat_dates(snaps, camera)
    with stats_lock:
        STATS.execute("UPDATE provenance SET est_date=NULL, est_lo=NULL, est_hi=NULL, est_reason=NULL")
        for path, e in found.items():
            STATS.execute(
                "UPDATE provenance SET est_date=?, est_lo=?, est_hi=?, est_reason=? WHERE path=?",
                (e["date"], e["lo"], e["hi"], e["reason"], path),
            )
        STATS.commit()
    cache.bump()
    return len(found)


def _is_snap(path):
    return bool(sq(f"SELECT 1 FROM provenance WHERE path=? AND {_EFFECTIVE}='snapchat'", (path,)))


def estimated_date(path):
    """The estimated save date of a file that is (still) labelled Snapchat, or None."""
    rows = sq(f"SELECT est_date FROM provenance WHERE path=? AND {_EFFECTIVE}='snapchat'", (path,))
    return float(rows[0][0]) if rows and rows[0][0] else None


def own_album_paths():
    """Files whose app has an album of its own turned on (they skip the "Date unknown" album)."""
    cfg = get_settings()["sources"]
    with_album = [s for s in provenance.APP_SOURCES if cfg[s]["album"]]
    if not cfg["asked"] or not with_album:
        return set()
    marks = ",".join("?" * len(with_album))
    return {p for (p,) in sq(f"SELECT path FROM provenance WHERE {_EFFECTIVE} IN ({marks})", with_album)}


def _caption_for(source, est_date=None, est_lo=None, est_hi=None):
    label = {"app_export": "Saved from an app", "screenshot": "Screenshot"}.get(
        source, f"Saved from {provenance.LABELS.get(source, source)}"
    )
    if est_date:
        days = max(1, round(((est_hi or est_date) - (est_lo or est_date)) / 2 / 86400))
        label += f" · around {time.strftime('%-d %b %Y', time.gmtime(est_date))} ± {days} day{'s' * (days != 1)}"
    return label


def _source_targets(cfg):
    """What should be true in Google for each labelled, uploaded file:
    {account: [(path, media key, dedup key, album or "", archive, caption or None, row)]}."""
    rows = sq(
        f"""SELECT path, {_EFFECTIVE}, album, archived, captioned, est_date, est_lo, est_hi FROM provenance
            WHERE {_EFFECTIVE} IN ({",".join("?" * len(provenance.APP_SOURCES))})
            OR archived=1 OR captioned IS NOT NULL OR album IS NOT NULL""",
        provenance.APP_SOURCES,
    )
    if not rows:
        return {}
    files = {
        p: (a, k, h)
        for p, a, k, h in q(
            """SELECT path, account, media_key, sha1 FROM files WHERE status IN ('uploaded','exists')
               AND media_key != '' AND path IN (SELECT value FROM json_each(?))""",
            (json.dumps([r[0] for r in rows]),),
        )
    }
    accs = dict(accounts())
    out = collections.defaultdict(list)
    for path, source, album, archived, captioned, est, lo, hi in rows:
        if path not in files:
            continue  # not in Google yet
        account, key, sha1 = files[path]
        account = _main_account(account)
        if account not in accs:
            continue
        s = cfg.get(source) if source in provenance.APP_SOURCES else None
        want_album = s["album_name"] if s and s["album"] else ""
        want_archive = bool(s and s["archive"])
        want_caption = _caption_for(source, est, lo, hi) if s and s["caption"] else None
        dedup = _b64_hash(bytes.fromhex(sha1)) if sha1 else ""
        out[account].append((path, key, dedup, want_album, want_archive, want_caption, (album, archived, captioned)))
    return out


def sources_step(make_client=None):
    """Bring Google in line with the Sources settings: albums, archive and captions for items
    saved from apps, undone when turned off. Returns True if anything was done."""
    cfg = get_settings()["sources"]
    if not cfg["asked"]:
        return False
    targets = _source_targets(cfg)
    did, calls = False, 0
    sources_state["needs_library"] = False
    for account, items in targets.items():
        client = None

        def api(account=account):
            nonlocal client
            client = client or (make_client or _reclaim_client)(account)
            return client

        try:
            # Albums: add to the app's album once (a renamed album gets them again).
            by_album = collections.defaultdict(list)
            for path, key, _d, want_album, _a, _c, (album, _ar, _cap) in items:
                if want_album and album != want_album:
                    by_album[want_album].append((path, key))
            for name, todo in by_album.items():
                for i in range(0, len(todo), SOURCE_BATCH):
                    if calls >= SOURCE_STEP_LIMIT:
                        return True
                    chunk = todo[i : i + SOURCE_BATCH]
                    calls += 1
                    added, _left_out, ok = album_add(api(), account, name, chunk)
                    _set_sources("album", name, added)
                    did = did or bool(added)
                    if not ok:
                        sources_state["error"] = f"Could not add to the album {name}"
                        break
            # Archive and unarchive (needs the file's fingerprint, made while idle).
            for want in (True, False):
                todo = [
                    (p, d) for p, _k, d, _al, wa, _c, (_a, ar, _cp) in items if d and wa == want and bool(ar) != want
                ]
                for i in range(0, len(todo), SOURCE_BATCH):
                    if calls >= SOURCE_STEP_LIMIT:
                        return True
                    chunk = todo[i : i + SOURCE_BATCH]
                    calls += 1
                    api().api.set_archived([d for _, d in chunk], want)
                    _set_sources("archived", int(want), [p for p, _ in chunk])
                    did = True
            # Captions: only where Google has none (or still ours), read from the library list.
            pending = [it for it in items if it[2] and (it[5] or None) != _ours(it[6][2])]
            if pending:
                google = _google_dates(account, [it[2] for it in pending])
                for path, _k, dedup, _al, _wa, want_caption, (_a, _ar, captioned) in pending:
                    listed = google.get(dedup)
                    if listed is None:
                        sources_state["needs_library"] = True  # unknown whether it has its own caption
                        continue
                    current = listed[3] or ""
                    if current and current != _ours(captioned):
                        if want_caption and captioned != "-":
                            _set_sources("captioned", "-", [path])  # the user's own caption stays
                        elif not want_caption and captioned:
                            _set_sources("captioned", None, [path])
                        continue
                    if calls >= SOURCE_STEP_LIMIT:
                        return True
                    calls += 1
                    api().api.set_item_caption(dedup_key=dedup, caption=want_caption or "")
                    _set_sources("captioned", want_caption, [path])
                    did = True
            sources_state["error"] = ""
        except Exception as e:
            msg = outage_kind(e) or f"{type(e).__name__}: {e}"[:300]
            sources_state["error"] = msg
            print(f"Sources: {msg}", file=sys.stderr, flush=True)
            return did
    if did:
        notify_change()
    return did


def _ours(captioned):
    """The caption GP Sync wrote, or None ("-" marks an item that kept its own caption)."""
    return None if captioned in (None, "-") else captioned


def _set_sources(column, value, paths):
    for i in range(0, len(paths), 500):
        part = paths[i : i + 500]
        sq(
            f"UPDATE provenance SET {column}=? WHERE path IN ({','.join('?' * len(part))})",
            (value, *part),
        )


def sources_report(source="", limit=600):
    """Counts per source and device, and the files of one source with the evidence for it."""
    cfg = get_settings()["sources"]
    backed = backed_folders()
    indexed = {
        str(MEDIA / e[0]): e for e in state.get("index") or [] if backed is None or e[0].split("/", 1)[0] in backed
    }
    rows = [
        r
        for r in sq(
            f"""SELECT path, source, decision, {_EFFECTIVE}, app, device, app_version, os_version, taken, confidence,
                       reasons, est_date, est_lo, est_hi, est_reason, album, archived, captioned FROM provenance"""
        )
        if r[0] in indexed
    ]
    uploaded = {
        p: k
        for p, k in q("SELECT path, media_key FROM files WHERE status IN ('uploaded','exists') AND media_key != ''")
    }
    counts = collections.Counter(r[3] for r in rows)
    devices = collections.Counter(r[5] or "Unknown camera" for r in rows if r[3] == provenance.CAMERA)
    if source not in provenance.SOURCES:
        source = next((s for s in provenance.APP_SOURCES if counts.get(s)), "snapchat")
    picked = [r for r in rows if r[3] == source]
    picked.sort(key=lambda r: -(r[11] or r[8] or indexed[r[0]][5] or 0))
    items = []
    for (path, detected, decision, eff, app, device, app_ver, os_ver, taken, confidence, reasons, est, lo, hi,
         est_reason, album, archived, captioned) in picked[:limit]:  # fmt: skip
        e = indexed[path]
        key = uploaded.get(path, "")
        items.append(
            {
                "name": rel(path),
                "kind": "video" if e[1] else "photo",
                "size": e[2],
                "mtime": int(e[5]),
                "source": eff,
                "detected": detected,
                "decided": decision is not None,
                "app": app or "",
                "device": provenance.APPLE_MODELS.get(device or "", device or ""),
                "app_version": app_ver or "",
                "os_version": os_ver or "",
                "taken": taken,
                "confidence": confidence or "",
                "reasons": json.loads(reasons or "[]"),
                "estimate": {"date": est, "lo": lo, "hi": hi, "reason": est_reason or ""} if est else None,
                "uploaded": bool(key),
                "google_url": f"https://photos.google.com/photo/{urllib.parse.quote(key, safe='')}" if key else "",
                "album": album or "",
                "archived": bool(archived),
                "captioned": _ours(captioned) or "",
            }
        )
    account = _main_account("")
    refreshed = meta_get(f"library_refreshed:{account}") if account else None
    return {
        "source": source,
        "sources": list(provenance.SOURCES),
        "app_sources": list(provenance.APP_SOURCES),
        "labels": provenance.LABELS,
        "counts": {s: counts.get(s, 0) for s in provenance.SOURCES},
        "devices": [{"name": n, "files": c} for n, c in devices.most_common()],
        "checked": len(rows),
        "total": len(indexed),
        "estimated": sum(1 for r in rows if r[3] == "snapchat" and r[11]),
        "items": items,
        "more": max(0, len(picked) - limit),
        "settings": cfg,
        "account": account,
        "refreshed": float(refreshed) if refreshed else None,
        "refreshing": bool(account) and account in _refreshing,
        "needs_library": sources_state["needs_library"],
        "error": sources_state["error"],
    }


def _source_paths(names):
    return [str(MEDIA / n) for n in names if clean_folder(n)]


def sources_decide(names, source):
    """Correct the label of files: a source, or "auto" for the detected one."""
    if source != "auto" and source not in provenance.SOURCES:
        raise ValueError("unknown source")
    paths = _source_paths(names)
    if paths:
        _set_sources("decision", None if source == "auto" else source, paths)
        refresh_estimates()
        reclaim_wake.set()
    return {"updated": len(paths)}


def sources_accept_dates(names):
    """Use the estimated date for these Snapchat saves: it becomes their picked date, and ones
    already in Google wait in Fix Dates for the user to delete them there."""
    accepted = []
    for path in _source_paths(names):
        est = estimated_date(path)
        if est is None:
            continue
        if sq("UPDATE media_dates SET manual=? WHERE path=? AND inside=0 RETURNING path", (est, path)):
            accepted.append(rel(path))
    fixing = dates_fix(accepted)["queued"] if accepted else 0
    return {"accepted": len(accepted), "queued": fixing}


# ---------------------------------------------------------------- Snapchat memories
# scripts/snapchat_export.py turns Snapchat's Memories export into a folder (Photos/Snapchat) with
# the exact date, time and place written into every file, and a list of them
# (snapchat-memories.json). Those files are never backed up blindly: each memory is first looked
# for in the account's library (the same file, a camera-roll copy taken at the same moment, or one
# that looks the same). Memories Google has with the right date only go into the Snapchat album;
# ones it has with a wrong date are uploaded with Snapchat's date and Google's copy deleted (kept
# in Downloads unless the same file is on this server); the rest are uploaded. Nothing changes in
# Google until the user starts it on the Snapchat page. A copy of a memory here (a snap saved to
# the camera roll and backed up from the phone) gets the memory's date, and isn't uploaded as well
# when the memory takes its place.

SNAP_THUMB_WIDTH = 128
SNAP_PRINTS_PER_STEP = 120  # Google thumbnails fingerprinted per step (4 at a time)
SNAP_UPLOADS_PER_STEP = 4
SNAP_LOCAL_BYTES_PER_STEP = 512 * 1024**2  # files here read per step to find copies of memories
SNAP_ACTIONS = ("album", "replace", "upload", "skip")
SNAP_DIR = DATA / "snapchat"
snapchat_state = collections.defaultdict(lambda: {"current": None, "error": "", "wanted": 0, "prints": 0})
_snap = {"paths": frozenset(), "skip": frozenset()}  # memory files, and files here they stand in for
_snap_tried = {}  # path -> when a replace last found Google still had the old copy
_snap_dirty = set()  # accounts whose matches must be worked out again


def _snap_album():
    return get_settings()["sources"]["snapchat"]["album_name"] or "Snapchat"


def snapchat_load(manifest):
    """Bring snap_items in line with one snapchat-memories.json (read again when it changes).
    Returns the paths of its memories."""
    manifest = str(manifest)
    known = {p for (p,) in q("SELECT path FROM snap_items WHERE manifest=?", (manifest,))}
    try:
        stamp = str(os.stat(manifest).st_mtime)
    except OSError:
        return known
    if meta_get(f"snap_manifest:{manifest}") == stamp:
        return known
    try:
        data = snapchat.read_manifest(manifest)
    except snapchat.BadManifest as e:
        log(str(e), "error")
        return known
    rows = []
    for it in data["items"]:
        try:
            fst = os.stat(it["file"])
        except OSError:
            continue  # listed but not here (yet)
        rows.append(
            (str(it["file"]), manifest, it["id"], it["kind"], it["taken"], it["lat"], it["lon"], it["sha1"],
             it["orig_sha1"], it["width"], it["height"], str(it["poster"]) if it["poster"] else None,
             fst.st_size, fst.st_mtime, time.time())
        )  # fmt: skip
    with db_lock:
        # A file that changed gets its fingerprint and match worked out again.
        CONN.executemany(
            """INSERT INTO snap_items (path, manifest, mid, kind, taken, lat, lon, sha1, orig_sha1, width,
                   height, poster, size, mtime, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(path) DO UPDATE SET manifest=excluded.manifest, mid=excluded.mid, kind=excluded.kind,
               taken=excluded.taken, lat=excluded.lat, lon=excluded.lon, sha1=excluded.sha1,
               orig_sha1=excluded.orig_sha1, width=excluded.width, height=excluded.height, poster=excluded.poster,
               dhash=CASE WHEN snap_items.size=excluded.size AND snap_items.mtime=excluded.mtime
                     THEN snap_items.dhash END,
               match=CASE WHEN snap_items.size=excluded.size AND snap_items.mtime=excluded.mtime
                     THEN snap_items.match END,
               size=excluded.size, mtime=excluded.mtime, updated=excluded.updated""",
            rows,
        )
        CONN.commit()
    cache.bump()
    meta_set(f"snap_manifest:{manifest}", stamp)
    meta_set(f"snap_export:{manifest}", json.dumps(data["export"]))
    log(f"Snapchat memories: {len(rows)} in {rel(Path(manifest).parent)}", "info")
    _snap_dirty.add("*")
    reclaim_wake.set()
    return known | {r[0] for r in rows}


def snapchat_forget(manifests):
    """After a scan: rows of memory files gone from this server go (unless Google has them from
    GP Sync), and the lists the scan uses are made again."""
    gone = [p for p, state_ in q("SELECT path, state FROM snap_items") if state_ != "done" and not os.path.exists(p)]
    for i in range(0, len(gone), 500):
        part = gone[i : i + 500]
        q(f"DELETE FROM snap_items WHERE path IN ({','.join('?' * len(part))})", part)
    if gone:
        _snap_dirty.add("*")
    _snap_sets()


def _snap_sets():
    paths = frozenset(p for (p,) in q("SELECT path FROM snap_items"))
    # Files here that a memory stands in for: a copy not in Google (the memory is uploaded instead),
    # and the files of Google's copies (kept, or deleted as duplicates): never uploaded themselves.
    skip = {p for (p,) in q("SELECT local FROM snap_items WHERE local IS NOT NULL AND action='upload'")}
    skip |= {
        p
        for (p,) in q(
            """SELECT c.local FROM snap_copies c JOIN snap_items i ON i.path = c.path
               WHERE c.local IS NOT NULL AND i.action IN ('album','replace')"""
        )
    }
    _snap.update(paths=paths, skip=frozenset(skip))


def snapchat_time(path, sha1hex=None):
    """The moment of the Snapchat memory this file is a later copy of, or None. (A file Google keeps
    as the original of a memory keeps its own date.)"""
    if not _snap["paths"]:
        return None
    rows = q("SELECT MIN(taken) FROM snap_items WHERE local=? AND action='upload'", (path,))
    if rows and rows[0][0]:
        return float(rows[0][0])
    dedup = _b64_hash(bytes.fromhex(sha1hex)) if sha1hex else ""
    rows = q(
        """SELECT MIN(i.taken) FROM snap_copies c JOIN snap_items i ON i.path = c.path
           WHERE (c.local=? OR c.dedup=?) AND i.action='replace'""",
        (path, dedup),
    )
    return float(rows[0][0]) if rows and rows[0][0] else None


def _snap_replaces(sha1hex):
    """Google's copy of this file belongs to a Snapchat memory (kept with the right date, replaced by
    the memory, or deleted as a duplicate): the Snapchat page takes care of it."""
    return bool(
        sha1hex
        and q(
            """SELECT 1 FROM snap_copies c JOIN snap_items i ON i.path = c.path
               WHERE c.dedup=? AND i.action IN ('album','replace') LIMIT 1""",
            (_b64_hash(bytes.fromhex(sha1hex)),),
        )
    )


def snapchat_account(st=None):
    """The account memories go to: the one picked on the Snapchat page, else the main account of
    the folder they are in."""
    accs = [a for a, _ in accounts()]
    picked = meta_get("snap_account") or ""
    if picked in accs:
        return picked
    row = q("SELECT path FROM snap_items LIMIT 1")
    if row:
        found = folder_map(st).get(top_folder(row[0][0]))
        if found in accs:
            return found
    return accs[0] if accs else ""


def snapchat_paused(account):
    return meta_get(f"snap_paused:{account}") == "1"


def _snap_local_step():
    """Fingerprint files here that may be copies of memories (labelled Snapchat or saved from an
    app): their SHA-1 finds Google's copy, and photos also get a picture fingerprint. Returns True
    if there is more to do."""
    cands = sq(f"SELECT path, size, mtime FROM provenance WHERE {_EFFECTIVE} IN ('snapchat','app_export')")
    known = {p: (s, m) for p, s, m in q("SELECT path, size, mtime FROM snap_local")}
    read = 0
    for path, size, mtime in cands:
        if known.get(path) == (size, mtime) or path in _snap["paths"]:
            continue
        if read >= SNAP_LOCAL_BYTES_PER_STEP:
            return True
        try:
            sha1 = media.sha1_file(path).hex()
        except OSError:
            sha1 = ""
        fp = ""
        if sha1 and ext(path) in THUMB_EXTS:
            with contextlib.suppress(Exception):
                fp = snapchat.dhash_file(path)
        q("INSERT OR REPLACE INTO snap_local VALUES (?,?,?,?,?)", (path, size, mtime, sha1, fp))
        _snap_dirty.add("*")  # a new copy here: match again
        read += size or 0
    return False


def _snap_memories():
    return [
        {
            "path": p,
            "kind": k,
            "taken": t,
            "dedups": [_b64_hash(bytes.fromhex(h)) for h in (s, o) if h and re.fullmatch(r"[0-9a-f]{40}", h)],
            "dhash": d or "",
            "width": w,
            "height": h_,
        }
        for p, k, t, s, o, d, w, h_ in q(
            "SELECT path, kind, taken, sha1, orig_sha1, dhash, width, height FROM snap_items ORDER BY taken"
        )
    ]


def snapchat_match_step(account, make_client=None):
    """Find each memory in the account's library, a little at a time. Returns True if it did work
    (it is called again soon) and False once everything is matched."""
    rows = q("SELECT path, kind, poster, dhash, match FROM snap_items")
    if not rows:
        return False
    track = snapchat_state[account]
    refreshed = float(meta_get(f"library_refreshed:{account}") or 0)
    if not refreshed:
        if account not in _refresh_requests:
            _refresh_requests.append(account)  # the library is read first
        return False
    # Fingerprints of the memories: the photo, or a video's first frame (saved by the script).
    done = 0
    for path, kind, poster, dh, _match in rows:
        if dh is not None:
            continue
        source = poster if kind == "video" else path
        fp = ""
        if source and os.path.isfile(source):
            with contextlib.suppress(Exception):
                fp = snapchat.dhash_file(source)
        q("UPDATE snap_items SET dhash=? WHERE path=?", (fp, path))
        done += 1
        if done >= 200:
            return True
    if _snap_local_step():
        return True
    matched = float(meta_get(f"snap_matched:{account}") or 0)
    if all(r[4] is not None for r in rows) and matched >= refreshed and not (_snap_dirty & {"*", account}):
        return False
    listed = (
        _library_rows(
            account,
            f"SELECT media_key, dedup_key, type, utc_timestamp, width, height, make FROM remote_media WHERE {_OWNED}",
        )
        or []
    )
    library = [(k, d, t, _seconds(ts), w, h) for k, d, t, ts, w, h, _make in listed]
    cameras = {r[0] for r in listed if r[6]}  # taken by a camera: an original, never replaced
    memories = _snap_memories()
    # Google's thumbnails of the items that could be copies, fingerprinted once and kept.
    first = snapchat.match(memories, library, {})
    wanted = snapchat.thumbs_wanted(memories, library, first)
    prints = dict(q("SELECT media_key, dhash FROM snap_prints WHERE account=?", (account,)))
    missing = [k for k in wanted if k not in prints]
    track.update(wanted=len(wanted), prints=len(wanted) - len(missing))
    if missing:
        client = (make_client or _reclaim_client)(account)

        def one(key):
            try:
                return key, snapchat.dhash_bytes(client.api.get_thumbnail(key, width=SNAP_THUMB_WIDTH))
            except Exception as e:
                if outage_kind(e):
                    raise
                return key, ""  # not a picture Google can show: never a match

        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(4) as pool:
            got = list(pool.map(one, missing[:SNAP_PRINTS_PER_STEP]))
        with db_lock:
            CONN.executemany("INSERT OR REPLACE INTO snap_prints VALUES (?,?,?)", [(account, k, f) for k, f in got])
            CONN.commit()
        track["prints"] += len(got)
        notify_change()
        return True
    owned = {r[0] for r in library}
    prints = {k: f for k, f in prints.items() if k in owned}
    local_rows = q("SELECT path, sha1, dhash FROM snap_local WHERE sha1 != ''")
    in_google = {r[1] for r in library}
    here = {_b64_hash(bytes.fromhex(h)): p for p, h, _f in local_rows}  # Google's copy -> the file here
    local = {p: f for p, h, f in local_rows if f and _b64_hash(bytes.fromhex(h)) not in in_google}
    found = snapchat.match(memories, library, prints, local)
    _snap_save(account, found, here, cameras)
    meta_set(f"snap_matched:{account}", str(time.time()))
    _snap_dirty.discard("*")
    _snap_dirty.discard(account)
    _snap_sets()
    counts = collections.Counter(f["match"] for f in found.values())
    log(
        f"Snapchat memories in {account}: {counts['same_file'] + counts['same_moment'] + counts['looks_same']} "
        f"already in Google Photos, {counts['local'] + counts['none']} not yet",
        "info",
    )
    return True


def _snap_save(account, found, here, cameras=frozenset()):
    rows = q("SELECT path, taken, decided, action FROM snap_items WHERE COALESCE(state,'') NOT IN ('done','queued')")
    updates, copy_rows, paths = [], [], []
    for path, taken, decided, action in rows:
        f = found.get(path)
        if not f:
            continue
        auto = snapchat.action_for(f, taken, DATE_SLACK, cameras)
        copies = f["copies"]
        if decided and (action in ("album", "replace")) != bool(copies):
            decided, action = 0, auto  # what the user chose no longer fits what Google has
        best = min(copies, key=lambda c: snapchat.HOW_ORDER.index(c["how"])) if copies else None
        updates.append(
            (account, f["match"], best["key"] if best else None, best["dedup"] if best else None,
             best["taken"] if best else None, best["distance"] if best else None, f["local"],
             action if decided else auto, decided, int(any(c["key"] in cameras for c in copies)), time.time(), path)
        )  # fmt: skip
        paths.append(path)
        copy_rows += [
            (path, c["key"], c["dedup"], c["taken"], c["how"], c["distance"], int(c["key"] in cameras),
             here.get(c["dedup"]))
            for c in copies
        ]  # fmt: skip
    with db_lock:
        CONN.executemany(
            """UPDATE snap_items SET account=?, match=?, google_key=?, google_dedup=?, google_taken=?, distance=?,
               local=?, action=?, decided=?, original=?, updated=? WHERE path=?""",
            updates,
        )
        CONN.executemany("DELETE FROM snap_copies WHERE path=?", [(p,) for p in paths])
        CONN.executemany(
            """INSERT OR REPLACE INTO snap_copies (path, media_key, dedup, taken, how, distance, original, local)
               VALUES (?,?,?,?,?,?,?,?)""",
            copy_rows,
        )
        CONN.commit()
    cache.bump()
    notify_change()


def _snap_copies(path):
    return [
        {"key": k, "dedup": d, "taken": t, "original": bool(o), "favorite": bool(f), "state": st or "", "how": h}
        for k, d, t, o, f, st, h in q(
            "SELECT media_key, dedup, taken, original, favorite, state, how FROM snap_copies WHERE path=?", (path,)
        )
    ]


def _snap_upload(client, account, path, taken, size, track):
    """Upload one memory with its own moment as the date. Returns (media key, SHA-1, status)."""
    from rich.progress import Progress

    name = rel(path)
    track["current"] = {"key": name, "step": "hashing", "done": 0, "total": size}
    digest = media.sha1_file(path)
    phases = []

    def progress(ev):
        phases.append(ev.get("phase"))
        if ev.get("phase") == "uploading":
            track["current"] = {"key": name, "step": "uploading", "done": ev.get("bytes_completed") or 0,
                                "total": size}  # fmt: skip

    with Progress(disable=True) as bar, upload_date(client, taken):
        result = client._upload_file(
            Path(path), digest, bar, force_upload=False, use_quota=False, saver=False, progress_callback=progress
        )
    key = next(iter(result.values()), "")
    if not key:
        raise RuntimeError("Google returned no media key")
    return key, digest, "exists" if "skipped" in phases else "uploaded"


def _snap_keep_google_copy(client, account, key, dedup, track):
    """Before Google's copy of a memory is deleted: keep it in Downloads, unless the same file is
    on this server already."""
    if _local_copies(dedup) or q("SELECT 1 FROM snap_local WHERE sha1=?", (_digest(dedup).hex(),)):
        return
    row = _library_rows(
        account, "SELECT file_name, size_bytes, utc_timestamp FROM remote_media WHERE media_key=?", (key,)
    )
    name, size, taken = (row or [("", 0, 0)])[0]
    taken = _seconds(taken)
    got, _ = _download(client, account, key, dedup, name or f"{key}.bin", size, taken, SNAP_DIR / account, track)
    _keep_download(account, got, key, size, taken)


def snapchat_step(account, make_client=None):
    """Do what the user started on the Snapchat page, memory by memory: duplicates in Google are kept
    on this server and deleted there, then the memory stays (album) or is uploaded with its date
    (replace, upload). Every one goes into the album. Returns True if anything was done."""
    if snapchat_paused(account):
        return False
    rows = q(
        """SELECT path, action, kind, taken, size, mtime, favorite, archived, caption, local
           FROM snap_items WHERE state='queued' AND account=? ORDER BY taken LIMIT 300""",
        (account,),
    )
    later = q(
        "SELECT 1 FROM snap_items WHERE account=? AND state='done' AND in_album=0 AND new_key IS NOT NULL LIMIT 1",
        (account,),
    )
    if not rows and not later:
        return False
    track = snapchat_state[account]
    client = (make_client or _reclaim_client)(account)
    album = _snap_album()
    did, heavy, now = False, 0, time.time()
    try:
        for path, action, kind, taken, size, mtime, fav, arch, cap, local in rows:
            if heavy >= SNAP_UPLOADS_PER_STEP:
                break
            if now - _snap_tried.get(path, 0) < 100:
                continue
            name = rel(path)
            copies = _snap_copies(path)
            keep, drop = snapchat.plan(action, copies, taken, DATE_SLACK)
            started = time.monotonic()
            try:
                waiting = False
                for c in drop:
                    if c["state"] == "deleted":
                        continue
                    heavy += 1
                    if not _snap_drop_copy(client, account, path, c, track):
                        waiting = True
                        break
                if waiting:
                    _snap_tried[path] = time.time()
                    q("UPDATE snap_items SET error=? WHERE path=?",
                      ("Google hasn't deleted a copy yet: trying again soon", path))  # fmt: skip
                    continue
                if action == "album":
                    if keep is None:
                        raise RuntimeError("Google's copy is gone: look for the memories again")
                    if any(c["favorite"] for c in drop) and not keep["favorite"]:
                        with contextlib.suppress(Exception):  # a duplicate was a favourite: the copy kept is
                            client.api.set_favorite(keep["dedup"], True)
                    _snap_finish(path, account, keep["key"], "exists", "")
                    did = True
                    continue
                heavy += 1
                key, digest, status = _snap_upload(client, account, path, taken, size, track)
                if action == "replace":
                    _restore_details(client, _b64_hash(digest), fav, arch, cap)
                _snap_finish(path, account, key, status, digest.hex(), size, mtime, local)
                ms = int((time.monotonic() - started) * 1000)
                save_upload(name, kind, size, ms, status, account)
                if status == "uploaded":
                    add_stats(**({"videos": 1, "video_bytes": size, "video_ms": ms} if kind == "video"
                                 else {"photos": 1, "photo_bytes": size, "photo_ms": ms}))  # fmt: skip
                when = time.strftime("%Y-%m-%d %H:%M", time.gmtime(taken))
                log(f"Snapchat memory {name}: uploaded dated {when} UTC ({account})", "uploaded", name)
                did = True
            except Exception as e:
                if outage_kind(e):
                    raise
                msg = f"{type(e).__name__}: {e}"[:300]
                print(f"Snapchat: {name}: {msg}", file=sys.stderr, flush=True)
                q("UPDATE snap_items SET state='failed', error=? WHERE path=?", (msg, path))
            finally:
                track["current"] = None
        # Into the album, a batch at a time (and any an earlier album call missed).
        later = q(
            """SELECT path, new_key FROM snap_items WHERE account=? AND state='done' AND in_album=0
               AND new_key IS NOT NULL LIMIT ?""",
            (account, SOURCE_BATCH),
        )
        if later:
            added, left_out, ok = album_add(client, account, album, later)
            _snap_in_album(added)
            for path in left_out:  # Google can't find it now: leave it out instead of trying forever
                q("UPDATE snap_items SET in_album=-1 WHERE path=?", (path,))
            if not ok:
                track["error"] = f"Could not add to the album {album}"
                return did
            did = did or bool(added)
        track["error"] = ""
    except Exception as e:
        track["error"] = outage_kind(e) or f"{type(e).__name__}: {e}"[:300]
        print(f"Snapchat ({account}): {track['error']}", file=sys.stderr, flush=True)
    finally:
        if did:
            _snap_sets()
            notify_change()
    return did


def _snap_drop_copy(client, account, path, c, track):
    """Delete one of a memory's copies from Google, after keeping it on this server. True once Google
    no longer has it."""
    name = rel(path)
    if client.api.find_remote_media_by_hash(_digest(c["dedup"])):
        track["current"] = {"key": name, "step": "keeping", "done": 0, "total": 0}
        _snap_keep_google_copy(client, account, c["key"], c["dedup"], track)
        track["current"] = {"key": name, "step": "deleting", "done": 0, "total": 0}
        if not _delete_in_google(client, c["dedup"]):
            return False
        log(f"Deleted a copy of the Snapchat memory {name} from Google Photos ({account}); kept on this server",
            "deleted", name)  # fmt: skip
    q("UPDATE snap_copies SET state='deleted', error='' WHERE path=? AND media_key=?", (path, c["key"]))
    return True


def _snap_in_album(paths):
    for i in range(0, len(paths), 500):
        part = paths[i : i + 500]
        q(f"UPDATE snap_items SET in_album=1 WHERE path IN ({','.join('?' * len(part))})", part)


def _snap_finish(path, account, key, status, sha1hex, size=None, mtime=None, local=None):
    """Google has the memory now: remember it, and count the file (and the copies here it stands in
    for) as backed up."""
    if size is None:
        size, mtime = q("SELECT size, mtime FROM snap_items WHERE path=?", (path,))[0]
    now = time.time()
    q("UPDATE snap_items SET state='done', error='', new_key=?, updated=? WHERE path=?", (key, now, path))
    # sha1 "" when Google's item is a different file (a camera-roll copy): never fingerprinted later.
    q(
        """INSERT OR REPLACE INTO files (path, size, mtime, status, media_key, error, attempts, updated, account, sha1)
           VALUES (?,?,?,?,?,'',0,?,?,?)""",
        (path, size, mtime, status, key, now, account, sha1hex),
    )
    locals_ = {local} | {p for (p,) in q("SELECT local FROM snap_copies WHERE path=? AND local IS NOT NULL", (path,))}
    for lp in locals_:
        try:
            fst = os.stat(lp) if lp else None
        except OSError:
            continue
        if fst is None:
            continue
        q(
            """INSERT INTO files (path, size, mtime, status, media_key, error, attempts, updated, account, sha1)
               VALUES (?,?,?,'exists',?,'',0,?,?,NULL)
               ON CONFLICT(path) DO UPDATE SET media_key=excluded.media_key, status='exists',
               updated=excluded.updated""",
            (lp, fst.st_size, fst.st_mtime, key, now, account),
        )


def snapchat_work(account, make_client=None):
    """The Snapchat part of an account's Google work."""
    if not _snap["paths"] or account != snapchat_account():
        return False
    try:
        busy = snapchat_match_step(account, make_client)
    except Exception as e:
        snapchat_state[account]["error"] = outage_kind(e) or f"{type(e).__name__}: {e}"[:300]
        raise
    return snapchat_step(account, make_client) or busy


def snapchat_start(account, actions=None, names=None):
    """Start the work for memories: every one with these actions, or these files."""
    if account not in dict(accounts()):
        raise ValueError("unknown account")
    if not meta_get(f"snap_matched:{account}"):
        raise ValueError("Still looking for these memories in Google Photos")
    rows = q(
        """SELECT path, action FROM snap_items WHERE account=? AND match IS NOT NULL
           AND COALESCE(state,'') IN ('', 'failed') AND action != 'skip'""",
        (account,),
    )
    if actions is not None:
        rows = [r for r in rows if r[1] in actions]
    if names is not None:
        wanted = {str(MEDIA / n) for n in names if clean_folder(n)}
        rows = [r for r in rows if r[0] in wanted]
    # Favourite, archive and caption of Google's copies are read now: they are gone once deleted.
    keys = [k for (k,) in q("SELECT media_key FROM snap_copies WHERE path IN (SELECT value FROM json_each(?))",
                            (json.dumps([r[0] for r in rows]),))]  # fmt: skip
    details = _library_rows(
        account,
        "SELECT media_key, is_favorite, is_archived, caption FROM remote_media WHERE media_key IN "
        "(SELECT value FROM json_each(?))",
        (json.dumps(keys),),
    )
    info = {k: (f, a, c) for k, f, a, c in details or []}
    for path, _action in rows:
        got = [info[k] for (k,) in q("SELECT media_key FROM snap_copies WHERE path=?", (path,)) if k in info]
        for (k,) in q("SELECT media_key FROM snap_copies WHERE path=?", (path,)):
            if k in info:
                q("UPDATE snap_copies SET favorite=? WHERE path=? AND media_key=?", (int(bool(info[k][0])), path, k))
        fav = any(f for f, _a, _c in got)
        arch = bool(got) and all(a for _f, a, _c in got)
        cap = next((c for _f, _a, c in got if c and not _junk_caption(c)), "")
        q(
            """UPDATE snap_items SET state='queued', error='', favorite=?, archived=?, caption=?, updated=?
               WHERE path=?""",
            (int(fav), int(arch), cap, time.time(), path),
        )
    reclaim_wake.set()
    notify_change()
    return {"queued": len(rows)}


def snapchat_set_action(names, action):
    """Choose what happens to these memories: album or replace (Google has copies), upload (it has
    none), skip, or auto (the automatic choice)."""
    if action not in (*SNAP_ACTIONS, "auto"):
        raise ValueError("unknown action")
    n = 0
    for path in [str(MEDIA / x) for x in names if clean_folder(x)]:
        row = q("SELECT taken FROM snap_items WHERE path=? AND COALESCE(state,'') NOT IN ('done','queued')", (path,))
        if not row:
            continue
        (taken,) = row[0]
        copies = _snap_copies(path)
        if action in ("album", "replace") and not copies:
            continue  # Google has no copy of it
        if action == "upload" and copies:
            continue  # it would be a duplicate: replace takes Google's copies' place
        if action == "auto":
            cams = {c["key"] for c in copies if c["original"]}
            auto = snapchat.action_for({"copies": copies}, taken, DATE_SLACK, cams)
            q("UPDATE snap_items SET action=?, decided=0, state=NULL WHERE path=?", (auto, path))
        else:
            q("UPDATE snap_items SET action=?, decided=1, state=NULL WHERE path=?", (action, path))
        n += 1
    _snap_sets()
    return {"updated": n}


def snapchat_report(account="", action="", limit=120, offset=0):
    """What the Snapchat page shows: the export, how far the search in Google is, what will happen
    to each memory and how it's going."""
    account = account or snapchat_account()
    manifests = []
    for (manifest,) in q("SELECT DISTINCT manifest FROM snap_items"):
        try:
            export = json.loads(meta_get(f"snap_export:{manifest}") or "{}")
        except ValueError:
            export = {}
        manifests.append({"folder": rel(Path(manifest).parent), "export": export})
    rows = q(
        """SELECT path, kind, taken, lat, lon, match, distance, google_key, google_taken, local, action, decided,
                  state, error, new_key, account, dhash FROM snap_items ORDER BY taken DESC"""
    )
    counts = collections.defaultdict(lambda: {"total": 0, "done": 0, "queued": 0, "failed": 0, "photos": 0})
    matches = collections.Counter()
    for r in rows:
        if r[5] is None:
            continue
        c = counts[r[10] or "upload"]
        c["total"] += 1
        c["photos"] += r[1] == "photo"
        if r[12] in ("done", "queued", "failed"):
            c[r[12]] += 1
        matches[r[5]] += 1
    picked = [r for r in rows if r[5] is not None and (not action or r[10] == action)]
    all_copies = collections.defaultdict(list)
    for path, key, dedup, taken, how, dist, orig, fav, st in q(
        "SELECT path, media_key, dedup, taken, how, distance, original, favorite, state FROM snap_copies"
    ):
        all_copies[path].append({"key": key, "dedup": dedup, "taken": taken, "how": how, "distance": dist,
                                 "original": bool(orig), "favorite": bool(fav), "state": st or ""})  # fmt: skip
    # Duplicates: copies in Google that go (kept on this server first), and how many are gone already.
    dupes = {"total": 0, "deleted": 0}
    roles = {}
    for r in rows:
        if r[5] is None:
            continue
        keep, drop = snapchat.plan(r[10], all_copies.get(r[0], []), r[2], DATE_SLACK)
        roles[r[0]] = (keep, drop)
        dupes["total"] += len(drop)
        dupes["deleted"] += sum(c["state"] == "deleted" for c in drop)

    def url(key):
        return f"https://photos.google.com/photo/{urllib.parse.quote(key, safe='')}" if key else ""

    def copies_of(path):
        keep, drop = roles.get(path, (None, []))
        gone = {c["key"] for c in drop}
        return [
            {
                "key": c["key"],
                "taken": c["taken"],
                "how": c["how"],
                "distance": c["distance"],
                "camera": c["original"],
                "role": "keep" if keep is c else "delete" if c["key"] in gone else "leave",
                "state": c["state"],
                "url": url(c["key"]) if c["state"] != "deleted" else "",
            }
            for c in all_copies.get(path, [])
        ]

    items = [
        {
            "name": rel(r[0]),
            "kind": r[1],
            "taken": r[2],
            "place": [r[3], r[4]] if r[3] is not None else None,
            "match": r[5],
            "distance": r[6],
            "google": {"key": r[7], "taken": r[8], "url": url(r[7])} if r[7] else None,
            "copies": copies_of(r[0]),
            "local": rel(r[9]) if r[9] else "",
            "action": r[10],
            "decided": bool(r[11]),
            "state": r[12] or "",
            "error": r[13] or "",
            "url": url(r[14]),
        }
        for r in picked[offset : offset + limit]
    ]
    track = snapchat_state[account]
    refreshed = meta_get(f"library_refreshed:{account}")
    matched = meta_get(f"snap_matched:{account}")
    return {
        "account": account,
        "picked": meta_get("snap_account") or "",
        "album": _snap_album(),
        "manifests": manifests,
        "memories": len(rows),
        "fingerprinted": sum(1 for r in rows if r[16] is not None),
        "checked": sum(1 for r in rows if r[5] is not None),
        "thumbs": {"done": track["prints"], "total": track["wanted"]},
        "refreshed": float(refreshed) if refreshed else None,
        "matched": float(matched) if matched else None,
        "searching": bool(rows) and (not matched or any(r[5] is None for r in rows) or bool(_snap_dirty)),
        "counts": {a: counts[a] for a in SNAP_ACTIONS},
        "duplicates": dupes,
        "matches": dict(matches),
        "action": action,
        "items": items,
        "more": max(0, len(picked) - offset - limit),
        "paused": snapchat_paused(account),
        "held": state.get("snap_held", 0),
        "exporting": state.get("snap_exporting", []),
        "current": track["current"],
        "error": track["error"],
    }


def snapchat_pick_account(account):
    if account and account not in dict(accounts()):
        raise ValueError("unknown account")
    meta_set("snap_account", account)
    # Memories not done yet are looked for again in the account now picked.
    q("UPDATE snap_items SET match=NULL, action=NULL, decided=0, state=NULL, error='' "
      "WHERE COALESCE(state,'') != 'done'")  # fmt: skip
    _snap_dirty.add("*")
    _snap_sets()
    reclaim_wake.set()
    return {"ok": True, "account": snapchat_account()}


# ---------------------------------------------------------------- copy between Google accounts
# Everything one connected account owns is copied to another, newest first, one item at a time:
# download the original (checked against Google's fingerprint), upload it to the other account
# the free way, bring back favourite, archive and caption. Nothing is deleted anywhere.

COPY_DIR = DATA / "copy"
# Progress and the last error per (source, destination): each pair copies on its own worker.
copy_state = collections.defaultdict(lambda: {"current": None, "error": "", "jobs": {}})
copy_wake = threading.Event()
_OWNED = "origin = 'self' AND COALESCE(trash_timestamp, 0) = 0 AND COALESCE(is_locked, 0) = 0 AND is_canonical"


def _year(ts):
    return time.gmtime(ts).tm_year if ts else None


def copy_report(src, dst):
    """What account `src` has that `dst` doesn't, and how copying is going."""
    src_rows = _library_rows(
        src,
        f"""SELECT dedup_key, type, size_bytes, utc_timestamp, media_key, file_name FROM remote_media
            WHERE {_OWNED} ORDER BY utc_timestamp DESC""",
    )
    dst_keys = _library_rows(dst, "SELECT dedup_key FROM remote_media")
    have = {k for (k,) in dst_keys or []}
    jobs = dict(q("SELECT state, COUNT(*) FROM copyjobs WHERE src=? AND dst=? GROUP BY state", (src, dst)))
    handled = {k for (k,) in q("SELECT dedup_key FROM copyjobs WHERE src=? AND dst=?", (src, dst))}
    todo = {"photo": [0, 0], "video": [0, 0]}
    years = {}
    in_dst = 0
    dups = {}  # media key -> item: already in dst, or the same file twice in src. Never copied.
    seen = set()
    for dedup, kind, size, taken, key, name in src_rows or []:
        twice = dedup in seen
        seen.add(dedup)
        if dedup in handled and not twice:
            continue  # on the copy list (or copied: then the other account has it because of us)
        if dedup in have or twice:
            in_dst += not twice
            dups[key] = (key, name, RECLAIM_KINDS.get(kind, "photo"), size, _seconds(taken),
                         "Already in " + dst if not twice else "The same file twice in " + src)  # fmt: skip
            continue
        k = RECLAIM_KINDS.get(kind, "photo")
        todo[k][0] += 1
        todo[k][1] += size or 0
        y = _year(_seconds(taken))
        if y:
            years[y] = years.get(y, 0) + 1

    def refreshed(acc):
        v = meta_get(f"library_refreshed:{acc}")
        return float(v) if v else None

    done = q(
        "SELECT COUNT(*), SUM(size) FROM copyjobs WHERE src=? AND dst=? AND state IN ('copied', 'done')", (src, dst)
    )[0]
    queued = q("SELECT COUNT(*), SUM(size) FROM copyjobs WHERE src=? AND dst=? AND state='queued'", (src, dst))[0]
    # Copies timed from 1.5.4 on: how long they took, for the speed shown on the page.
    timed = q(
        """SELECT COUNT(*), SUM(finished - started), SUM(size) FROM copyjobs WHERE src=? AND dst=?
           AND state IN ('copied', 'done') AND started IS NOT NULL AND finished >= started""",
        (src, dst),
    )[0]
    recent = q(
        """SELECT media_key, name, kind, size, taken, state, error, updated FROM copyjobs
           WHERE src=? AND dst=? AND state != 'queued' ORDER BY updated DESC LIMIT 40""",
        (src, dst),
    )
    failed = q(
        """SELECT media_key, name, kind, size, taken, state, error, updated FROM copyjobs
           WHERE src=? AND dst=? AND state IN ('failed', 'counts') ORDER BY updated DESC LIMIT 100""",
        (src, dst),
    )
    for key, name, kind, size, taken in q(
        "SELECT media_key, name, kind, size, taken FROM copyjobs WHERE src=? AND dst=? AND state='exists'", (src, dst)
    ):  # Google said the other account had it when uploading
        dups.setdefault(key, (key, name, kind, size, taken, "Already in " + dst))
    dup_list = sorted(dups.values(), key=lambda d: -(d[4] or 0))

    def item(r):
        key, name, kind, size, taken, state_, error, updated = r
        return {"key": key, "name": name, "kind": kind, "size": size, "taken": taken, "state": state_,
                "error": error or "", "updated": updated}  # fmt: skip

    return {
        "src": src,
        "dst": dst,
        "listed": {"src": src_rows is not None, "dst": dst_keys is not None},
        "refreshed": {"src": refreshed(src), "dst": refreshed(dst)},
        "refreshing": bool(_refreshing & {src, dst}) or any(a in (src, dst) for a in _refresh_requests),
        "error": copy_state[(src, dst)]["error"] or reclaim_state[src]["error"] or reclaim_state[dst]["error"],
        "paused": meta_get("copy_paused") == "1",
        "current": next(iter(_copy_working(src, dst)), None),
        "working": _copy_working(src, dst),
        "src_total": len(src_rows or []),
        "in_dst": in_dst,
        "duplicates": {
            "count": len(dup_list),
            "bytes": sum(d[3] or 0 for d in dup_list),
            "items": [
                {
                    "key": k,
                    "name": n,
                    "kind": kd,
                    "size": sz,
                    "taken": t,
                    "state": "exists",
                    "error": why,
                    "updated": 0,
                }
                for k, n, kd, sz, t, why in dup_list[:60]
            ],
        },
        "todo": {k: {"count": c, "bytes": b} for k, (c, b) in todo.items()},
        "years": dict(sorted(years.items())),
        "jobs": {
            "queued": jobs.get("queued", 0),
            "queued_bytes": queued[1] or 0,
            "copied": jobs.get("copied", 0) + jobs.get("done", 0),
            "copied_bytes": done[1] or 0,
            "verified": jobs.get("done", 0),
            "exists": jobs.get("exists", 0),
            "failed": jobs.get("failed", 0),
            "counts": jobs.get("counts", 0),
            "timed": timed[0] or 0,
            "seconds": timed[1] or 0,
            "timed_bytes": timed[2] or 0,
        },
        "recent": [item(r) for r in recent],
        "failed": [item(r) for r in failed],
        "free_space": shutil.disk_usage(DATA).free if DATA.exists() else 0,
        "check": check_info(dst),
        "mirror": mirror_info(src, dst),
    }


def copy_candidates(src, dst, kinds=("photo", "video"), from_year=None, to_year=None, query="", offset=0, limit=60):
    """Browse distinct, eligible source items without putting them on the copy queue."""
    rows = _library_rows(
        src,
        f"""SELECT media_key, dedup_key, file_name, type, size_bytes, utc_timestamp
            FROM remote_media WHERE {_OWNED} ORDER BY utc_timestamp DESC, media_key""",
    )
    target = _library_rows(dst, "SELECT dedup_key FROM remote_media")
    if rows is None or target is None:
        raise ValueError("Read both libraries before copying")
    have = {k for (k,) in target}
    handled = {k for (k,) in q("SELECT dedup_key FROM copyjobs WHERE src=? AND dst=?", (src, dst))}
    selected = []
    for key, dedup, name, kind, size, taken in rows:
        k = RECLAIM_KINDS.get(kind, "photo")
        t = _seconds(taken)
        y = _year(t)
        if dedup in have or dedup in handled or k not in kinds:
            continue
        if (from_year and (not y or y < from_year)) or (to_year and (not y or y > to_year)):
            continue
        if query and query.casefold() not in (name or "").casefold():
            continue
        handled.add(dedup)
        selected.append({"key": key, "name": name or "", "kind": k, "size": size or 0, "taken": t})
    return {
        "total": len(selected),
        "bytes": sum(i["size"] for i in selected),
        "items": selected[offset : offset + limit],
    }


def copy_summary():
    """Copy accounts across every pair, for the status: items waiting, copies Google hasn't
    confirmed yet, and whether copying is paused."""
    counts = dict(q("SELECT state, COUNT(*) FROM copyjobs GROUP BY state"))
    return {"queued": counts.get("queued", 0), "checking": counts.get("copied", 0),
            "done": counts.get("done", 0) + counts.get("exists", 0),
            "paused": meta_get("copy_paused") == "1"}  # fmt: skip


def copy_start(src, dst, kinds=("photo", "video"), from_year=None, to_year=None, keys=None, skip=()):
    """Queue everything `src` owns that `dst` doesn't have yet (with the chosen filters). `skip`:
    source media keys never to queue (copies GP Sync made into `src` from `dst`)."""
    if meta_get("copy_paused") == "1" and q("SELECT 1 FROM copyjobs WHERE state='counts' LIMIT 1"):
        raise ValueError("Copying is paused because Google counted a copy against storage; review and resume first")
    src_rows = _library_rows(
        src,
        f"""SELECT media_key, dedup_key, file_name, type, size_bytes, utc_timestamp, is_favorite, is_archived,
                   caption FROM remote_media WHERE {_OWNED} ORDER BY utc_timestamp DESC, media_key""",
    )
    if src_rows is None:
        raise ValueError("Read the library of the account to copy from first")
    dst_rows = _library_rows(dst, "SELECT dedup_key FROM remote_media")
    if dst_rows is None:
        raise ValueError("Read the library of the account to copy to first")
    have = {k for (k,) in dst_rows}
    handled = {k for (k,) in q("SELECT dedup_key FROM copyjobs WHERE src=? AND dst=?", (src, dst))}
    if keys is not None:
        if not isinstance(keys, list) or len(keys) > 5000 or any(not isinstance(k, str) for k in keys):
            raise ValueError("Select at most 5,000 valid items at a time")
        keys = set(keys)
    now = time.time()
    batch = []
    for key, dedup, name, kind, size, taken, fav, arch, cap in src_rows:
        k = RECLAIM_KINDS.get(kind, "photo")
        t = _seconds(taken)
        y = _year(t)
        if dedup in have or dedup in handled or k not in kinds or (keys is not None and key not in keys):
            continue  # duplicates (already there) are never copied
        if key in skip:
            continue
        if (from_year and (not y or y < from_year)) or (to_year and (not y or y > to_year)):
            continue
        handled.add(dedup)  # the same file twice: copy it once
        batch.append((src, dst, key, dedup, name, k, size, t, int(bool(fav)), int(bool(arch)), cap or "", now))
    with db_lock:
        before = CONN.total_changes
        CONN.executemany(
            """INSERT OR IGNORE INTO copyjobs (src, dst, media_key, dedup_key, name, kind, size, taken, favorite,
                                              archived, caption, state, updated)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,'queued',?)""",
            batch,
        )
        queued = CONN.total_changes - before
        CONN.commit()
    cache.bump()
    copy_wake.set()
    if queued:
        log(f"Copying {queued} items from {src} to {dst}", "account")
    return {"queued": queued}


# ---- keep two accounts in sync

MIRROR_EVERY = 3600  # seconds between comparing the libraries of a pair kept in sync


def mirror_get(src, dst):
    """Keep in sync for a pair: "off", "one" (src → dst) or "both"."""
    on = {(a, b) for a, b in q("SELECT src, dst FROM mirrors WHERE (src=? AND dst=?) OR (src=? AND dst=?)",
                                (src, dst, dst, src))}  # fmt: skip
    return "both" if len(on) == 2 else "one" if (src, dst) in on else "reverse" if on else "off"


def mirror_set(src, dst, mode):
    """Turn Keep in sync on (one way or both ways) or off for two accounts. Turning it on compares the
    libraries straight away."""
    if mode not in ("off", "one", "both"):
        raise ValueError("Choose off, one way or both ways")
    q("DELETE FROM mirrors WHERE (src=? AND dst=?) OR (src=? AND dst=?)", (src, dst, dst, src))
    pairs = [] if mode == "off" else [(src, dst)] if mode == "one" else [(src, dst), (dst, src)]
    now = time.time()
    for a, b in pairs:
        q("INSERT INTO mirrors (src, dst, added, last_run) VALUES (?, ?, ?, NULL)", (a, b, now))
    arrow = {"off": "stopped", "one": f"{src} → {dst}", "both": f"{src} ↔ {dst}"}[mode]
    log(f"Keep in sync: {arrow}", "account")
    mirror_wake.set()
    return {"mode": mirror_get(src, dst)}


def mirror_info(src, dst):
    """Keep in sync for the Copy accounts page: the mode, and when the libraries are compared."""
    rows = q("SELECT src, dst, last_run, last_queued, error FROM mirrors WHERE (src=? AND dst=?) OR (src=? AND dst=?)",
             (src, dst, dst, src))  # fmt: skip
    last = max((r[2] or 0 for r in rows), default=0) or None
    return {
        "mode": mirror_get(src, dst),
        "last_run": last,
        "next_run": (min((r[2] or 0) for r in rows) + MIRROR_EVERY) if rows else None,
        "queued": sum(r[3] or 0 for r in rows if r[2] == last) if last else 0,
        "error": next((r[4] for r in rows if r[4]), ""),
    }


def mirror_run(src, dst, make_client=None):
    """Compare both libraries and queue what `src` has and `dst` lacks. Items already in both are
    skipped by checksum, and so are copies GP Sync made into `src` from `dst` (no copying back).
    The copies then go through the usual copy queue: the file on this server first, 4 at a time,
    confirmed by the checker. Returns how many items were queued."""
    make_client = make_client or _reclaim_client
    connected = dict(accounts())
    try:
        if src not in connected or dst not in connected:
            raise RuntimeError(f"{src if src not in connected else dst} is no longer connected")
        for acc in (src, dst):
            update_library(make_client(acc), acc)
        came_back = {k for (k,) in q("SELECT new_key FROM copyjobs WHERE src=? AND dst=? AND new_key IS NOT NULL",
                                     (dst, src))}  # fmt: skip
        queued = copy_start(src, dst, skip=came_back)["queued"]
        q("UPDATE mirrors SET last_run=?, last_queued=?, error='' WHERE src=? AND dst=?",
          (time.time(), queued, src, dst))  # fmt: skip
        return queued
    except Exception as e:
        msg = outage_kind(e) or f"{type(e).__name__}: {e}"[:300]
        print(f"Keep in sync {src} → {dst}: {msg}", file=sys.stderr, flush=True)
        # Tried again in a quarter of the usual wait.
        q("UPDATE mirrors SET last_run=?, error=? WHERE src=? AND dst=?",
          (time.time() - MIRROR_EVERY * 3 / 4, msg, src, dst))  # fmt: skip
        return 0
    finally:
        notify_change()


def mirror_step(make_client=None, now=None):
    """Compare the pairs that are due (every MIRROR_EVERY, or straight away once turned on). Nothing
    is compared while copying is paused. Returns True if a pair was compared."""
    now = time.time() if now is None else now
    if meta_get("copy_paused") == "1":
        return False
    due = q("SELECT src, dst FROM mirrors WHERE last_run IS NULL OR last_run <= ?", (now - MIRROR_EVERY,))
    for src, dst in due:
        mirror_run(src, dst, make_client)
    if due:
        copy_wake.set()
    return bool(due)


mirror_wake = threading.Event()


def mirror_worker():
    while True:
        mirror_wake.clear()
        try:
            mirror_step()
        except Exception as e:
            print(f"Keep in sync: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        mirror_wake.wait(60)


def copy_stop(src, dst):
    """Take the items that haven't started off the list (copies already made stay)."""
    n = q("DELETE FROM copyjobs WHERE src=? AND dst=? AND state='queued' RETURNING 1", (src, dst))
    return {"removed": len(n)}


def copy_retry(src, dst):
    n = q(
        "UPDATE copyjobs SET state='queued', error='' WHERE src=? AND dst=? AND state='failed' RETURNING 1", (src, dst)
    )
    copy_wake.set()
    return {"queued": len(n)}


def _set_copy(src, dst, key, state_, error="", **cols):
    sets = "".join(f", {c}=?" for c in cols)
    q(
        f"UPDATE copyjobs SET state=?, error=?, updated=?{sets} WHERE src=? AND dst=? AND media_key=?",
        (state_, error, time.time(), *cols.values(), src, dst, key),
    )
    notify_change()


def copy_step(make_client=None, pair=None):
    """Copy the next items (of one (source, destination) pair, or any): up to Settings'
    reclaim.workers at once, the same "Items at once" as Free storage. The checker (check_step)
    confirms the copies are free, apart from this. Returns True if there was something to do."""
    make_client = make_client or _reclaim_client
    if meta_get("copy_paused") == "1":
        return False
    src_, dst_ = pair or (None, None)
    rows = q(
        """SELECT src, dst, media_key, dedup_key, name, size, taken, favorite, archived, caption FROM copyjobs
           WHERE state='queued' AND (? IS NULL OR (src=? AND dst=?)) ORDER BY taken DESC LIMIT ?""",
        (src_, src_, dst_, get_settings()["reclaim"]["workers"]),
    )
    if not rows:
        return False
    connected = dict(accounts())
    for src, dst, *_ in rows:
        if src not in connected or dst not in connected:  # an account was removed: wait, don't fail it all
            copy_state[(src, dst)]["error"] = (
                f"{src if src not in connected else dst} is no longer connected. Copying is paused."
            )
            meta_set("copy_paused", "1")
            return False
    if len(rows) == 1:
        results = [_copy_one(rows[0], make_client)]
    else:  # several at once, each on its own thread
        with concurrent.futures.ThreadPoolExecutor(len(rows), thread_name_prefix="account copy") as pool:
            results = list(pool.map(lambda r: _copy_one(r, make_client), rows))
    check_lanes.wake(dict.fromkeys(r[1] for r in rows))  # the checker confirms the copies are free
    return any(results)


def _copy_working(src, dst):
    """Progress of every item being copied between two accounts."""
    return [dict(t["current"]) for t in list(copy_state[(src, dst)]["jobs"].values()) if t.get("current")]


def _copy_one(row, make_client):
    """Copy one item: download it from the source, check it, upload it to the destination.
    Returns False when Google or the internet is down (it stays queued)."""
    src, dst, key, dedup, name, size, taken, fav, arch, cap = row
    state_ = copy_state[(src, dst)]
    held = _stop_check(lambda: meta_get("copy_paused") == "1")
    track = state_["jobs"][key] = {"current": None, "stop": held}
    folder = COPY_DIR / f"{dst}-{key}"  # the same item can be copied to two accounts at once
    started = time.time()
    try:
        source, target = make_client(src), make_client(dst)
        track["current"] = {"key": key, "step": "downloading", "done": 0, "total": size, "name": name}
        path, have = _download(source, src, key, dedup, name, size, taken, folder=folder, track=track)
        new_key = _reupload(target, path, have, size, key, track, when=taken)
        if new_key is None:
            _set_copy(src, dst, key, "exists")
            log(f"Already in {dst}: {name}", "exists")
        else:
            _restore_details(target, have, fav, arch, cap)
            _set_copy(src, dst, key, "copied", new_key=new_key, started=started, finished=time.time())
            log(f"Copied {name} from {src} to {dst} ({fmt_bytes(size)})", "copied")
        state_["error"] = ""
        return True
    except Paused:  # stopped mid-download: it stays queued
        return False
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"[:300]
        print(f"Account copy: {name}: {msg}", file=sys.stderr, flush=True)
        if outage := outage_kind(e):  # Google or the internet is down: keep it queued, try again later
            state_["error"] = outage
            return False
        _set_copy(src, dst, key, "failed", msg)
        return True
    finally:
        track["current"] = None
        state_["jobs"].pop(key, None)
        shutil.rmtree(folder, ignore_errors=True)


copy_lanes = Lanes("Account copy", lambda pair: copy_step(pair=pair))


def copy_worker():
    """A lane per (source, destination) pair with work: copies to different accounts run side by side."""
    while True:
        copy_wake.clear()
        try:
            pairs = q("SELECT DISTINCT src, dst FROM copyjobs WHERE state IN ('queued', 'copied')")
            copy_lanes.wake([(s_, d) for s_, d in pairs])
        except Exception as e:
            print(f"Account copy: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        copy_wake.wait(30)


# ---------------------------------------------------------------- the checker
# Free uploads (Free storage) and copies (Copy accounts) are confirmed with Google once it lists
# them: one library read per account covers every item waiting there. This runs on a lane of its
# own per account, so downloads, uploads and new items never wait for a library read.

CHECK_EVERY = 60  # seconds between library reads while items wait to be confirmed
CHECK_MAX_WAIT = 15 * 60  # longest wait after Google didn't answer (it doubles from CHECK_EVERY)
CHECK_SETTLE = 30  # Google needs a moment after an upload before it lists the item
check_state = collections.defaultdict(lambda: {"next": 0.0, "wait": CHECK_EVERY, "error": "", "last": None})


def _waiting_checks(account):
    """Free uploads and copies into this account that Google hasn't confirmed yet."""
    freed = q(
        """SELECT media_key, name, size, taken, local, new_key, checks, keep, quota, uploaded_at FROM reclaim
           WHERE state='checking' AND account=?""",
        (account,),
    )
    copies = q(
        "SELECT src, media_key, new_key, name, checks, finished FROM copyjobs WHERE dst=? AND state='copied'",
        (account,),
    )
    return freed, copies


def check_info(account):
    """How checking with Google is going in one account, for the pages."""
    freed, copies = _waiting_checks(account)
    st = check_state[account]
    return {"waiting": len(freed) + len(copies), "last": st["last"], "next": st["next"] or None,
            "error": st["error"]}  # fmt: skip


def check_step(account, make_client=None, now=None, force=False):
    """Confirm, in one library read, that this account's recent free uploads and copies don't count
    against storage. At most once a minute; after a failed read the wait doubles (up to 15 minutes).
    `force`: read now, and check items uploaded a moment ago too (tests). Returns True if it read."""
    now = time.time() if now is None else now
    freed, copies = _waiting_checks(account)
    if not freed and not copies:
        check_state[account].update(error="", wait=CHECK_EVERY)
        return False
    st = check_state[account]
    settled = now - (0 if force else CHECK_SETTLE)
    if not force and (now < st["next"] or all((r[-1] or 0) > settled for r in [*freed, *copies])):
        return False
    try:
        client = (make_client or _reclaim_client)(account)
        update_library(client, account)
    except Exception as e:
        st["wait"] = min(st["wait"] * 2, CHECK_MAX_WAIT)
        st["next"] = now + st["wait"]
        st["error"] = f"{type(e).__name__}: {e}"[:300]
        print(f"Checker: couldn't read {account}'s library: {st['error']}", file=sys.stderr, flush=True)
        return False
    st.update(next=now + CHECK_EVERY, wait=CHECK_EVERY, error="", last=now)

    def charged(new_key):
        rows = _library_rows(account, "SELECT quota_charged_bytes FROM remote_media WHERE media_key=?", (new_key,))
        return None if not rows else rows[0][0]

    done = []
    for key, name, size, taken, local, new_key, checks, keep, quota, uploaded in freed:
        if (uploaded or 0) > settled:
            continue
        quota_ = charged(new_key)
        if quota_ is None:
            if (checks or 0) >= 10:
                _set_reclaim(account, key, "failed", "Uploaded, but Google hasn't listed it yet. Check later")
            else:
                _set_reclaim(account, key, "checking", checks=(checks or 0) + 1)
            continue
        if quota_:
            _set_reclaim(account, key, "counts", "Google counted this upload too, so nothing was freed")
            log(f"Free upload didn't work for {account}: stopped freeing storage", "error")
            alert("Freeing storage stopped", f"Google counted the new upload of {name} against storage.")
            continue
        if keep:  # the user wants the copy: it goes to Downloads
            _keep_download(account, Path(local), new_key, size, taken)
        shutil.rmtree(Path(local).parent, ignore_errors=True)
        _set_reclaim(account, key, "done", local="", finished=time.time())
        log(f"Freed {fmt_bytes(quota or size)} of Google storage: re-uploaded {name} for free ({account})", "freed")
        done.append(new_key)
    album = get_settings()["reclaim"]
    if done and album["album"]:
        add_to_album(client, account, album["album_name"], done)
    for src, key, new_key, name, checks, finished in copies:
        if (finished or 0) > settled:
            continue
        quota_ = charged(new_key)
        if quota_:
            _set_copy(src, account, key, "counts", "Google counted this copy against storage")
            meta_set("copy_paused", "1")
            log(f"Copy to {account} paused: Google counted {name} against storage", "error")
            alert("Account copy paused", f"Google counted the copy of {name} against the storage of {account}.")
        elif quota_ is not None:
            _set_copy(src, account, key, "done")
        elif (checks or 0) >= 10:
            _set_copy(src, account, key, "done", "Copied, but Google hasn't listed it yet")
        else:
            q("UPDATE copyjobs SET checks=COALESCE(checks, 0)+1 WHERE src=? AND dst=? AND media_key=?",
              (src, account, key))  # fmt: skip
    notify_change()
    return True


check_lanes = Lanes("Google check", check_step)


def check_worker():
    """Keeps a checker lane going for every account with items waiting to be confirmed."""
    while True:
        try:
            accs = q(
                """SELECT account FROM reclaim WHERE state='checking'
                   UNION SELECT dst FROM copyjobs WHERE state='copied'"""
            )
            check_lanes.wake([a for (a,) in accs])
        except Exception as e:
            print(f"Checker: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        time.sleep(30)


# ---------------------------------------------------------------- uploads from phones


def upload_folders():
    """Named folders inside the upload folder (not the year folders)."""
    try:
        return sorted(
            e.name
            for e in os.scandir(UPLOAD_DIR)
            if e.is_dir() and not e.name.startswith(".") and not re.fullmatch(r"\d{4}", e.name)
        )
    except OSError:
        return []


def upload_folder_name():
    """Where phone uploads show up among the backed-up folders (e.g. Photos/Uploads), or ""."""
    if str(UPLOAD_DIR).startswith(str(MEDIA) + os.sep):
        return rel(UPLOAD_DIR)
    # Mounted separately (as on Umbrel): find the same folder inside the media folders.
    with contextlib.suppress(OSError):
        for top in list_top_folders():
            candidate = MEDIA / top / UPLOAD_DIR.name
            if candidate.is_dir() and os.path.samefile(candidate, UPLOAD_DIR):
                return f"{top}/{UPLOAD_DIR.name}"
    return ""


def upload_info():
    ready = UPLOAD_DIR.is_dir() and os.access(UPLOAD_DIR, os.W_OK)
    free = shutil.disk_usage(UPLOAD_DIR).free if ready else 0
    shown = upload_folder_name()
    synced = bool(shown) and any(shown.split("/")[0] == top and acc for top, acc in folder_map().items())
    return {
        "ready": ready,
        "folder": shown,
        "free": free,
        "max_bytes": MAX_UPLOAD,
        "synced": synced,
        "folders": upload_folders() if ready else [],
        "chunk_bytes": 8 * 1024**2,
    }


def clean_name(name):
    name = str(name).replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if c.isprintable() and c not in '<>:"|?*').strip(" .")
    return name[-180:]


def _owned(path):
    with contextlib.suppress(OSError):  # not root, or a file system without owners
        os.chown(path, *UPLOAD_OWNER)


CHUNKS = ".chunks"  # unfinished resumable uploads (a dot folder, so never scanned)
UPLOAD_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")


def _when(mtime_ms):
    try:
        when = float(mtime_ms) / 1000 if mtime_ms else time.time()
        time.gmtime(when)
        return when
    except (TypeError, ValueError, OverflowError, OSError):
        return time.time()


def _upload_folder(when, folder=""):
    """UPLOAD_DIR/<folder>/ if the user named one, else UPLOAD_DIR/<year>/<month>/."""
    folder = clean_name(folder)
    if folder:
        parts = [folder]
    else:
        local = time.gmtime(when + get_settings()["tz_offset_min"] * 60)
        parts = [time.strftime("%Y", local), time.strftime("%m", local)]
    d = UPLOAD_DIR
    for part in parts:
        d = d / part
        if not d.exists():
            d.mkdir(exist_ok=True)
            _owned(d)
    return d


def _copy(stream, f, length, digest=None):
    left = length
    while left:
        chunk = stream.read(min(left, 1024 * 1024))
        if not chunk:
            raise ConnectionError("upload cut off")
        if digest:
            digest.update(chunk)
        f.write(chunk)
        left -= len(chunk)


def _check_upload(name, total):
    if not name or ext(name) not in EXTS:
        return 400, {"ok": False, "error": "Only photos and videos can be uploaded"}
    if not 0 < total <= MAX_UPLOAD:
        return 400, {"ok": False, "error": "Empty or too large"}
    info = upload_info()
    if not info["ready"]:
        return 503, {"ok": False, "error": "The upload folder is not available"}
    if info["free"] < total + 512 * 1024**2:
        return 507, {"ok": False, "error": f"Not enough free space on {HOST_NAME}"}
    return None


def chunk_offset(upload_id):
    """Bytes already received for a resumable upload (0 if none)."""
    if not UPLOAD_ID.fullmatch(upload_id or ""):
        return 0
    part = UPLOAD_DIR / CHUNKS / f"{upload_id}.part"
    return part.stat().st_size if part.exists() else 0


def receive_chunk(stream, length, upload_id, offset, total, name, mtime_ms=None, folder=""):
    """One piece of a resumable upload. The last piece saves the file like receive_upload."""
    name = clean_name(name)
    if not UPLOAD_ID.fullmatch(upload_id or ""):
        return 400, {"ok": False, "error": "Bad upload id"}
    if not name or ext(name) not in EXTS:  # every piece: the last one names the saved file
        return 400, {"ok": False, "error": "Only photos and videos can be uploaded"}
    problem = _check_upload(name, total) if offset == 0 else None
    if problem:
        return problem
    if not 0 < length <= 64 * 1024**2 or offset + length > total or total > MAX_UPLOAD:
        return 400, {"ok": False, "error": "Bad piece"}
    chunks = UPLOAD_DIR / CHUNKS
    chunks.mkdir(exist_ok=True)
    part = chunks / f"{upload_id}.part"
    have = part.stat().st_size if part.exists() else 0
    if offset != have:  # resume from what actually arrived
        return 409, {"ok": False, "offset": have, "error": "Resume from offset"}
    with open(part, "ab") as f:
        try:
            _copy(stream, f, length)
        except BaseException:
            f.truncate(have)  # drop a half piece so the next try starts cleanly
            raise
    if have + length < total:
        return 200, {"ok": True, "offset": have + length}
    when = _when(mtime_ms)
    return _finish_upload(part, name, total, when, media.sha1_file(part).hex(), folder)


def clean_chunks(max_age=2 * 86400):
    d = UPLOAD_DIR / CHUNKS
    if d.is_dir():
        for f in d.glob("*.part"):
            if time.time() - f.stat().st_mtime > max_age:
                f.unlink(missing_ok=True)


def receive_upload(stream, length, name, mtime_ms=None, folder=""):
    """Save one file sent from a phone into UPLOAD_DIR/<year>/<month>/ (or a named folder).

    Returns (http code, result)."""
    name = clean_name(name)
    problem = _check_upload(name, length)
    if problem:
        return problem
    when = _when(mtime_ms)
    target_dir = _upload_folder(when, folder)
    tmp = target_dir / f".{name}.{os.getpid()}.{threading.get_ident()}.part"  # dot files are not scanned
    digest = hashlib.sha1()
    try:
        with open(tmp, "wb") as f:
            _copy(stream, f, length, digest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return _finish_upload(tmp, name, length, when, digest.hexdigest(), folder)


def _finish_upload(tmp, name, length, when, sha1_hex, folder=""):
    """Move a fully received file into place, unless the same photo is already here."""
    try:
        target_dir = _upload_folder(when, folder)
        # Only a copy still on this server counts: one deleted here must be stored again.
        dup = next((p for (p,) in q("SELECT path FROM files WHERE sha1=?", (sha1_hex,)) if os.path.isfile(p)), None)
        if dup:
            tmp.unlink()
            return 200, {"ok": True, "duplicate": rel(dup), "name": name}
        stem, dot, suffix = name.rpartition(".")
        with upload_lock:  # two phones sending IMG_0001.jpg at once must not overwrite each other
            target, n = target_dir / name, 1
            while target.exists():
                if target.stat().st_size == length and media.sha1_file(target).hex() == sha1_hex:
                    tmp.unlink()
                    return 200, {"ok": True, "duplicate": rel(target), "name": name}
                target = target_dir / f"{stem} ({n}){dot}{suffix}"
                n += 1
            os.replace(tmp, target)
        _owned(target)
        os.utime(target, (when, when))
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    saved = rel(target)
    log(f"Received {saved} from a phone ({fmt_bytes(length)})", "received", saved)
    wake.set()
    return 200, {"ok": True, "saved": saved, "name": name}


def about():
    packages = ("gpmc", "pyturso", "pillow", "pillow-heif", "requests", "rich", "redis")
    return {
        "version": VERSION,
        "backend_version": BACKEND_VERSION,
        "host": HOST_NAME,
        "commit": os.environ.get("BUILD_COMMIT", ""),
        "built": os.environ.get("BUILD_DATE", ""),
        "python": sys.version.split()[0],
        "sqlite": sqlite3.sqlite_version,
        "stats_engine": STATS_ENGINE,
        "packages": {n: _dist_version(n) for n in packages},
        "started": STARTED,
        "uptime": time.time() - STARTED,
        "media": str(MEDIA),
        "upload_dir": str(UPLOAD_DIR),
        "interval_min": INTERVAL // 60,
    }


# ---------------------------------------------------------------- web server

CONTENT_TYPES = {
    "html": "text/html; charset=utf-8",
    "js": "text/javascript; charset=utf-8",
    "css": "text/css; charset=utf-8",
    "svg": "image/svg+xml",
    "png": "image/png",
    "webp": "image/webp",
    "ico": "image/x-icon",
    "json": "application/json",
    "webmanifest": "application/manifest+json",
    "woff2": "font/woff2",
    "txt": "text/plain; charset=utf-8",
}


def static_file(path):
    """A file of the built dashboard, or None. Never leaves STATIC_DIR."""
    name = urllib.parse.unquote(path).lstrip("/") or "index.html"
    try:
        target = (STATIC_DIR / name).resolve()
        target.relative_to(STATIC_DIR.resolve())
    except (ValueError, OSError):
        return None
    return target if target.is_file() else None


SESSION_COOKIE = "gps_auth"


def session_token():
    """The sign-in cookie's value: changes when PASSWORD changes, so old sign-ins stop working."""
    return hmac.new(PASSWORD.encode(), b"gp-sync sign-in", "sha256").hexdigest()


def signed_in(headers):
    if not PASSWORD:
        return True
    cookie = http.cookies.SimpleCookie()
    with contextlib.suppress(http.cookies.CookieError):
        cookie.load(headers.get("Cookie", ""))
    if SESSION_COOKIE in cookie and hmac.compare_digest(
        cookie[SESSION_COOKIE].value.encode(), session_token().encode()
    ):
        return True
    kind, _, value = headers.get("Authorization", "").partition(" ")
    if kind.lower() == "basic":  # scripts: curl -u any:PASSWORD
        try:
            given = base64.b64decode(value).decode().partition(":")[2]
        except (ValueError, UnicodeDecodeError):
            return False
        return hmac.compare_digest(given.encode(), PASSWORD.encode())
    return False


# A self-contained page (no scripts or files), so it works before signing in and in installed apps.
LOGIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#08090c"><title>Sign in · GP Sync</title>
<style>
*{box-sizing:border-box}body{margin:0;min-height:100dvh;display:grid;place-items:center;padding:24px;
font:16px/1.4 -apple-system,BlinkMacSystemFont,"SF Pro Text","Google Sans Text",Roboto,system-ui,sans-serif;
color:#f5f5f7;
background:#08090c radial-gradient(48rem 34rem at 0% 0%,rgb(10 132 255/.18),transparent 62%)}
form{width:100%;max-width:340px;padding:28px;border:1px solid rgb(255 255 255/.09);border-radius:22px;
background:rgb(30 31 37/.62);-webkit-backdrop-filter:blur(24px);backdrop-filter:blur(24px);
box-shadow:inset 0 1px 0 rgb(255 255 255/.1),0 20px 50px -25px #000}
h1{margin:0 0 4px;font-size:22px}p{margin:0 0 20px;color:#8e8e93;font-size:14px}
input{width:100%;height:46px;padding:0 14px;border-radius:12px;border:1px solid rgb(255 255 255/.12);
background:rgb(255 255 255/.06);color:inherit;font-size:16px;outline:none}input:focus{border-color:#0a84ff}
button{margin-top:14px;width:100%;height:46px;border:0;border-radius:999px;color:#fff;font-size:16px;
font-weight:600;background:#0a84ff;cursor:pointer}button:active{transform:scale(.98)}
.e{min-height:20px;margin-top:10px;color:#ff6961;font-size:14px}
</style></head><body><form method="post" action="login">
<h1>GP Sync</h1><p>Enter the password to open the dashboard.</p>
<input type="password" name="password" autocomplete="current-password" placeholder="Password" required autofocus>
<button type="submit">Sign in</button><div class="e">{error}</div></form></body></html>"""


WRONG_PASSWORD_DELAY = 1  # seconds


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json", cache="no-cache"):
        data = body if isinstance(body, bytes) else body.encode()
        etag = ""
        if code == 200 and self.command == "GET":
            # The browser keeps the answer and asks "changed?"; unchanged answers cost 0 bytes.
            etag = '"' + hashlib.blake2b(data, digest_size=8).hexdigest() + '"'
            if etag in self.headers.get("If-None-Match", ""):
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", cache)
                self.end_headers()
                return
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache if code == 200 else "no-store")
        if etag:
            self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(data)

    def json(self, obj, code=200):
        self.send(code, json.dumps(obj))

    def send_file(self, target, ctype, download=False):
        """Send an original file, with Range support so videos can be played and seeked."""
        size = target.stat().st_size
        start, end, code = 0, size - 1, 200
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", "").strip())
        if m and (m[1] or m[2]):
            if m[1]:
                start, end = int(m[1]), min(int(m[2]) if m[2] else size - 1, size - 1)
            else:
                start = max(0, size - int(m[2]))
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            code = 206
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "private, max-age=3600")
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if download:
            quoted = urllib.parse.quote(target.name)
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quoted}")
        self.end_headers()
        left = end - start + 1
        try:
            with open(target, "rb") as f:
                f.seek(start)
                while left > 0 and (block := f.read(min(left, 1024 * 1024))):
                    self.wfile.write(block)
                    left -= len(block)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the viewer moved on

    def stream(self):
        """Server-sent events: live status, pushed when something happens (and at least every 2 s)."""
        if not stream_slots.acquire(blocking=False):
            self.send(503, "{}")
            return
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.close_connection = True
            viewers["streams"] += 1
            watched_event.set()
            while True:
                self.wfile.write(b"data: " + status_body() + b"\n\n")
                self.wfile.flush()
                with changed:
                    changed.wait(timeout=2)
                time.sleep(0.25)  # bursts of events become one update
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            viewers["streams"] = max(0, viewers["streams"] - 1)
            viewers["last_request"] = time.time()
            stream_slots.release()

    def cached(self, key, ttl, make):
        """Answer with JSON from the cache (made by make() when missing or stale)."""
        self.send(200, cache.remember(key, ttl, lambda: json.dumps(make()).encode()))

    def image(self, kind, name, make):
        """A thumbnail or preview: from the cache, else made (and cached) by make(name)."""
        key = f"{kind}:{name}"
        data = cache.get(key)
        if data is None:
            try:
                target = make(name)
            except Exception as e:
                print(f"{kind.title()} failed for {name}: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
                target = None
            data = target.read_bytes() if target else b""
            media.release_memory()
            # Missing ones (videos not uploaded yet) are asked for again after a while. Found ones
            # are files on disk the system already keeps in memory, so only Redis stores them.
            if not data or cache.remote():
                cache.set(key, data, 86400 if data else 300)
        if data:
            # ?v=<file time> addresses one version of the picture: phones may keep it for good.
            versioned = "v=" in self.path
            self.send(
                200,
                data,
                "image/webp",
                "private, max-age=31536000, immutable" if versioned else "private, max-age=86400",
            )
        else:
            self.send(404, "{}")

    def allowed(self):
        """With PASSWORD set, every request needs the sign-in cookie (or HTTP Basic, for scripts).

        Pages go to the sign-in page; API calls get 401 (without a browser password popup)."""
        if not PASSWORD or signed_in(self.headers):
            return True
        path = self.path.partition("?")[0]
        if self.command in ("GET", "HEAD") and path in ("/", "/index.html"):
            self.send_response(303)
            self.send_header("Location", "login")
        else:
            self.send_response(401)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.close_connection = True
        return False

    def sign_in_page(self, error=False):
        self.send(
            200,
            LOGIN_PAGE.replace("{error}", "Wrong password" if error else ""),
            "text/html; charset=utf-8",
            "no-store",
        )

    def sign_in(self, body):
        given = (urllib.parse.parse_qs(body).get("password") or [""])[0]
        self.send_response(303)
        if PASSWORD and hmac.compare_digest(given.encode(), PASSWORD.encode()):
            self.send_header(
                "Set-Cookie", f"{SESSION_COOKIE}={session_token()}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax"
            )
            self.send_header("Location", "./")
        else:
            time.sleep(WRONG_PASSWORD_DELAY)  # slows down guessing
            self.send_header("Location", "login?e=1")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def sign_out(self):
        self.send_response(303)
        self.send_header("Set-Cookie", f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
        self.send_header("Location", "login" if PASSWORD else "./")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self):
        """Like GET for the dashboard files (some browsers and install checks use HEAD)."""
        if not self.allowed():
            return
        path = self.path.partition("?")[0]
        target = static_file(path) if not path.startswith("/api/") else None
        if not target:
            self.send_response(404 if not path.startswith("/api/") else 405)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(ext(target.name), "application/octet-stream"))
        self.send_header("Content-Length", str(target.stat().st_size))
        self.send_header("Cache-Control", "public, max-age=31536000, immutable" if "/assets/" in path else "no-cache")
        self.end_headers()

    def do_GET(self):
        if self.path.partition("?")[0] == "/login":
            self.sign_in_page("e=1" in self.path)
            return
        if not self.allowed():
            return
        path, _, qs = self.path.partition("?")
        query = urllib.parse.parse_qs(qs)

        def arg(name, default, lo=None, hi=None):
            raw = query.get(name, [default])[0]
            if lo is None:
                return raw
            try:
                return max(lo, min(hi, int(raw)))
            except ValueError:
                return default

        if path.startswith("/api/"):
            saw_viewer()
        if path == "/api/status":
            self.send(200, status_body())
        elif path == "/api/processes":
            days, tz = arg("days", 371, 1, 3660), arg("tz", 0, -840, 840)
            self.cached(f"processes:{days}:{tz}", 60, lambda: process_activity(days, tz))
        elif path == "/api/history":
            days, tz = arg("days", 365, 1, 3660), arg("tz", 0, -840, 840)
            self.cached(f"history:{days}:{tz}", 60, lambda: history(days, tz))
        elif path == "/api/folders":
            folder = clean_folder(arg("path", ""))
            if folder is None:
                self.json({"error": "bad folder"}, 400)
                return
            group = "devices" if arg("group", "folders") == "devices" else "folders"
            days, tz = arg("days", 30, 1, 366), arg("tz", 0, -840, 840)
            key = f"folders:{group}:{folder}:{days}:{tz}:{state['last_scan']}"
            self.cached(key, 30, lambda: folder_report(folder, group, days, tz))
        elif path == "/api/storage/freed":
            account = arg("account", "")
            if account not in dict(accounts()):
                self.json({"error": "unknown account"}, 400)
            else:
                self.json(freed_report(account))
        elif path == "/api/storage":
            account = arg("account", "") or busy_account("storage")
            kind = arg("kind", "all")
            kind = kind if kind in ("all", "photo", "video") else "all"
            self.json(storage_report(account, kind=kind) if account else {"account": "", "listed": False, "items": []})
        elif path == "/api/copy":
            accs = [a for a, _ in accounts()]
            busy = q("SELECT src, dst FROM copyjobs WHERE state='queued' GROUP BY src, dst ORDER BY COUNT(*) DESC")
            busy = next(((a, b) for a, b in busy if a in accs and b in accs), None)  # the pair at work
            src = arg("src", "") or (busy[0] if busy and not arg("dst", "") else accs[0] if accs else "")
            dst = arg("dst", "") or (busy[1] if busy and busy[0] == src else next((a for a in accs if a != src), ""))
            if src in accs and dst in accs and src != dst:
                self.json(copy_report(src, dst))
            else:
                self.json({"src": src, "dst": dst, "accounts": accs, "ready": False})
        elif path == "/api/copy/items":
            accs = dict(accounts())
            src, dst = arg("src", ""), arg("dst", "")
            if src not in accs or dst not in accs or src == dst:
                self.json({"error": "Pick two different connected accounts"}, 400)
                return
            try:
                kinds = [k for k in arg("kinds", "photo,video").split(",") if k in ("photo", "video")]
                years = [int(arg(k, "")) if arg(k, "") else None for k in ("from_year", "to_year")]
                result = copy_candidates(
                    src, dst, kinds, *years, arg("q", "")[:200], arg("offset", 0, 0, 10**9), arg("limit", 60, 1, 200)
                )
                self.json(result)
            except ValueError as e:
                self.json({"error": str(e)}, 400)
        elif path == "/api/storage/thumb":
            account, key = arg("account", ""), arg("key", "")
            self.image("gthumb", f"{account}\n{key}", google_thumb)
        elif path == "/api/folders/files":
            folder = clean_folder(arg("path", ""))
            if folder is None:
                self.json({"error": "bad folder"}, 400)
                return
            args = (folder, arg("status", "all"), arg("kind", "all"), arg("sort", "new"),
                    arg("offset", 0, 0, 10**9), arg("limit", 60, 1, 500),
                    arg("day", ""), arg("tz", 0, -840, 840), arg("group", "folders"))  # fmt: skip
            try:
                self.cached(f"ffiles:{json.dumps(args)}:{state['last_scan']}", 30, lambda: folder_files(*args))
            except ValueError as e:
                self.json({"error": str(e)}, 400)
        elif path == "/api/totals":
            self.cached("totals", 60, totals)
        elif path == "/api/settings":
            self.json({**public_settings(), "copy_paused": meta_get("copy_paused") == "1"})
        elif path == "/api/files":
            args = (
                arg("q", ""),
                arg("status", "all"),
                arg("folder", ""),
                arg("sort", "new"),
                arg("limit", 60, 1, 500),
                arg("offset", 0, 0, 10**9),
            )
            self.cached("files:" + json.dumps(args), 60, lambda: list_files(*args))
        elif path == "/api/dates":
            self.json(dates_report(arg("account", "")))
        elif path == "/api/sources":
            self.json(sources_report(arg("source", ""), arg("limit", 600, 1, 5000)))
        elif path == "/api/snapchat":
            action = arg("action", "")
            self.json(
                snapchat_report(
                    arg("account", ""),
                    action if action in SNAP_ACTIONS else "",
                    arg("limit", 120, 1, 1000),
                    arg("offset", 0, 0, 10**9),
                )
            )
        elif path == "/api/duplicates":
            self.cached("duplicates", 300, duplicates)
        elif path == "/api/activity":
            self.json(activity(arg("kind", "all"), arg("q", ""), arg("before", "") or None, arg("limit", 100, 1, 1000)))
        elif path == "/api/resources":
            self.json(resources_json())
        elif path == "/api/health":
            self.json(health())
        elif path == "/api/diagnostics":
            body = json.dumps(diagnostics(arg("hours", 24, 1, 24 * 30)), indent=1).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header(
                "Content-Disposition", f'attachment; filename="gp-sync-diagnostics-{time.strftime("%Y%m%d-%H%M")}.json"'
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/thumb":
            self.image("thumb", arg("path", ""), thumbnail)
        elif path == "/api/insights":
            tz = arg("tz", 0, -840, 840)
            self.cached(f"insights:{tz}", 60, lambda: insights(tz))
        elif path == "/api/metrics":
            hours = arg("hours", 24, 1, SAMPLE_DAYS * 24)
            self.cached(f"metrics:{hours}", 60, lambda: metrics(hours))
        elif path == "/api/logs":
            self.json(
                app_logs(arg("level", "all"), arg("q", ""), arg("before", "") or None, arg("limit", 200, 1, 2000))
            )
        elif path == "/api/about":
            self.json(about())
        elif path == "/api/upload":
            if query.get("id"):
                self.json({"offset": chunk_offset(arg("id", ""))})
            else:
                self.json(upload_info())
        elif path == "/api/file":
            self.json(file_info(arg("path", "")))
        elif path == "/api/preview":
            self.image("preview", arg("path", ""), preview)
        elif path == "/api/media":
            target = safe_media_path(arg("path", ""))
            if target:
                self.send_file(
                    target, MEDIA_TYPES.get(ext(target.name), "application/octet-stream"), arg("download", "")
                )
            else:
                self.send(404, "{}")
        elif path == "/api/downloads":
            self.json(downloads_report(arg("account", "")))
        elif path == "/api/downloads/file":
            target = download_path(arg("id", 0, 0, 2**62))
            if target:
                inline = arg("inline", "") == "1"  # played in the browser (Freed page), not saved
                self.send_file(target, MEDIA_TYPES.get(ext(target.name), "application/octet-stream"), not inline)
            else:
                self.send(404, "{}")
        elif path == "/api/stream":
            self.stream()
        elif path.startswith("/api/"):
            self.send(404, "{}")
        elif target := static_file(path):
            # Vite gives built assets hashed names, so they can be cached for good.
            cache = "public, max-age=31536000, immutable" if "/assets/" in path else "no-cache"
            self.send(200, target.read_bytes(), CONTENT_TYPES.get(ext(target.name), "application/octet-stream"), cache)
        else:
            self.send(404, "{}")

    def do_POST(self):
        path0 = self.path.partition("?")[0]
        if path0 in ("/login", "/logout"):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(min(max(n, 0), 4096)).decode(errors="replace")
            self.sign_in(body) if path0 == "/login" else self.sign_out()
            return
        if not self.allowed():
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        path, _, qs = self.path.partition("?")
        if path == "/api/upload":  # streamed straight to disk, not read into memory
            query = urllib.parse.parse_qs(qs)

            def a(k, default=""):
                return query.get(k, [default])[0]

            try:
                if a("id"):
                    code, result = receive_chunk(
                        self.rfile, length, a("id"), int(a("offset", "0")), int(a("total", "0")),
                        a("name"), a("mtime", None), a("folder"),
                    )  # fmt: skip
                else:
                    code, result = receive_upload(self.rfile, length, a("name"), a("mtime", None), a("folder"))
            except Exception as e:
                code, result = 500, {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
            if code != 200:
                self.close_connection = True  # the rest of the body may be unread
            self.json(result, code)
            return
        if path == "/share":  # shared from a phone without the service worker: open the Upload page
            self.close_connection = True
            self.send_response(303)
            self.send_header("Location", "./#/upload")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if not 0 <= length <= 1024 * 1024:
            self.send(413, "{}")
            self.close_connection = True
            return
        body = self.rfile.read(length).decode(errors="replace")
        if path in ("/auth", "/api/auth"):
            try:
                email, error = save_account((urllib.parse.parse_qs(body).get("auth") or [""])[0]), ""
            except ValueError as e:
                email, error = None, str(e)
            if path == "/auth":  # plain form post
                self.send_response(303)
                self.send_header("Location", "./")
                self.end_headers()
            else:
                error = error or "That doesn't look like a login line (it starts with androidId=)"
                self.json({"ok": bool(email), "email": email, "error": "" if email else error}, 200 if email else 400)
        elif path == "/api/accounts/remove":
            try:
                req = json.loads(body or "{}")
                email = str(req.get("email", ""))
            except (ValueError, AttributeError):
                self.json({"ok": False, "error": "bad request"}, 400)
                return
            self.json({"ok": remove_account(email)})
        elif path == "/api/settings":
            try:
                new = update_settings(json.loads(body or "{}"))
            except (ValueError, TypeError) as e:
                self.json({"ok": False, "error": str(e)}, 400)
                return
            log("Settings saved", "info")
            wake.set()
            self.json({"ok": True, "settings": {**new, "notify": public_settings()["notify"]}})
        elif path == "/api/notify/test":
            cfg = get_settings()["notify"]
            if not notify.channels(cfg):
                self.json({"ok": False, "results": {}, "error": "No alert channel is set up"}, 400)
                return
            results = notify.send_now(cfg, "GP Sync", "Test alert: alerts are working.", "bell")
            self.json({"ok": all(v == "ok" for v in results.values()), "results": results})
        elif path == "/api/notify/weekly":
            msg = weekly_summary(force=True)
            self.json({"ok": bool(msg), "message": msg or "Set up an alert channel first"})
        elif path == "/api/health/check-login":
            self.json(check_logins())
        elif path == "/api/sync":
            wake.set()
            self.json({})
        elif path == "/api/pause":
            if PAUSE_FILE.exists():
                PAUSE_FILE.unlink()
                log("Resumed uploads", "resume")
                wake.set()
            else:
                PAUSE_FILE.touch()
                if state["phase"] != "uploading":
                    state["phase"] = "paused"
            notify_change()
            self.json({})
        elif path == "/api/pause-all":
            try:
                paused = bool(json.loads(body or "{}").get("paused"))
            except (ValueError, AttributeError):
                self.json({"error": "bad request"}, 400)
                return
            pause_all(paused)
            self.json({"ok": True, "all_paused": all_paused()})
        elif path.startswith("/api/copy/"):
            try:
                req = json.loads(body or "{}")
                src, dst = str(req.get("src", "")), str(req.get("dst", ""))
            except (ValueError, AttributeError):
                self.json({"error": "bad request"}, 400)
                return
            accs = dict(accounts())
            if path == "/api/copy/pause":
                meta_set("copy_paused", "1" if req.get("paused") else "0")
                for t in copy_state.values():
                    t["error"] = ""
                copy_wake.set()
                self.json({"ok": True})
            elif src not in accs or dst not in accs or src == dst:
                self.json({"error": "Pick two different connected accounts"}, 400)
            elif path == "/api/copy/refresh":
                _refresh_requests.extend([src, dst])
                reclaim_wake.set()
                self.json({"ok": True})
            elif path == "/api/copy/start":
                try:
                    kinds = [k for k in req.get("kinds", ["photo", "video"]) if k in ("photo", "video")]
                    years = [int(req[k]) if req.get(k) else None for k in ("from_year", "to_year")]
                    self.json(copy_start(src, dst, kinds, *years, keys=req.get("keys")))
                except (ValueError, TypeError) as e:
                    self.json({"error": str(e)}, 400)
            elif path == "/api/copy/mirror":
                try:
                    self.json(mirror_set(src, dst, str(req.get("mode", ""))))
                except ValueError as e:
                    self.json({"error": str(e)}, 400)
            elif path == "/api/copy/stop":
                self.json(copy_stop(src, dst))
            elif path == "/api/copy/retry":
                self.json(copy_retry(src, dst))
            else:
                self.send(404, "{}")
        elif path == "/api/downloads/delete":
            try:
                req = json.loads(body or "{}")
                ids = [int(i) for i in req.get("ids") or []][:5000]
                self.json(downloads_delete(ids, str(req.get("account") or "") or None))
            except (ValueError, TypeError, AttributeError):
                self.json({"error": "bad request"}, 400)
        elif path.startswith("/api/storage/"):
            try:
                req = json.loads(body or "{}")
                account, keys = str(req.get("account", "")), [str(k) for k in req.get("keys") or []][:1000]
            except (ValueError, AttributeError):
                self.json({"error": "bad request"}, 400)
                return
            if account not in dict(accounts()):
                self.json({"error": "unknown account"}, 400)
            elif path == "/api/storage/refresh":
                _refresh_requests.append(account)
                reclaim_wake.set()
                self.json({"ok": True})
            elif path == "/api/storage/download":
                keep = bool(req.get("keep"))
                if req.get("kind") and not keys:  # everything of a kind (photo, video or all)
                    try:
                        self.json(reclaim_queue_all(account, str(req["kind"]), keep))
                    except ValueError as e:
                        self.json({"error": str(e)}, 400)
                else:
                    self.json(reclaim_queue(account, keys, keep))
            elif path == "/api/storage/cancel":
                self.json(reclaim_cancel(account, keys))
            elif path == "/api/storage/pause":
                meta_set(f"reclaim_paused:{account}", "1" if req.get("paused") else "0")
                reclaim_state[account]["error"] = ""
                reclaim_wake.set()
                notify_change()
                self.json({"ok": True, "paused": reclaim_paused(account)})
            elif path == "/api/storage/check":
                if req.get("unblock"):  # the user read why it stopped and wants to go on
                    q("UPDATE reclaim SET state='counted' WHERE account=? AND state='counts'", (account,))
                reclaim_wake.set()
                self.json({"ok": True})
            else:
                self.send(404, "{}")
        elif path.startswith("/api/dates/"):
            try:
                req = json.loads(body or "{}")
                names = [str(n) for n in req.get("paths") or []]
                if path != "/api/dates/recheck":
                    names = names[:1000]
            except (ValueError, AttributeError):
                self.json({"error": "bad request"}, 400)
                return
            try:
                if path == "/api/dates/fix":
                    self.json(dates_fix(names))
                elif path == "/api/dates/cancel":
                    self.json(dates_cancel(names))
                elif path == "/api/dates/recheck":
                    account = str(req.get("account", "")) or _main_account("")
                    self.json(dates_recheck(account, None if req.get("all") is True else names))
                elif path == "/api/dates/set":
                    self.json(dates_set(str(req.get("path", "")), req.get("when")))
                elif path == "/api/dates/pause":
                    account = str(req.get("account", "")) or _main_account("")
                    if account not in dict(accounts()):
                        raise ValueError("unknown account")
                    meta_set(f"dates_paused:{account}", "1" if req.get("paused") else "0")
                    reclaim_wake.set()
                    notify_change()
                    self.json({"ok": True, "paused": dates_paused(account)})
                elif path == "/api/dates/refresh":
                    account = str(req.get("account", "")) or _main_account("")
                    if account not in dict(accounts()):
                        raise ValueError("unknown account")
                    _refresh_requests.append(account)
                    reclaim_wake.set()
                    self.json({"ok": True})
                else:
                    self.send(404, "{}")
            except (ValueError, TypeError) as e:
                self.json({"error": str(e)}, 400)
        elif path.startswith("/api/snapchat/"):
            try:
                req = json.loads(body or "{}")
                names = [str(n) for n in req.get("paths") or []][:5000]
                account = str(req.get("account", "")) or snapchat_account()
            except (ValueError, AttributeError, TypeError):
                self.json({"error": "bad request"}, 400)
                return
            try:
                if path == "/api/snapchat/start":
                    actions = req.get("actions")
                    actions = [a for a in actions if a in SNAP_ACTIONS] if isinstance(actions, list) else None
                    self.json(snapchat_start(account, actions, names if req.get("paths") else None))
                elif path == "/api/snapchat/action":
                    self.json(snapchat_set_action(names, str(req.get("action", ""))))
                elif path == "/api/snapchat/account":
                    self.json(snapchat_pick_account(str(req.get("account", ""))))
                elif path == "/api/snapchat/pause":
                    if account not in dict(accounts()):
                        raise ValueError("unknown account")
                    meta_set(f"snap_paused:{account}", "1" if req.get("paused") else "0")
                    reclaim_wake.set()
                    notify_change()
                    self.json({"ok": True, "paused": snapchat_paused(account)})
                elif path == "/api/snapchat/recheck":
                    if account not in dict(accounts()):
                        raise ValueError("unknown account")
                    q("UPDATE snap_items SET state=NULL, error='' WHERE state='failed'")
                    _snap_dirty.add(account)
                    _refresh_requests.append(account)  # read the library, then look again
                    reclaim_wake.set()
                    self.json({"ok": True})
                else:
                    self.send(404, "{}")
            except (ValueError, TypeError) as e:
                self.json({"error": str(e)}, 400)
        elif path.startswith("/api/sources/"):
            try:
                req = json.loads(body or "{}")
                names = [str(n) for n in req.get("paths") or []][:5000]
            except (ValueError, AttributeError, TypeError):
                self.json({"error": "bad request"}, 400)
                return
            try:
                if path == "/api/sources/decide":
                    self.json(sources_decide(names, str(req.get("source", ""))))
                elif path == "/api/sources/accept-dates":
                    self.json(sources_accept_dates(names))
                elif path == "/api/sources/settings":
                    changes = req.get("settings")
                    if not isinstance(changes, dict):
                        raise ValueError("settings must be a JSON object")
                    new = update_settings({"sources": changes})
                    reclaim_wake.set()
                    self.json({"ok": True, "settings": new["sources"]})
                else:
                    self.send(404, "{}")
            except (ValueError, TypeError) as e:
                self.json({"error": str(e)}, 400)
        elif path == "/api/retry":
            try:
                one = (json.loads(body or "{}") if body.strip().startswith("{") else {}).get("path")
            except ValueError:
                self.json({"ok": False, "error": "bad request"}, 400)
                return
            if one:
                self.json({"ok": retry_file(one)})
            else:
                q("UPDATE files SET attempts=0 WHERE status='failed'")
                q("UPDATE copies SET attempts=0 WHERE status='failed'")
                wake.set()
                self.json({})
        else:
            self.send(404, "{}")


def main():
    os.chdir(DATA)  # gpmc writes failed_skipped_files.log to the working folder
    sys.stdout = LogTee(sys.stdout, "info")
    sys.stderr = LogTee(sys.stderr, "error")
    # One plain line per message from gpmc and the libraries it uses. Set up first, so gpmc keeps it:
    # its own handler wraps every message into many narrow lines when there is no terminal.
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    threading.Thread(target=log_writer, daemon=True).start()
    migrate_accounts()
    migrate_stats()
    split_pause()
    init_events()
    backfill_stats()
    log(f"GP Sync {VERSION}: watching {MEDIA} every {INTERVAL // 60} min", "scan")
    threading.Thread(target=sampler, daemon=True).start()
    threading.Thread(target=hasher, daemon=True).start()
    threading.Thread(target=latency_sampler, daemon=True).start()
    threading.Thread(target=minute_sampler, daemon=True).start()
    with contextlib.suppress(Exception):  # gpmc sets a signal handler on import: main thread only
        import gpmc.client  # noqa: F401
    threading.Thread(target=reclaim_worker, daemon=True).start()
    threading.Thread(target=copy_worker, daemon=True).start()
    threading.Thread(target=check_worker, daemon=True).start()
    threading.Thread(target=mirror_worker, daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    worker()  # gpmc installs signal handlers, so it must run on the main thread


if __name__ == "__main__":
    main()
