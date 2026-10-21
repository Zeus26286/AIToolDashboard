"""SQLite storage for normalized usage events.

Token columns use one convention for every provider:
  input_tokens        uncached input
  cache_read_tokens   input served from cache
  cache_write_tokens  input written to a 5-minute cache (Anthropic)
  cache_write_1h_tokens  input written to a 1-hour cache (Anthropic)
  output_tokens       output, including reasoning
  reasoning_tokens    informational subset of output_tokens
"""

import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_events (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    source TEXT NOT NULL,
    ts TEXT NOT NULL,
    day TEXT NOT NULL,
    model TEXT NOT NULL,
    project TEXT,
    session_id TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_1h_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_day ON usage_events(day);
CREATE INDEX IF NOT EXISTS idx_events_account_day ON usage_events(account_id, day);

CREATE TABLE IF NOT EXISTS source_files (
    path TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    mtime REAL NOT NULL,
    size INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

EVENT_COLUMNS = (
    "id", "account_id", "source", "ts", "day", "model", "project", "session_id",
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
    "cache_write_1h_tokens", "reasoning_tokens",
)

_UPSERT = (
    f"INSERT OR REPLACE INTO usage_events ({', '.join(EVENT_COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in EVENT_COLUMNS)})"
)


class Database:
    def __init__(self, path):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.conn.executescript(SCHEMA)

    def file_unchanged(self, path, account_id, mtime, size):
        with self.lock:
            row = self.conn.execute(
                "SELECT account_id, mtime, size FROM source_files WHERE path = ?", (path,)
            ).fetchone()
        return row is not None and (row["account_id"], row["mtime"], row["size"]) == (account_id, mtime, size)

    def store_file_events(self, path, account_id, mtime, size, events):
        rows = [tuple(e.get(c) for c in EVENT_COLUMNS) for e in events]
        with self.lock, self.conn:
            self.conn.executemany(_UPSERT, rows)
            self.conn.execute(
                "INSERT OR REPLACE INTO source_files (path, account_id, mtime, size) VALUES (?, ?, ?, ?)",
                (path, account_id, mtime, size),
            )

    def query(self, sql, params=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def get_meta(self, key):
        with self.lock:
            row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key, value):
        with self.lock, self.conn:
            self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
