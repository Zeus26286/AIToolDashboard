"""Scans every configured source and stores new or changed log files."""

import threading
import time
from datetime import datetime, timezone

from .collectors import COLLECTORS, source_dirs

_sync_lock = threading.Lock()


def run(cfg, db, pricing):
    """Imports changed files for every account. Returns a summary dict."""
    if not _sync_lock.acquire(blocking=False):
        return {"status": "busy"}
    try:
        started = time.monotonic()
        summary = {"status": "ok", "files_scanned": 0, "files_imported": 0, "events": 0, "errors": [], "sources": []}

        price_error = pricing.refresh_if_stale()
        if price_error:
            summary["errors"].append(price_error)

        for acct in cfg["accounts"]:
            for source in acct["sources"]:
                collector = COLLECTORS[source["type"]]
                for directory in source_dirs(source):
                    entry = {"account_id": acct["id"], "type": source["type"], "path": str(directory),
                             "found": directory.is_dir(), "files": 0}
                    summary["sources"].append(entry)
                    if not entry["found"]:
                        continue
                    for path in collector.find_files(directory):
                        entry["files"] += 1
                        summary["files_scanned"] += 1
                        try:
                            st = path.stat()
                            if db.file_unchanged(str(path), acct["id"], st.st_mtime, st.st_size):
                                continue
                            events = collector.parse_file(str(path), acct["id"])
                            db.store_file_events(str(path), acct["id"], st.st_mtime, st.st_size, events)
                        except Exception as e:  # one bad file must not stop the sync
                            summary["errors"].append(f"{path}: {e}")
                            continue
                        summary["files_imported"] += 1
                        summary["events"] += len(events)

        summary["seconds"] = round(time.monotonic() - started, 2)
        summary["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        db.set_meta("last_sync", summary["finished_at"])
        return summary
    finally:
        _sync_lock.release()


def start_background(cfg, db, pricing, log=print):
    interval = max(1, float(cfg["server"]["sync_interval_minutes"])) * 60

    def loop():
        while True:
            time.sleep(interval)
            result = run(cfg, db, pricing)
            if result.get("status") == "ok" and (result["files_imported"] or result["errors"]):
                log(f"sync: {result['files_imported']} file(s), {result['events']} event(s), "
                    f"{len(result['errors'])} error(s)")

    threading.Thread(target=loop, name="aitd-sync", daemon=True).start()
