"""API list prices per model, used to put a dollar value on every token.

Prices come from LiteLLM's community-maintained table (cached under data/ and
refreshed daily), then the bundled fallback table, then config price_overrides
(in USD per million tokens), which win over both.
"""

import json
import re
import time
import urllib.request
from pathlib import Path

LITELLM_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
FALLBACK_PATH = Path(__file__).resolve().parent / "prices_fallback.json"
REFRESH_SECONDS = 24 * 3600
PER_MILLION = 1_000_000

_DATE_SUFFIX = re.compile(r"-(\d{8}|\d{4}-\d{2}-\d{2})$")
_PROVIDER_PREFIXES = ("", "anthropic/", "openai/", "xai/", "gemini/", "deepseek/", "dashscope/")


class Pricing:
    def __init__(self, cache_path, overrides=None):
        self.cache_path = Path(cache_path)
        self.overrides = overrides or {}
        self.table = {}
        self.source = "none"
        self.load()

    def load(self):
        self.table = json.loads(FALLBACK_PATH.read_text())
        self.source = "bundled"
        if self.cache_path.exists():
            try:
                self.table.update(json.loads(self.cache_path.read_text()))
                self.source = "litellm"
            except (OSError, json.JSONDecodeError):
                pass
        self._lookup_cache = {}

    def refresh_if_stale(self, force=False):
        """Downloads the LiteLLM table when the cached copy is over a day old. Returns an error string or None."""
        if not force and self.cache_path.exists() and time.time() - self.cache_path.stat().st_mtime < REFRESH_SECONDS:
            return None
        try:
            with urllib.request.urlopen(LITELLM_URL, timeout=30) as resp:
                data = json.loads(resp.read())
        except Exception as e:  # offline is normal; keep using the cached/bundled table
            return f"price table refresh failed: {e}"
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(self.cache_path)
        self.load()
        return None

    def rates(self, model):
        """Per-token USD rates for a model, or None when it is unknown."""
        if model in self._lookup_cache:
            return self._lookup_cache[model]
        rates = self._from_override(model) or self._from_table(model)
        self._lookup_cache[model] = rates
        return rates

    def _from_override(self, model):
        o = self.overrides.get(model)
        if not o:
            return None
        inp = o.get("input", 0) / PER_MILLION
        return {
            "input": inp,
            "output": o.get("output", 0) / PER_MILLION,
            "cache_read": o.get("cache_read", o.get("input", 0)) / PER_MILLION,
            "cache_write": o.get("cache_write", o.get("input", 0)) / PER_MILLION,
            "cache_write_1h": o.get("cache_write_1h", o.get("input", 0) * 2) / PER_MILLION,
        }

    def _from_table(self, model):
        candidates = [model]
        stripped = _DATE_SUFFIX.sub("", model)
        if stripped != model:
            candidates.append(stripped)
        for name in candidates:
            for prefix in _PROVIDER_PREFIXES:
                entry = self.table.get(prefix + name)
                if isinstance(entry, dict) and "input_cost_per_token" in entry:
                    return _rates_from_entry(entry)
        return None

    def cost(self, model, tokens):
        """USD for a dict of token counts (aitd.db column names), or None if the model is unpriced."""
        r = self.rates(model)
        if r is None:
            return None
        return (
            tokens.get("input_tokens", 0) * r["input"]
            + tokens.get("output_tokens", 0) * r["output"]
            + tokens.get("cache_read_tokens", 0) * r["cache_read"]
            + tokens.get("cache_write_tokens", 0) * r["cache_write"]
            + tokens.get("cache_write_1h_tokens", 0) * r["cache_write_1h"]
        )


def _rates_from_entry(e):
    def rate(key, default):
        value = e.get(key)
        return value if isinstance(value, (int, float)) else default

    inp = rate("input_cost_per_token", 0)
    return {
        "input": inp,
        "output": rate("output_cost_per_token", 0),
        "cache_read": rate("cache_read_input_token_cost", inp),
        "cache_write": rate("cache_creation_input_token_cost", inp),
        "cache_write_1h": rate("cache_creation_input_token_cost_above_1hr", inp * 2),
    }
