"""Uploaded telemetry: stored durably, mirrored locally, equal to a full build."""
import json
from datetime import datetime, timedelta

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from src.common import pool_store
from src.common.pool_store import LocalStore, StoreError, SupabaseStore
from src.ingestion.event_pool import build_event_pool, scan_event_pool


@pytest.fixture
def days(tmp_path):
    """Three consecutive daily AROL-named files for machine M1."""
    rows = 12
    frame = pl.DataFrame({
        "timestamp": [datetime(2026, 2, 1, 22) + timedelta(hours=i) for i in range(rows)],
        "H01 Count": [float(10 + i) for i in range(rows)],
        "H01 AppTorque": [1.0 + i / 10 for i in range(rows)],
        "H01 Status": [0] * rows,
        "H02 Count": [float(20 + i // 2) for i in range(rows)],
        "H02 AppTorque": [2.0] * rows,
        "H02 Status": [0, 65] * (rows // 2),
    })
    folder = tmp_path / "csv"
    folder.mkdir()
    paths = []
    for day, chunk in enumerate((frame[:3], frame[3:7], frame[7:]), start=1):
        path = folder / f"telemetry_M1_2026-02-0{day}.csv"
        chunk.write_csv(path)
        paths.append(path)
    return paths


def test_uploads_build_the_same_pool_as_a_full_build(tmp_path, days):
    store = LocalStore(tmp_path / "store")
    mirror = tmp_path / "mirror"
    entries = [pool_store.add_csv(store, mirror, path) for path in days]
    assert [e["input_file"] for e in entries] == [p.name for p in days]
    full = build_event_pool(days, tmp_path / "full", machine_id="M1")
    # A fresh host with an empty disk recovers everything from the store.
    fresh = pool_store.pull(store, tmp_path / "fresh-host")
    stored = json.loads(fresh.read_text())
    expected = json.loads(full.read_text())
    assert stored["summary"] == expected["summary"]
    assert stored["partitions"] == expected["partitions"]
    assert_frame_equal(scan_event_pool(fresh, verify_hashes=True).collect(),
                       scan_event_pool(full).collect())


def test_bad_uploads_leave_the_store_untouched(tmp_path, days):
    store = LocalStore(tmp_path / "store")
    mirror = tmp_path / "mirror"
    pool_store.add_csv(store, mirror, days[1])
    before = store.read("manifest.json")
    with pytest.raises(ValueError, match="already in the event pool"):
        pool_store.add_csv(store, mirror, days[1])
    with pytest.raises(ValueError, match="backwards"):
        pool_store.add_csv(store, mirror, days[0])  # an earlier day after a later one
    other = tmp_path / "telemetry_M2_2026-02-04.csv"
    other.write_bytes(days[2].read_bytes())
    with pytest.raises(ValueError, match="machine M2"):
        pool_store.add_csv(store, mirror, other)
    misnamed = tmp_path / "day3.csv"
    misnamed.write_bytes(days[2].read_bytes())
    with pytest.raises(ValueError, match="AROL export"):
        pool_store.add_csv(store, mirror, misnamed)
    assert store.read("manifest.json") == before


def test_failed_save_keeps_store_and_mirror_consistent(tmp_path, days, monkeypatch):
    store = LocalStore(tmp_path / "store")
    mirror = tmp_path / "mirror"
    pool_store.add_csv(store, mirror, days[0])
    before = store.read("manifest.json")
    original = store.write

    def fail_on_manifest(name, data):
        if name == "manifest.json":
            raise StoreError("network down")
        original(name, data)

    monkeypatch.setattr(store, "write", fail_on_manifest)
    with pytest.raises(StoreError, match="network down"):
        pool_store.add_csv(store, mirror, days[1])
    assert store.read("manifest.json") == before
    assert (mirror / "manifest.json").read_bytes() == before
    monkeypatch.setattr(store, "write", original)
    pool_store.add_csv(store, mirror, days[1])  # the retry succeeds
    assert len(json.loads(store.read("manifest.json"))["partitions"]) == 2


def test_failed_first_save_leaves_no_pool(tmp_path, days, monkeypatch):
    store = LocalStore(tmp_path / "store")
    monkeypatch.setattr(store, "write", lambda name, data: (_ for _ in ()).throw(StoreError("down")))
    with pytest.raises(StoreError):
        pool_store.add_csv(store, tmp_path / "mirror", days[0])
    assert pool_store.pull(store, tmp_path / "mirror") is None


def test_concurrent_change_is_refused(tmp_path, days):
    store = LocalStore(tmp_path / "store")
    pool_store.add_csv(store, tmp_path / "host-a", days[0])
    stale = tmp_path / "host-b"
    pool_store.pull(store, stale)
    pool_store.add_csv(store, tmp_path / "host-a", days[1])
    # host-b pulls before appending, so it continues from the latest data.
    pool_store.add_csv(store, stale, days[2])
    assert len(json.loads(store.read("manifest.json"))["partitions"]) == 3
    with pytest.raises(StoreError, match="changed"):
        pool_store._push(store, stale, base_count=1)


def test_pull_rejects_corrupt_or_unsafe_stored_files(tmp_path, days):
    store = LocalStore(tmp_path / "store")
    pool_store.add_csv(store, tmp_path / "mirror", days[0])
    manifest = json.loads(store.read("manifest.json"))
    store.write(manifest["partitions"][0]["event_parquet"], b"corrupt")
    with pytest.raises(StoreError, match="hash"):
        pool_store.pull(store, tmp_path / "fresh")
    manifest["partitions"][0]["raw_parquet"] = "../../outside.parquet"
    store.write("manifest.json", json.dumps(manifest).encode())
    with pytest.raises(StoreError, match="Unexpected partition path"):
        pool_store.pull(store, tmp_path / "fresh2")


def test_uploaded_bytes_use_only_the_file_name(tmp_path, days):
    store = LocalStore(tmp_path / "store")
    entry = pool_store.add_uploaded_csv(store, tmp_path / "mirror",
                                        "../../" + days[0].name, days[0].read_bytes())
    assert entry["input_file"] == days[0].name
    with pytest.raises(ValueError, match="AROL export"):
        pool_store.add_uploaded_csv(store, tmp_path / "mirror", "notes.csv", b"timestamp\n")


def test_store_from_env_prefers_supabase():
    assert pool_store.store_from_env({}) is None
    local = pool_store.store_from_env({"AROL_STORE_DIR": "somewhere"})
    assert isinstance(local, LocalStore)
    remote = pool_store.store_from_env({"AROL_STORE_DIR": "x", "SUPABASE_URL": "https://p.supabase.co",
                                        "SUPABASE_KEY": "sb_secret_abc", "AROL_STORAGE_BUCKET": "b"})
    assert isinstance(remote, SupabaseStore) and remote.bucket == "b"


class _Response:
    def __init__(self, status, content=b""):
        self.status_code = status
        self.content = content
        self.text = content.decode(errors="replace")


class _Session:
    def __init__(self, responses):
        self.headers = {}
        self.responses = list(responses)
        self.calls = []

    def get(self, url, timeout):
        self.calls.append(("GET", url, None))
        return self.responses.pop(0)

    def post(self, url, data, timeout, headers):
        self.calls.append(("POST", url, headers))
        return self.responses.pop(0)


def test_supabase_requests_and_missing_objects():
    session = _Session([_Response(200, b"data"), _Response(404),
                        _Response(400, b'{"statusCode":"404","error":"not_found"}'),
                        _Response(200), _Response(500, b"boom")])
    store = SupabaseStore("https://p.supabase.co/", "sb_secret_abc", session=session)
    assert session.headers == {"apikey": "sb_secret_abc"}  # new keys never go in Authorization
    assert store.read("events/0001-a.parquet") == b"data"
    assert store.read("manifest.json") is None
    assert store.read("manifest.json") is None
    store.write("manifest.json", b"{}")
    with pytest.raises(StoreError, match="HTTP 500"):
        store.write("raw/0001-a.parquet", b"x")
    method, url, headers = session.calls[3]
    assert url == "https://p.supabase.co/storage/v1/object/telemetry/pool/manifest.json"
    assert method == "POST" and headers["x-upsert"] == "true"
    assert headers["Content-Type"] == "application/json"
    with pytest.raises(StoreError):
        store.read("../secrets")
    legacy = SupabaseStore("https://p.supabase.co", "eyJhbGciOi", session=_Session([]))
    assert legacy.session.headers["Authorization"] == "Bearer eyJhbGciOi"
