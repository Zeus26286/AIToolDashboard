"""Codex CLI rollout logs: $CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl.

Usage arrives as `event_msg` records of payload type `token_count`, carrying the
running `total_token_usage` for the session and the `last_token_usage` of the
latest turn. Codex can emit the same token_count more than once, so the delta of
the running total is preferred; `last_token_usage` is the fallback when no
total is present or the total went backwards (e.g. after the context reset).

OpenAI-style counts include cached tokens in input_tokens and reasoning tokens
in output_tokens; input is converted to the uncached share here.
"""

import os
from pathlib import Path

from .common import as_int, iter_jsonl, local_day

NAME = "codex"
FALLBACK_MODEL = "gpt-5"  # early Codex logs carried no model name
USAGE_KEYS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")


def default_dirs():
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    return [home / "sessions", home / "archived_sessions"]


def find_files(directory):
    return sorted(Path(directory).rglob("*.jsonl"))


def _usage(value):
    if not isinstance(value, dict):
        return None
    usage = {k: as_int(value.get(k)) for k in USAGE_KEYS}
    if not usage["cached_input_tokens"]:
        usage["cached_input_tokens"] = as_int(value.get("cache_read_input_tokens"))
    return usage


def _model_from(payload):
    info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
    for candidate in (info.get("model"), info.get("model_name"), payload.get("model")):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def parse_file(path, account_id):
    events = []
    session_id = Path(path).stem
    model = None
    cwd = None
    prev_total = None

    for lineno, rec in iter_jsonl(path):
        rtype = rec.get("type")
        payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}

        if rtype == "session_meta":
            session_id = payload.get("id") or session_id
            cwd = payload.get("cwd") or cwd
            continue
        if rtype == "turn_context":
            model = _model_from(payload) or model
            cwd = payload.get("cwd") or cwd
            continue
        if rtype != "event_msg" or payload.get("type") != "token_count":
            continue
        ts = rec.get("timestamp")
        info = payload.get("info")
        if not ts or not isinstance(info, dict):
            continue

        total = _usage(info.get("total_token_usage"))
        last = _usage(info.get("last_token_usage"))
        delta = None
        if total is not None and prev_total is not None and all(total[k] >= prev_total[k] for k in USAGE_KEYS):
            delta = {k: total[k] - prev_total[k] for k in USAGE_KEYS}
        elif last is not None:
            delta = last
        elif total is not None:
            delta = total
        if total is not None:
            prev_total = total
        if delta is None or not any(delta.values()):
            continue

        model = _model_from(payload) or model
        cached = min(delta["cached_input_tokens"], delta["input_tokens"])
        events.append({
            "id": f"codex:{path}:{lineno}",
            "account_id": account_id,
            "source": NAME,
            "ts": ts,
            "day": local_day(ts),
            "model": model or FALLBACK_MODEL,
            "project": cwd,
            "session_id": session_id,
            "input_tokens": delta["input_tokens"] - cached,
            "output_tokens": delta["output_tokens"],
            "cache_read_tokens": cached,
            "cache_write_tokens": 0,
            "cache_write_1h_tokens": 0,
            "reasoning_tokens": delta["reasoning_output_tokens"],
        })
    return events
