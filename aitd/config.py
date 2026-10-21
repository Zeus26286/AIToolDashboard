"""Loads config.json, creating it from the example on first run."""

import json
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_PATH = ROOT / "config.example.json"
DEFAULT_PATH = ROOT / "config.json"

SOURCE_TYPES = {"claude_code", "codex"}
ACCOUNT_KINDS = {"subscription", "api"}


class ConfigError(Exception):
    pass


def load(path=None):
    path = Path(path or os.environ.get("AITD_CONFIG") or DEFAULT_PATH)
    if not path.exists():
        if path == DEFAULT_PATH and EXAMPLE_PATH.exists():
            shutil.copyfile(EXAMPLE_PATH, path)
        else:
            raise ConfigError(f"config file not found: {path}")
    try:
        cfg = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path}: invalid JSON ({e})") from e
    _validate(cfg, path)
    cfg["_path"] = str(path)
    return cfg


def _validate(cfg, path):
    cfg.setdefault("server", {})
    cfg["server"].setdefault("host", "127.0.0.1")
    cfg["server"].setdefault("port", 8787)
    cfg["server"].setdefault("sync_interval_minutes", 10)
    cfg.setdefault("database", str(ROOT / "data" / "usage.db"))
    cfg.setdefault("price_overrides", {})

    ids = set()
    for acct in cfg.get("accounts", []):
        for key in ("id", "name", "kind"):
            if key not in acct:
                raise ConfigError(f"{path}: account is missing '{key}': {acct}")
        if acct["id"] in ids:
            raise ConfigError(f"{path}: duplicate account id '{acct['id']}'")
        ids.add(acct["id"])
        if acct["kind"] not in ACCOUNT_KINDS:
            raise ConfigError(f"{path}: account '{acct['id']}' kind must be one of {sorted(ACCOUNT_KINDS)}")
        acct.setdefault("monthly_fee", 0)
        acct.setdefault("provider", "")
        acct.setdefault("sources", [])
        for src in acct["sources"]:
            if src.get("type") not in SOURCE_TYPES:
                raise ConfigError(f"{path}: account '{acct['id']}' has unknown source type {src.get('type')!r}")
