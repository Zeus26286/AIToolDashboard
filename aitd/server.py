"""HTTP server: the dashboard page plus a small JSON API."""

import json
import mimetypes
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import report, sync

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _parse_day(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def make_handler(cfg, db, pricing):
    class Handler(BaseHTTPRequestHandler):
        server_version = "AIToolDashboard"

        def log_message(self, fmt, *args):
            pass  # keep the console for sync messages

        def _send(self, status, body, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload, status=200):
            self._send(status, json.dumps(payload).encode(), "application/json")

        def do_GET(self):
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            if url.path == "/api/summary":
                self._json(report.build(cfg, db, pricing, start=_parse_day(q.get("start")),
                                        end=_parse_day(q.get("end")), account=q.get("account") or None))
            elif url.path == "/api/health":
                self._json({"ok": True, "last_sync": db.get_meta("last_sync")})
            else:
                self._static(url.path)

        def do_POST(self):
            if urlparse(self.path).path == "/api/sync":
                self._json(sync.run(cfg, db, pricing))
            else:
                self._json({"error": "not found"}, 404)

        def _static(self, path):
            name = "index.html" if path in ("/", "") else path.lstrip("/").removeprefix("static/")
            target = (STATIC_DIR / name).resolve()
            if STATIC_DIR not in target.parents or not target.is_file():
                self._json({"error": "not found"}, 404)
                return
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self._send(200, target.read_bytes(), ctype)

    return Handler


def serve(cfg, db, pricing, host, port):
    return ThreadingHTTPServer((host, port), make_handler(cfg, db, pricing))
