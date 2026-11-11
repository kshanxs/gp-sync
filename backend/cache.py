"""A small cache for API answers.

Kept in the app's own memory (bounded, fastest, nothing extra to run). Set REDIS_URL to
use a Redis or Valkey server instead, for example when several copies share one cache.
Every database write bumps a generation number that is part of each key, so cached
answers never outlive the data they came from.
"""

import collections
import os
import sys
import threading
import time

URL = os.environ.get("REDIS_URL", "")
PREFIX = "gps:"
MEMORY_LIMIT = 8 * 1024 * 1024  # bytes kept in memory
RETRY_AFTER = 30  # seconds to wait before trying Redis again after an error

_lock = threading.Lock()
_mem = collections.OrderedDict()  # key -> (expires, bytes)
_mem_bytes = 0
_client = None
_down_until = 0.0
_gen = 0
stats = {"hits": 0, "misses": 0, "errors": 0}


def generation():
    return _gen


def bump():
    """Data changed: answers cached before now are stale."""
    global _gen
    _gen += 1


def _redis():
    """The Redis client, or None while Redis is not configured or not reachable."""
    global _client, _down_until
    if not URL or time.monotonic() < _down_until:
        return None
    if _client is None:
        try:
            import redis

            _client = redis.Redis.from_url(
                URL, socket_timeout=0.5, socket_connect_timeout=0.5, health_check_interval=60
            )
        except Exception as e:  # missing package or a bad URL: use memory
            _fail(e)
            return None
    return _client


def _fail(e):
    global _down_until
    stats["errors"] += 1
    if time.monotonic() >= _down_until:
        print(f"Cache: Redis unavailable ({type(e).__name__}: {e}); using memory", file=sys.stderr, flush=True)
    _down_until = time.monotonic() + RETRY_AFTER


def remote():
    """True when a Redis server holds the cache (worth keeping bigger things like thumbnails)."""
    return _redis() is not None


def get(key):
    r = _redis()
    if r is not None:
        try:
            value = r.get(PREFIX + key)
        except Exception as e:
            _fail(e)
        else:
            stats["hits" if value is not None else "misses"] += 1
            return value
    with _lock:
        hit = _mem.get(key)
        if hit and hit[0] > time.monotonic():
            _mem.move_to_end(key)
            stats["hits"] += 1
            return hit[1]
        stats["misses"] += 1
        return None


def set(key, value, ttl):
    """Store bytes for `ttl` seconds."""
    global _mem_bytes
    r = _redis()
    if r is not None:
        try:
            r.set(PREFIX + key, value, ex=max(1, int(ttl)))
            return
        except Exception as e:
            _fail(e)
    if len(value) > MEMORY_LIMIT // 8:
        return
    with _lock:
        old = _mem.pop(key, None)
        if old:
            _mem_bytes -= len(old[1])
        _mem[key] = (time.monotonic() + ttl, value)
        _mem_bytes += len(value)
        while _mem_bytes > MEMORY_LIMIT and _mem:
            _, (_, dropped) = _mem.popitem(last=False)
            _mem_bytes -= len(dropped)


def remember(key, ttl, make):
    """Cached bytes for `key` in the current data generation, made with make() on a miss."""
    k = f"{key}@{_gen}"
    value = get(k)
    if value is None:
        value = make()
        set(k, value, ttl)
    return value


# ---- stale-while-revalidate, for the pages' bigger answers (kept in this app's memory)
# Background work (uploads, freeing storage) writes all the time, so "any write makes every answer
# stale" would mean no cache at all while the app is busy, which is when answers are slowest to
# make. Here an answer is reused for `fresh` seconds even after background writes, and after that
# the old one is sent once more while a new one is made behind it. Anything the user changes
# (user_changed(), on every POST) drops them all: the next look is always made fresh.

SWR_LIMIT = 48 * 1024 * 1024  # bytes kept
_swr = collections.OrderedDict()  # key -> (made, generation, user generation, bytes)
_swr_bytes = 0
_swr_busy = {}  # keys being made again (a dict: this module's set() is the cache's)
_user_gen = 0


def user_changed():
    """The user changed something: answers made before now are not reused."""
    global _user_gen
    _user_gen += 1


def _swr_store(key, value, made, gen, user_gen):
    global _swr_bytes
    with _lock:
        old = _swr.pop(key, None)
        if old:
            _swr_bytes -= len(old[3])
        _swr[key] = (made, gen, user_gen, value)
        _swr_bytes += len(value)
        while _swr_bytes > SWR_LIMIT and len(_swr) > 1:
            _, dropped = _swr.popitem(last=False)
            _swr_bytes -= len(dropped[3])


def swr(key, make, ttl=60, fresh=5):
    """Bytes for `key`: reused while the data hasn't changed (up to `ttl` seconds), or for `fresh`
    seconds after background changes; older ones are answered at once and made again behind it.
    make() runs on this thread only when there is nothing usable."""
    now = time.time()
    with _lock:
        hit = _swr.get(key)
        if hit:
            _swr.move_to_end(key)
    if hit and hit[2] == _user_gen:
        made, gen, _, value = hit
        age = now - made
        if age < (ttl if gen == _gen else min(fresh, ttl)):
            stats["hits"] += 1
            return value
        if age < 600:  # stale but recent: answer now, refresh behind it
            stats["hits"] += 1
            _refresh(key, make)
            return value
    stats["misses"] += 1
    gen, user_gen, made = _gen, _user_gen, time.time()
    value = make()
    _swr_store(key, value, made, gen, user_gen)
    return value


def _refresh(key, make):
    with _lock:
        if key in _swr_busy:
            return
        _swr_busy[key] = True

    def run():
        try:
            gen, user_gen, made = _gen, _user_gen, time.time()
            _swr_store(key, make(), made, gen, user_gen)
        except Exception as e:
            print(f"Cache refresh failed for {key}: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        finally:
            with _lock:
                _swr_busy.pop(key, None)

    threading.Thread(target=run, daemon=True, name="cache refresh").start()


def clear():
    global _mem_bytes
    global _swr_bytes
    with _lock:
        _mem.clear()
        _mem_bytes = 0
        _swr.clear()
        _swr_bytes = 0
    bump()


def info():
    """What the Health page shows about the cache."""
    out = {"backend": "memory", "connected": False, **stats, "used": _mem_bytes, "keys": len(_mem)}
    r = _redis()
    if r is not None:
        try:
            mem = r.info("memory")
            server = r.info("server")
            out.update(
                backend="Redis",
                connected=True,
                version=server.get("redis_version", ""),
                used=mem.get("used_memory", 0),
                limit=mem.get("maxmemory", 0),
                keys=r.dbsize(),
            )
        except Exception as e:
            _fail(e)
    elif URL:
        out["backend"] = "memory (Redis unreachable)"
    total = stats["hits"] + stats["misses"]
    out["hit_rate"] = stats["hits"] / total if total else 0
    return out
