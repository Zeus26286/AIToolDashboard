"""Collectors turn local tool logs into normalized usage events (see aitd.db)."""

import os
from pathlib import Path

from . import claude_code, codex

COLLECTORS = {
    "claude_code": claude_code,
    "codex": codex,
}


def source_dirs(source):
    """Directories to scan for a source: its configured path(s), else the collector's defaults."""
    paths = source.get("path")
    if paths is None:
        paths = COLLECTORS[source["type"]].default_dirs()
    elif isinstance(paths, str):
        paths = [paths]
    return [Path(os.path.expanduser(p)) for p in paths]
