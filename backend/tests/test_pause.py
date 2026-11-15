"""Pause stops work part-way (not after the file under way), and Sync pauses accounts one by one."""

import io
import json

import pytest
from helpers import post
from test_accounts import SECOND

import sync


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(sync, "PAUSE_CHECK_SECONDS", 0)


@pytest.fixture
def two(media):
    sync.q("DELETE FROM copies")
    sync.save_account(SECOND)
    yield ["test@example.com", "two@example.com"]
    for a in ("test@example.com", "two@example.com"):
        sync.meta_set(f"uploads_paused:{a}", "0")
    sync.PAUSE_FILE.unlink(missing_ok=True)
    sync.q("DELETE FROM copies")


def test_gpmc_upload_reader_stops_when_paused():
    import gpmc.client as gc

    with sync.stop_on_pause(lambda: False):
        reader = gc._ProgressReader(io.BytesIO(b"x" * 10), 10, lambda *a: None)
        assert reader.read(4) == b"xxxx"  # not paused: reads as before
    with sync.stop_on_pause(lambda: True), pytest.raises(sync.Paused):
        reader.read(4)
    assert reader.read(4) == b"xxxx"  # the check only holds inside stop_on_pause
    src = __import__("inspect").getsource(gc.Client._upload_file)
    assert "_ProgressReader(" in src  # gpmc still sends uploads through the reader we patch


class Streaming:
    """Sends a file the way gpmc does, through its reader in chunks; pauses uploads after `after` chunks."""

    def __init__(self, after):
        self.after = after
        self.started = []

    def _upload_file(self, file_path, hash_value, progress, force_upload, use_quota, saver, progress_callback):
        import gpmc.client as gc

        self.started.append(file_path.name)
        with open(file_path, "rb") as f:
            reader = gc._ProgressReader(f, 1, lambda *a: None)
            n = 0
            while reader.read(10):
                n += 1
                if n == self.after:
                    sync.PAUSE_FILE.touch()  # the user taps Pause while this file is sending
        return {str(file_path): "media-key"}


def test_pause_stops_the_file_under_way(media, quick):
    todo = sync.scan()
    client = Streaming(after=2)
    sync.upload_all(client, todo)
    assert client.started == [todo[0][0].rsplit("/", 1)[-1]]  # stopped part-way, nothing else started
    assert sync.q("SELECT COUNT(*) FROM files WHERE status IN ('uploaded','failed')")[0][0] == 0
    assert sync.state["lanes"]["test@example.com"]["phase"] == "stopped" and sync.state["uploading"] == {}
    assert sync.state["done"] == 0  # not counted as done: it goes again on resume
    sync.PAUSE_FILE.unlink()
    again = sync.scan()
    assert todo[0][0] in [t[0] for t in again]  # still waiting, from the start


def test_pause_stops_hashing_too(media, quick, monkeypatch):
    todo = sync.scan()

    def hashing(path, on_progress=None, chunk=0):
        sync.PAUSE_FILE.touch()
        on_progress(1)
        raise AssertionError("should have stopped")

    monkeypatch.setattr(sync.media, "sha1_file", hashing)
    client = Streaming(after=99)
    sync.upload_all(client, todo)
    assert client.started == [] and sync.q("SELECT COUNT(*) FROM files WHERE status='failed'")[0][0] == 0


def test_pause_one_account_then_all(two):
    a, b = two
    sync.set_uploads_paused(a, True)
    assert sync.uploads_paused(a) and not sync.uploads_paused(b) and not sync.PAUSE_FILE.exists()
    assert sync.paused_upload_accounts() == [a]
    sync.set_uploads_paused(b, True)  # every account paused: the same as Pause all
    assert sync.PAUSE_FILE.exists() and sync.meta_get(f"uploads_paused:{a}") == "0"
    sync.set_uploads_paused(a, False)  # resume one while all are paused: the other stays paused
    assert not sync.PAUSE_FILE.exists() and sync.paused_upload_accounts() == [b]
    sync.pause_uploads(False)  # Resume all
    assert sync.paused_upload_accounts() == []


def test_paused_account_waits_while_the_other_uploads(two, monkeypatch):
    from helpers import FakeClient

    sync.update_settings({"folders": {"Photos": two}})
    sync.set_uploads_paused("two@example.com", True)
    client = FakeClient({"a.jpg": "uploading", "b.HEIC": "uploading"})
    monkeypatch.setattr(sync, "_reclaim_client", lambda account: client)
    sync.run_round()
    assert "two@example.com" not in sync.state["lanes"]
    assert sync.state["lanes"]["test@example.com"]["phase"] == "done"
    assert sync.q("SELECT COUNT(*) FROM copies")[0][0] == 0  # nothing sent to the paused account


def test_pause_api_per_account_and_all(two, server):
    a, b = two
    code, body = post(server + "/api/pause", json.dumps({"account": b, "paused": True}).encode())
    assert code == 200 and json.loads(body)["paused_accounts"] == [b]
    status = json.loads(sync.status_body())
    assert status["paused_accounts"] == [b] and not status["paused"]
    assert post(server + "/api/pause", json.dumps({"account": "who@x.com", "paused": True}).encode())[0] == 404
    post(server + "/api/pause")  # Pause all
    assert sync.PAUSE_FILE.exists() and sync.paused_upload_accounts() == [a, b]
    post(server + "/api/pause")  # Resume all clears the per-account pause too
    assert not sync.PAUSE_FILE.exists() and sync.paused_upload_accounts() == []


def test_pause_everything_resumes_paused_accounts(two):
    sync.set_uploads_paused("two@example.com", True)
    sync.pause_all(True)
    assert sync.PAUSE_FILE.exists()
    sync.pause_all(False)
    assert sync.paused_upload_accounts() == []


def test_free_upload_stops_part_way(media):
    """Free storage, Copy accounts and Fix dates upload through _reupload: their pause stops it too."""
    path = media / "Photos" / "b.HEIC"
    client = Streaming(after=99)
    track = {"current": None, "stop": lambda: True}
    with pytest.raises(sync.Paused):
        sync._reupload(client, path, sync._b64_hash(b"0" * 20), 200, "K", track)
    track["stop"] = lambda: False
    assert sync._reupload(client, path, sync._b64_hash(b"0" * 20), 200, "K", track) == "media-key"
