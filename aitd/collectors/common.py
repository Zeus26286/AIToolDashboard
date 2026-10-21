"""Helpers shared by the collectors."""

import json
from datetime import datetime


def iter_jsonl(path):
    """Yields (line_number, record) for each parseable JSON object line."""
    with open(path, encoding="utf-8", errors="replace") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue  # a partially written last line, or corruption
            if isinstance(record, dict):
                yield lineno, record


def local_day(ts):
    """ISO-8601 UTC timestamp -> YYYY-MM-DD in this machine's local time zone."""
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt.astimezone().date().isoformat()


def as_int(value):
    return value if isinstance(value, int) and value > 0 else 0
