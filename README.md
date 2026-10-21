# AIToolDashboard

A local web dashboard showing how many tokens you use across your AI tools and what that usage is worth.

For **subscriptions** (Claude Pro, ChatGPT Plus…) it compares the monthly fee you pay with the
**API-equivalent cost**: what the same tokens would have cost at pay-as-you-go API prices. That tells
you whether each subscription earns its price.

It runs on your own computer as a small web server. Your logs and numbers never leave the machine
(the only outbound request is a daily download of the public model price table).

## What it reads today

| Source | Where the data comes from | Accuracy |
|---|---|---|
| **Claude Code** (Claude Pro/Max) | `~/.claude/projects/**/*.jsonl` | Exact token counts from the API's own usage records |
| **Codex CLI** (ChatGPT plan or API key) | `~/.codex/sessions/**/*.jsonl` | Exact token counts from Codex's token events |

Token totals for Claude Code match [`ccusage`](https://github.com/ryoppippi/ccusage).

Chat in the claude.ai / chatgpt.com web and mobile apps is **not** included. Those apps don't
expose token counts (see [Roadmap](#roadmap)).

## Quick start

Requires Python 3.9+. There is nothing to install; it uses only the standard library.

```bash
git clone https://github.com/zeus26286/aitooldashboard.git
cd aitooldashboard
python3 -m aitd serve
```

Open **http://localhost:8787**. On first run, `config.json` is created from `config.example.json`.
Edit it to match your accounts (below), then restart.

Other commands:

```bash
python3 -m aitd sync     # import new log data once and exit
python3 -m aitd report   # print a summary in the terminal
python3 -m aitd serve --port 9000
python3 -m aitd serve --host 0.0.0.0   # let your phone/other devices on the same Wi-Fi open it
```

The server re-imports changed log files every 10 minutes (`sync_interval_minutes`), and the
**Sync now** button does it on demand.

> **Keep it running regularly.** Claude Code deletes session logs older than 30 days by default.
> Everything already imported stays in the dashboard database (`data/usage.db`), so syncing at least
> once every few weeks keeps your full history.

## Configuration (`config.json`)

```json
{
  "server": { "host": "127.0.0.1", "port": 8787, "sync_interval_minutes": 10 },
  "accounts": [
    {
      "id": "claude-pro",
      "name": "Claude Pro",
      "provider": "anthropic",
      "kind": "subscription",
      "monthly_fee": 20,
      "since": null,
      "sources": [{ "type": "claude_code" }]
    },
    {
      "id": "codex",
      "name": "Codex CLI",
      "provider": "openai",
      "kind": "subscription",
      "monthly_fee": 0,
      "sources": [{ "type": "codex" }]
    }
  ],
  "price_overrides": {}
}
```

- **`kind`**: `subscription` (a flat monthly fee) or `api` (pay per token).
- **`monthly_fee`**: what you pay per month, in USD. Set the Codex account to your ChatGPT plan's
  price if you sign in to Codex with ChatGPT, or change its `kind` to `api` if you use an API key.
- **`since`**: the date (`"2026-03-01"`) your subscription started. If left `null`, fees count from
  the first day with recorded usage.
- **`sources[].path`**: optional. Defaults are `~/.claude/projects` (plus `$CLAUDE_CONFIG_DIR`) and
  `~/.codex/sessions` (plus `$CODEX_HOME`). Give a string or a list to read other folders, for
  example logs copied from a second computer.
- **`price_overrides`**: USD per **million** tokens, for models missing from the price table:
  `{"my-model": {"input": 3, "output": 15, "cache_read": 0.3, "cache_write": 3.75}}`.

## How costs are calculated

Every request's tokens are priced at the model's public API list price:

```
cost = input × input_price
     + output × output_price            (includes reasoning/thinking tokens)
     + cache_read × cache_read_price
     + cache_write_5m × cache_write_price
     + cache_write_1h × cache_write_1h_price
```

Prices come from [LiteLLM's model price table](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json).
It is refreshed daily into `data/litellm_prices.json`, with a bundled fallback table for offline
use. Models with no known price are flagged on the dashboard and count as $0 until you add an
override. Long-context surcharges and batch discounts are not applied.

## Dashboard

- **Totals**: API-equivalent cost, subscription fees (prorated to the selected range), value
  multiple (API-equivalent ÷ fees), and tokens / requests / sessions.
- **Daily chart**: cost or tokens per day, stacked by account, with a table view.
- **Subscription value by month**: fee vs API-equivalent for each subscription.
- **Cost by model** and **cost by project** (the folder you ran the CLI in).
- **Token breakdown**: input / output / cache read / cache write per account and model.

## Project layout

```
aitd/
  __main__.py        CLI: serve / sync / report
  config.py          config.json loading and validation
  collectors/        log parsers (claude_code.py, codex.py)
  db.py              SQLite storage (data/usage.db)
  pricing.py         model price lookup and cost math
  report.py          aggregation behind /api/summary
  server.py          HTTP server and JSON API
  static/            the dashboard page (no external scripts)
tests/               python3 -m unittest discover -s tests
```

## Roadmap

1. **API account collectors**: Anthropic Usage & Cost Admin API, OpenAI Usage/Costs API, and
   xAI Management API (exact billed cost), plus Gemini via Cloud Billing export. DeepSeek and Qwen
   need balance polling or console CSV import.
2. **Chat app exports**: import ChatGPT / Claude.ai / Gemini / Grok data exports and estimate
   tokens with a tokenizer (shown as *estimated*, since hidden reasoning and system prompts aren't
   in exports).
3. **More CLIs**: Gemini CLI and others with local session logs.
4. **Task summaries**: group sessions into kinds of work (coding, writing, research…) with an LLM
   and show cost per category.
