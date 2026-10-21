import json
import tempfile
import threading
import unittest
import urllib.request
from datetime import date
from pathlib import Path

from aitd import report, server, sync
from aitd.collectors import claude_code, codex
from aitd.db import Database
from aitd.pricing import Pricing

FIXTURES = Path(__file__).parent / "fixtures"


def make_config(tmp):
    return {
        "server": {"host": "127.0.0.1", "port": 0, "sync_interval_minutes": 10},
        "database": str(Path(tmp) / "usage.db"),
        "price_overrides": {},
        "accounts": [
            {"id": "claude-pro", "name": "Claude Pro", "provider": "anthropic", "kind": "subscription",
             "monthly_fee": 31, "sources": [{"type": "claude_code", "path": str(FIXTURES / "claude")}]},
            {"id": "codex", "name": "Codex CLI", "provider": "openai", "kind": "subscription",
             "monthly_fee": 0, "sources": [{"type": "codex", "path": str(FIXTURES / "codex")}]},
        ],
    }


class OfflinePricing(Pricing):
    def refresh_if_stale(self, force=False):
        return None


class ClaudeCodeTests(unittest.TestCase):
    def setUp(self):
        self.events = claude_code.parse_file(str(FIXTURES / "claude/proj-a/session1.jsonl"), "acct")

    def test_repeated_records_count_once_and_synthetic_is_skipped(self):
        self.assertEqual(sorted(e["model"] for e in self.events), ["claude-haiku-5-5", "claude-opus-5-5"])

    def test_cache_write_split_by_ttl(self):
        opus = next(e for e in self.events if e["model"] == "claude-opus-5-5")
        self.assertEqual(opus["input_tokens"], 10)
        self.assertEqual(opus["output_tokens"], 100)
        self.assertEqual(opus["cache_read_tokens"], 1000)
        self.assertEqual(opus["cache_write_tokens"], 300)
        self.assertEqual(opus["cache_write_1h_tokens"], 200)
        self.assertEqual(opus["reasoning_tokens"], 40)
        self.assertEqual(opus["project"], "/home/me/work/proj-a")


class CodexTests(unittest.TestCase):
    def setUp(self):
        path = FIXTURES / "codex/2026/10/20/rollout-2026-10-20T09-00-00-abc.jsonl"
        self.events = codex.parse_file(str(path), "acct")

    def test_duplicate_token_count_is_ignored(self):
        self.assertEqual(len(self.events), 2)

    def test_cached_input_is_separated(self):
        first, second = self.events
        self.assertEqual((first["input_tokens"], first["cache_read_tokens"], first["output_tokens"]), (600, 400, 200))
        self.assertEqual((second["input_tokens"], second["cache_read_tokens"], second["output_tokens"]), (500, 1000, 100))
        self.assertEqual(first["model"], "gpt-5.3-codex")
        self.assertEqual(first["session_id"], "abc")
        self.assertEqual(first["project"], "/home/me/work/proj-b")

    def test_missing_model_falls_back(self):
        events = codex.parse_file(str(FIXTURES / "codex/2026/10/20/rollout-legacy.jsonl"), "acct")
        self.assertEqual([(e["model"], e["input_tokens"]) for e in events], [("gpt-5", 100)])


class PricingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pricing = OfflinePricing(Path(self.tmp.name) / "none.json",
                                      {"my-model": {"input": 1, "output": 2}})

    def tearDown(self):
        self.tmp.cleanup()

    def test_anthropic_cost_uses_cache_rates(self):
        tokens = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "cache_read_tokens": 1_000_000,
                  "cache_write_tokens": 1_000_000, "cache_write_1h_tokens": 1_000_000}
        # claude-opus-5-5: $4 in, $20 out, $0.20 cache read, $5 5m write, $8 1h write per Mtok
        self.assertAlmostEqual(self.pricing.cost("claude-opus-5-5", tokens), 37.2)

    def test_date_suffix_and_override(self):
        self.assertIsNotNone(self.pricing.rates("claude-sonnet-5-5-20260901"))
        self.assertAlmostEqual(self.pricing.cost("my-model", {"input_tokens": 1_000_000, "output_tokens": 500_000}), 2.0)
        self.assertIsNone(self.pricing.cost("unknown-model", {"input_tokens": 5}))


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_config(self.tmp.name)
        self.db = Database(self.cfg["database"])
        self.pricing = OfflinePricing(Path(self.tmp.name) / "none.json")

    def tearDown(self):
        self.db.conn.close()
        self.tmp.cleanup()

    def test_sync_is_incremental(self):
        first = sync.run(self.cfg, self.db, self.pricing)
        self.assertEqual(first["files_imported"], 3)
        self.assertEqual(first["errors"], [])
        second = sync.run(self.cfg, self.db, self.pricing)
        self.assertEqual(second["files_imported"], 0)
        self.assertEqual(self.db.query("SELECT COUNT(*) AS n FROM usage_events")[0]["n"], 5)

    def test_renamed_account_reimports(self):
        sync.run(self.cfg, self.db, self.pricing)
        self.cfg["accounts"][1]["id"] = "chatgpt-plus"
        result = sync.run(self.cfg, self.db, self.pricing)
        self.assertEqual(result["files_imported"], 2)
        ids = {r["account_id"] for r in self.db.query("SELECT DISTINCT account_id FROM usage_events")}
        self.assertEqual(ids, {"claude-pro", "chatgpt-plus"})

    def test_report_totals_and_fees(self):
        sync.run(self.cfg, self.db, self.pricing)
        day = date(2026, 10, 20)
        data = report.build(self.cfg, self.db, self.pricing, start=day, end=day, today=day)
        claude = next(a for a in data["by_account"] if a["id"] == "claude-pro")
        expected = (10 * 4 + 100 * 20 + 1000 * 0.2 + 300 * 5 + 200 * 8 + 20 * 0.1 + 30 * 0.5) / 1e6
        self.assertAlmostEqual(claude["cost"], expected)
        self.assertAlmostEqual(claude["fee"], 1.0)  # $31 / 31 days in October
        self.assertEqual(data["totals"]["sessions"], 3)
        self.assertEqual(data["monthly"][0]["fee"], 31)

    def test_http_api(self):
        sync.run(self.cfg, self.db, self.pricing)
        httpd = server.serve(self.cfg, self.db, self.pricing, "127.0.0.1", 0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/api/summary?start=2026-10-01&end=2026-10-31&account=codex") as r:
                data = json.loads(r.read())
            self.assertEqual([a["id"] for a in data["by_account"]], ["codex"])
            with urllib.request.urlopen(base + "/") as r:
                self.assertIn(b"AI Tool Dashboard", r.read())
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(base + "/static/../server.py")
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()
