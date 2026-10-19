"""Durable storage for an uploaded event pool, mirrored to a local folder.

A hosted website (e.g. Streamlit Community Cloud) loses its disk on every
restart, so uploaded telemetry lives in a store and the website keeps a local
mirror to analyse. Only the converted pool travels (manifest plus raw and
event Parquet, about 2.6 MB per day); the uploaded CSV itself is not kept.

Stores:
  SupabaseStore  a private Supabase Storage bucket (SUPABASE_URL, SUPABASE_KEY)
  LocalStore     a plain folder (AROL_STORE_DIR), for local testing

The manifest is written last, so a store always holds a complete pool: an
interrupted upload leaves only unreferenced files behind.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import threading

from src.ingestion.event_pool import append_to_event_pool, build_event_pool

MANIFEST = "manifest.json"
# AROL export names: telemetry_<machine>_<YYYY-MM-DD>[...].csv
FILENAME = re.compile(r"^telemetry_(?P<machine>[A-Za-z0-9-]+)_(?P<date>\d{4}-\d{2}-\d{2})[^/\\]*\.csv$",
                      re.IGNORECASE)
_LOCK = threading.Lock()


class StoreError(RuntimeError):
    pass


class LocalStore:
    """A folder standing in for cloud storage."""

    def __init__(self, root):
        self.root = Path(root).resolve()

    def describe(self):
        return f"local folder {self.root}"

    def _path(self, name):
        path = (self.root / name).resolve()
        if not path.is_relative_to(self.root):
            raise StoreError(f"Path escapes the store: {name}")
        return path

    def read(self, name):
        path = self._path(name)
        return path.read_bytes() if path.is_file() else None

    def write(self, name, data):
        path = self._path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_bytes(data)
        temporary.replace(path)


class SupabaseStore:
    """A private Supabase Storage bucket, through its REST API."""

    def __init__(self, url, key, bucket="telemetry", prefix="pool", session=None, timeout=120):
        import requests

        self.base = url.rstrip("/") + "/storage/v1/object"
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        # New secret keys (sb_secret_...) go only in the apikey header; legacy
        # service_role keys are JWTs and also go in Authorization.
        self.session.headers["apikey"] = key
        if key.startswith("eyJ"):
            self.session.headers["Authorization"] = f"Bearer {key}"

    def describe(self):
        return f"Supabase bucket '{self.bucket}'"

    def _url(self, name):
        parts = PurePosixPath(name).parts
        if not parts or any(part in ("", ".", "..") for part in parts):
            raise StoreError(f"Invalid store path: {name}")
        return f"{self.base}/{self.bucket}/{self.prefix}/{name}"

    def read(self, name):
        response = self.session.get(self._url(name), timeout=self.timeout)
        if response.status_code == 404 or (
                response.status_code == 400 and "not_found" in response.text.lower().replace(" ", "_")):
            return None
        if response.status_code != 200:
            raise StoreError(f"Reading {name} from {self.describe()} failed: "
                             f"HTTP {response.status_code} {response.text[:200]}")
        return response.content

    def write(self, name, data):
        content_type = "application/json" if name.endswith(".json") else "application/octet-stream"
        response = self.session.post(self._url(name), data=data, timeout=self.timeout,
                                     headers={"x-upsert": "true", "Content-Type": content_type})
        if response.status_code not in (200, 201):
            raise StoreError(f"Saving {name} to {self.describe()} failed: "
                             f"HTTP {response.status_code} {response.text[:200]}")


def store_from_env(environ=None):
    """The configured store, or None when uploads are not set up."""
    environ = os.environ if environ is None else environ
    url = (environ.get("SUPABASE_URL") or "").strip()
    key = (environ.get("SUPABASE_KEY") or "").strip()
    if url and key:
        return SupabaseStore(url, key, bucket=(environ.get("AROL_STORAGE_BUCKET") or "telemetry").strip())
    folder = (environ.get("AROL_STORE_DIR") or "").strip()
    return LocalStore(folder) if folder else None


def machine_from_filename(name):
    match = FILENAME.match(name)
    if not match:
        raise ValueError("Name the file like the AROL export: telemetry_<machine>_<YYYY-MM-DD>.csv "
                         f"(got '{name}').")
    return match["machine"]


def _partition_files(manifest):
    names = []
    for entry in manifest["partitions"]:
        for key, folder in (("raw_parquet", "raw"), ("event_parquet", "events")):
            path = PurePosixPath(entry[key])
            if len(path.parts) != 2 or path.parts[0] != folder or path.parts[1] in ("", ".", ".."):
                raise StoreError(f"Unexpected partition path in the stored manifest: {entry[key]}")
            names.append(str(path))
    return names


def _write_local(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def pull(store, local_dir):
    """Mirror the stored pool into local_dir; return its manifest path, or None if empty."""
    local_dir = Path(local_dir)
    raw_manifest = store.read(MANIFEST)
    if raw_manifest is None:
        (local_dir / MANIFEST).unlink(missing_ok=True)
        return None
    manifest = json.loads(raw_manifest)
    hashes = {entry["event_parquet"]: entry["event_sha256"] for entry in manifest["partitions"]}
    for name in _partition_files(manifest):
        path = local_dir / name
        expected = hashes.get(name)
        if path.is_file() and (expected is None or hashlib.sha256(path.read_bytes()).hexdigest() == expected):
            continue
        data = store.read(name)
        if data is None:
            raise StoreError(f"{store.describe()} is missing {name}, which its manifest lists")
        if expected is not None and hashlib.sha256(data).hexdigest() != expected:
            raise StoreError(f"{name} in {store.describe()} does not match its manifest hash")
        _write_local(path, data)
    _write_local(local_dir / MANIFEST, raw_manifest)
    return local_dir / MANIFEST


def _push(store, local_dir, base_count):
    """Upload partitions after the first base_count, then the manifest."""
    remote = store.read(MANIFEST)
    remote_count = 0 if remote is None else len(json.loads(remote)["partitions"])
    if remote_count != base_count:
        raise StoreError("The stored data changed while this file was being added. "
                         "Reload the page and add the file again.")
    manifest_bytes = (local_dir / MANIFEST).read_bytes()
    manifest = json.loads(manifest_bytes)
    for name in _partition_files({"partitions": manifest["partitions"][base_count:]}):
        store.write(name, (local_dir / name).read_bytes())
    store.write(MANIFEST, manifest_bytes)


def add_csv(store, local_dir, csv_path, *, progress=None):
    """Add one daily CSV to the stored pool and return its new partition entry.

    Starts a pool with the first file. Afterwards files must follow in date
    order and belong to the same machine; the pool builder refuses anything
    else before the store is touched.
    """
    csv_path = Path(csv_path)
    machine = machine_from_filename(csv_path.name)
    local_dir = Path(local_dir)
    with _LOCK:
        manifest_path = pull(store, local_dir)
        if manifest_path is None:
            if local_dir.exists():
                shutil.rmtree(local_dir)  # a mirror without a manifest holds nothing kept
            manifest_path = build_event_pool([csv_path], local_dir, machine_id=machine, progress=progress)
            base_count = 0
            entry = json.loads(manifest_path.read_text(encoding="utf-8"))["partitions"][0]
        else:
            pooled = json.loads(manifest_path.read_text(encoding="utf-8"))
            if pooled["summary"]["machine_id"] != machine:
                raise ValueError(f"This file is for machine {machine}, but the stored data is for "
                                 f"machine {pooled['summary']['machine_id']}.")
            base_count = len(pooled["partitions"])
            entry = append_to_event_pool(csv_path, manifest_path, progress=progress)
        try:
            _push(store, local_dir, base_count)
        except Exception:
            # Return the mirror to what the store holds; the files left behind are unreferenced.
            pull(store, local_dir)
            raise
        return entry


def add_uploaded_csv(store, local_dir, name, data, *, progress=None):
    """add_csv for bytes received from a browser upload."""
    safe_name = Path(name).name
    machine_from_filename(safe_name)
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / safe_name
        path.write_bytes(data)
        return add_csv(store, local_dir, path, progress=progress)
