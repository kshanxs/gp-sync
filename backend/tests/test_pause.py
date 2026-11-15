"""Pause lets the file under way finish, then stops; Sync pauses accounts one by one."""

import json

import pytest
from helpers import FakeClient, post
from test_accounts import SECOND

import sync


@pytest.fixture
def two(media):
    sync.q("DELETE FROM copies")
    sync.save_account(SECOND)
    yield ["test@example.com", "two@example.com"]
    for a in ("test@example.com", "two@example.com"):
        sync.meta_set(f"uploads_paused:{a}", "0")
    sync.PAUSE_FILE.unlink(missing_ok=True)
    sync.q("DELETE FROM copies")


def test_pause_lets_the_file_under_way_finish(media):
    """Pausing waits for the file being sent: it finishes and is saved, then no new file starts."""
    todo = sync.scan()
    started = []

    class PauseMidFile(FakeClient):
        def _upload_file(self, file_path, *a, **k):
            started.append(file_path.name)
            sync.PAUSE_FILE.touch()  # the user taps Pause while this file is sending
            return super()._upload_file(file_path, *a, **k)

    sync.upload_all(PauseMidFile({p.rsplit("/", 1)[-1]: "uploading" for p, *_ in todo}), todo)
    assert len(started) == 1  # nothing new started after the pause
    assert sync.q("SELECT path FROM files WHERE status='uploaded'") == [(todo[0][0],)]  # it finished
    assert sync.state["done"] == 1


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
