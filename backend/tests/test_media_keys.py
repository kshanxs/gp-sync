"""Media keys whose bytes also read as a protobuf message come back from gpmc as text, not a dict."""

import hashlib
import time

import gpmc.api
from blackboxprotobuf import encode_message
from gpmc.db import Storage
from test_live_photos import gitem

import sync

TRICKY = "AF1QipN4Oup1ne-USW2YuppaZHXBxM1DqZlo0DVuoUBo"  # a real key that reads as a message
PLAIN = "AF1QipOF9qSKCKM_ze9uCXjCgoMo33IPztDuKgwPyB3z"
FIND = {"1": {"type": "message", "message_typedef": {"2": {"type": "message", "message_typedef": {
    "2": {"type": "message", "message_typedef": {"1": {"type": "bytes"}}}}}}}}  # fmt: skip


def answer(key):
    """Google's answer to "which item has this checksum?", holding `key`."""
    return encode_message({"1": {"2": {"2": {"1": key.encode()}}}}, FIND)


def test_a_key_that_reads_as_a_message_comes_back_as_text():
    message, _ = gpmc.api.decode_message(answer(TRICKY))
    assert message["1"]["2"]["2"]["1"] == TRICKY


def test_plain_keys_and_real_messages_are_unchanged():
    message, _ = gpmc.api.decode_message(answer(PLAIN))
    assert message["1"]["2"]["2"]["1"] == PLAIN
    from blackboxprotobuf import decode_message as original

    nested = encode_message({"1": {"5": 7, "6": b"\x00\x01", "7": b"Shimla"}}, {"1": {"type": "message",
        "message_typedef": {"5": {"type": "int"}, "6": {"type": "bytes"}, "7": {"type": "bytes"}}}})  # fmt: skip
    assert gpmc.api.decode_message(nested) == original(nested)  # only media keys are touched


def test_files_that_failed_this_way_are_fixed(media, tmp_path, monkeypatch):
    acc = "test@example.com"
    monkeypatch.setattr(sync, "GPMC_HOME", tmp_path / "gpmc")
    (media / "Photos").mkdir(exist_ok=True)
    there, missing = media / "Photos" / "there.jpg", media / "Photos" / "missing.jpg"
    there.write_bytes(b"photo in google")
    missing.write_bytes(b"photo not listed yet")
    err = "ProgrammingError: Error binding parameter 5: type 'dict' is not supported"
    for p in (there, missing):
        sync.q("INSERT OR REPLACE INTO files (path, size, mtime, status, media_key, error, attempts, updated, account) "
               "VALUES (?,?,?,'failed',NULL,?,3,?,?)", (str(p), 1, 1, err, time.time(), acc))  # fmt: skip
    dedup = sync._b64_hash(hashlib.sha1(there.read_bytes()).digest())
    db = sync._library_db(acc)
    db.parent.mkdir(parents=True)
    with Storage(db) as s:
        s.update([gitem(TRICKY, dedup)])
    assert sync.fix_dict_key_failures() == 2
    rows = dict((p, r) for p, *r in sync.q("SELECT path, status, media_key, attempts FROM files WHERE path IN (?,?)",
                                          (str(there), str(missing))))  # fmt: skip
    assert rows[str(there)] == ["exists", TRICKY, 0]  # in Google: recorded, nothing uploaded again
    assert rows[str(missing)] == ["failed", None, 0]  # tried again
