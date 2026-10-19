"""Check that the upload store is reachable before (or after) deploying.

Reads SUPABASE_URL / SUPABASE_KEY / AROL_STORAGE_BUCKET (or AROL_STORE_DIR)
from the environment or .env, writes one small test file, reads it back, and
reports how much telemetry the store already holds. From the repository root:

    python -m scripts.check_storage
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import sys

from src.common import config as config_mod
from src.common import pool_store


def main() -> int:
    config_mod.load_dotenv()
    store = pool_store.store_from_env()
    if store is None:
        print("No store configured: set SUPABASE_URL and SUPABASE_KEY (or AROL_STORE_DIR).")
        return 1
    print(f"Store: {store.describe()}")
    probe = f"checked {datetime.now(timezone.utc).isoformat()}".encode()
    try:
        store.write("connection-check.txt", probe)
        if store.read("connection-check.txt") != probe:
            print("FAILED: the test file read back differently.")
            return 1
        manifest = store.read(pool_store.MANIFEST)
    except pool_store.StoreError as exc:
        print(f"FAILED: {exc}")
        return 1
    print("OK: write and read work.")
    if manifest is None:
        print("No telemetry uploaded yet.")
    else:
        summary = json.loads(manifest)["summary"]
        print(f"Holds {summary['files']} file(s) for machine {summary['machine_id']}, "
              f"{summary['ts_min']} to {summary['ts_max']}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
