"""Claude Code session logs: <config dir>/projects/<project>/<session>.jsonl.

Each assistant record carries the API's `usage` block. A single API response is
split across several records (one per content block) that repeat the same
usage, so events are keyed by message id + request id to count it once.
"""

import os
from pathlib import Path

from .common import as_int, iter_jsonl, local_day

NAME = "claude_code"


def default_dirs():
    dirs = []
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        dirs += [Path(p.strip()) / "projects" for p in env.split(",") if p.strip()]
    dirs += [Path.home() / ".claude" / "projects", Path.home() / ".config" / "claude" / "projects"]
    return dirs


def find_files(directory):
    return sorted(Path(directory).rglob("*.jsonl"))


def parse_file(path, account_id):
    events = {}
    for lineno, rec in iter_jsonl(path):
        if rec.get("type") != "assistant":
            continue
        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        usage = msg.get("usage")
        model = msg.get("model")
        ts = rec.get("timestamp")
        if not isinstance(usage, dict) or not model or model == "<synthetic>" or not ts:
            continue

        cache_write = as_int(usage.get("cache_creation_input_tokens"))
        breakdown = usage.get("cache_creation")
        cache_write_1h = 0
        if isinstance(breakdown, dict):
            cache_write_1h = min(as_int(breakdown.get("ephemeral_1h_input_tokens")), cache_write)
        thinking = (usage.get("output_tokens_details") or {}).get("thinking_tokens")

        if msg.get("id") and rec.get("requestId"):
            event_id = f"cc:{msg['id']}:{rec['requestId']}"
        else:
            event_id = f"cc:{path}:{lineno}"

        events[event_id] = {
            "id": event_id,
            "account_id": account_id,
            "source": NAME,
            "ts": ts,
            "day": local_day(ts),
            "model": model,
            "project": rec.get("cwd"),
            "session_id": rec.get("sessionId"),
            "input_tokens": as_int(usage.get("input_tokens")),
            "output_tokens": as_int(usage.get("output_tokens")),
            "cache_read_tokens": as_int(usage.get("cache_read_input_tokens")),
            "cache_write_tokens": cache_write - cache_write_1h,
            "cache_write_1h_tokens": cache_write_1h,
            "reasoning_tokens": as_int(thinking),
        }
    return list(events.values())
