"""Command line: `python3 -m aitd serve | sync | report`."""

import argparse
import sys
from pathlib import Path

from . import config, report, server, sync
from .db import Database
from .pricing import Pricing


def _open(cfg):
    db = Database(cfg["database"])
    pricing = Pricing(Path(cfg["database"]).parent / "litellm_prices.json", cfg["price_overrides"])
    return db, pricing


def _print_sync(result):
    if result.get("status") != "ok":
        print(f"sync: {result.get('status')}")
        return
    for s in result["sources"]:
        state = f"{s['files']} file(s)" if s["found"] else "not found"
        print(f"  {s['account_id']:<16} {s['type']:<12} {s['path']}  [{state}]")
    print(f"imported {result['files_imported']} changed file(s), {result['events']} event(s) "
          f"in {result['seconds']}s")
    for err in result["errors"]:
        print(f"  warning: {err}", file=sys.stderr)


def cmd_serve(cfg, args):
    db, pricing = _open(cfg)
    host = args.host or cfg["server"]["host"]
    port = args.port or cfg["server"]["port"]
    print("initial sync...")
    _print_sync(sync.run(cfg, db, pricing))
    sync.start_background(cfg, db, pricing)
    httpd = server.serve(cfg, db, pricing, host, port)
    shown = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
    print(f"\nAI Tool Dashboard running at http://{shown}:{port}  (Ctrl+C to stop)")
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("warning: listening on a network interface; anyone on your network can open the dashboard")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


def cmd_sync(cfg, args):
    db, pricing = _open(cfg)
    _print_sync(sync.run(cfg, db, pricing))


def cmd_report(cfg, args):
    db, pricing = _open(cfg)
    data = report.build(cfg, db, pricing)
    t = data["totals"]
    print(f"{data['range']['start']} .. {data['range']['end']}")
    print(f"{'account':<20}{'tokens':>16}{'API-equiv $':>14}{'fee $':>10}{'value':>8}")
    for a in data["by_account"]:
        mult = f"{a['value_multiple']:.1f}x" if a["value_multiple"] else "-"
        print(f"{a['name']:<20}{a['total_tokens']:>16,}{a['cost']:>14,.2f}{a['fee']:>10,.2f}{mult:>8}")
    print(f"{'total':<20}{t['total_tokens']:>16,}{t['cost']:>14,.2f}{t['fees']:>10,.2f}")
    if data["unpriced_models"]:
        print("no price for: " + ", ".join(m["model"] for m in data["unpriced_models"]))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="aitd", description="AI Tool Dashboard")
    parser.add_argument("--config", help="path to config.json (default: ./config.json)")
    sub = parser.add_subparsers(dest="command")
    p_serve = sub.add_parser("serve", help="run the dashboard web server (default)")
    p_serve.add_argument("--host", help="interface to bind, e.g. 0.0.0.0 to allow other devices")
    p_serve.add_argument("--port", type=int)
    sub.add_parser("sync", help="import new log data once and exit")
    sub.add_parser("report", help="print a usage summary in the terminal")
    args = parser.parse_args(argv)

    try:
        cfg = config.load(args.config)
    except config.ConfigError as e:
        sys.exit(f"config error: {e}")
    {"serve": cmd_serve, "sync": cmd_sync, "report": cmd_report, None: cmd_serve}[args.command](cfg, args)


if __name__ == "__main__":
    main()
