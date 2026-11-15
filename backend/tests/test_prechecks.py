"""Check first, then sync: an account added to a folder is compared with its Google library first."""

import json
import urllib.request

import pytest
from gpmc.db import Storage
from test_free_storage import item

import sync

MAIN, TWO = "test@example.com", "two@example.com"
SECOND = "androidId=abc&app=com.google.android.apps.photos&Email=two%40example.com&lang=en_US&Token=y"


class Lib:
    """A Google client whose library list holds the given files (as Google's dedup keys)."""

    def __init__(self, db, files, fail=None):
        self.db, self.files, self.fail, self.reads = db, files, fail, 0

    def update_cache(self, show_progress=True):
        self.reads += 1
        if self.fail:
            raise self.fail
        with Storage(self.db) as st:
            st.update([item(f"K{i}", data=data) for i, data in enumerate(self.files)])


@pytest.fixture
def two(media, tmp_path, monkeypatch):
    sync.q("DELETE FROM copies")
    sync.q("DELETE FROM prechecks")
    sync.q("DELETE FROM hashes")
    sync.save_account(SECOND)
    monkeypatch.setattr(sync, "GPMC_HOME", tmp_path / "gpmc")
    sync._library_db(TWO).parent.mkdir(parents=True)
    sync._library_db(MAIN).parent.mkdir(parents=True)
    sync.update_settings({"folders": {"Photos": [MAIN], "Videos": [MAIN]}})
    sync.scan()
    yield
    sync.q("DELETE FROM copies")
    sync.q("DELETE FROM prechecks")


def add_two(folders):
    before = sync.folder_targets()
    sync.update_settings({"folders": folders})
    return sync.queue_prechecks(before, sync.folder_targets())


def run(account, lib):
    sync.run_precheck("Photos", account, make_client=lambda a: lib)
    return {c["account"]: c for c in sync.precheck_list()}[account]


def test_only_an_account_added_to_a_folder_already_backed_up_is_checked(two):
    assert add_two({"Photos": [MAIN, TWO], "Videos": [MAIN]}) == 1
    assert [(c["folder"], c["account"], c["state"]) for c in sync.precheck_list()] == [("Photos", TWO, "waiting")]
    # A folder backed up for the first time just syncs: nothing to compare it with yet.
    sync.update_settings({"folders": {"Photos": [MAIN, TWO], "Videos": []}})
    assert add_two({"Photos": [MAIN, TWO], "Videos": [TWO]}) == 0
    # Taking the account off the folder drops its check.
    add_two({"Photos": [MAIN], "Videos": [TWO]})
    assert sync.precheck_list() == []


def test_nothing_is_sent_to_the_account_while_it_is_checked(two):
    add_two({"Photos": [MAIN, TWO], "Videos": [MAIN]})
    todo = sync.scan()
    assert not [t for t in todo if t[3] == TWO]  # held
    assert {t[3] for t in todo} == {MAIN}  # the main backup goes on as usual


def test_matches_are_recorded_without_uploading_and_the_rest_waits(two):
    add_two({"Photos": [MAIN, TWO], "Videos": [MAIN]})
    lib = Lib(sync._library_db(TWO), [b"x" * 100])  # Google already has trip/a.jpg
    c = run(TWO, lib)
    assert lib.reads == 1
    assert (c["state"], c["total"], c["matched"], c["new"], c["new_bytes"]) == ("ready", 2, 1, 1, 200)
    rows = sync.q("SELECT path, status, media_key, error FROM copies WHERE account=?", (TWO,))
    assert [(r[0].rsplit("/", 1)[-1], r[1], r[2], r[3]) for r in rows] == [
        ("a.jpg", "exists", "K0", sync.PRECHECK_NOTE)
    ]
    assert sync.account_speeds().get(TWO, 0) == 0  # found, not uploaded: no pace from it
    assert not [t for t in sync.scan() if t[3] == TWO]  # still waits for the user
    accs = {a["account"]: a for a in sync.folder_report("Photos")["total"]["accounts"]}
    assert (accs[TWO]["exists"], accs[TWO]["waiting"]) == (1, 1)
    # Start: only what Google didn't have is sent.
    assert sync.precheck_start(TWO) == 1
    sent = [t for t in sync.scan() if t[3] == TWO]
    assert [t[0].rsplit("/", 1)[-1] for t in sent] == ["b.HEIC"]


def test_files_already_hashed_are_not_read_again(two, monkeypatch):
    add_two({"Photos": [MAIN, TWO], "Videos": [MAIN]})
    run(TWO, Lib(sync._library_db(TWO), []))
    assert sync.q("SELECT COUNT(*) FROM hashes")[0][0] == 2
    monkeypatch.setattr(sync.media, "sha1_file", lambda p: pytest.fail("read again"))
    sync.precheck_retry(TWO)  # only failed checks go again: make it wait by hand
    sync.q("UPDATE prechecks SET state='waiting'")
    assert run(TWO, Lib(sync._library_db(TWO), []))["new"] == 2


def test_a_new_main_account_is_checked_into_the_backup_records(two):
    add_two({"Photos": [TWO, MAIN], "Videos": [MAIN]})
    run(TWO, Lib(sync._library_db(TWO), [b"y" * 200]))
    row = sync.q("SELECT status, account, error FROM files WHERE path LIKE '%b.HEIC'")[0]
    assert row == ("exists", TWO, sync.PRECHECK_NOTE)


def test_a_failed_check_can_be_tried_again_or_skipped(two):
    add_two({"Photos": [MAIN, TWO], "Videos": [MAIN]})
    c = run(TWO, Lib(sync._library_db(TWO), [], fail=RuntimeError("Google is busy")))
    assert c["state"] == "failed" and "Google is busy" in c["error"]
    sync.precheck_retry(TWO)
    assert sync.precheck_list()[0]["state"] == "waiting"
    sync.precheck_start(TWO, "Photos")  # sync without checking
    assert sync.precheck_list() == []
    assert len([t for t in sync.scan() if t[3] == TWO]) == 2


def test_settings_and_start_over_http(two, server):
    def post(path, body):
        req = urllib.request.Request(server + path, data=json.dumps(body).encode(), method="POST")
        with urllib.request.urlopen(req) as r:
            return json.load(r)

    post("/api/settings", {"folders": {"Photos": [MAIN, TWO], "Videos": [MAIN]}})
    with urllib.request.urlopen(server + "/api/status") as r:
        assert [c["account"] for c in json.load(r)["prechecks"]] == [TWO]
    assert post("/api/prechecks/start", {"account": TWO})["started"] == 1
    assert sync.precheck_list() == []
