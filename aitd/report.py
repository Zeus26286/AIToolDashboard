"""Aggregates usage events into the JSON the dashboard renders."""

import calendar
from collections import defaultdict
from datetime import date, timedelta
from pathlib import PurePath

TOKEN_COLUMNS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
                 "cache_write_1h_tokens", "reasoning_tokens")
BILLED_COLUMNS = TOKEN_COLUMNS[:5]  # reasoning is already inside output_tokens
MONTHS_SHOWN = 12


def _sum_sql():
    return ", ".join(f"SUM({c}) AS {c}" for c in TOKEN_COLUMNS)


def _empty_tokens():
    return dict.fromkeys(TOKEN_COLUMNS, 0)


def _add(target, row):
    for c in TOKEN_COLUMNS:
        target[c] += row[c] or 0


def total_tokens(t):
    return sum(t[c] for c in BILLED_COLUMNS)


def project_label(path):
    if not path:
        return "(unknown)"
    parts = PurePath(path).parts
    return "/".join(parts[-2:]) if len(parts) > 2 else path


def _days_in_month(d):
    return calendar.monthrange(d.year, d.month)[1]


def _fee_active(acct, d):
    since = acct.get("since") or acct.get("_first_day")
    return not since or d.isoformat() >= since


def with_fee_start(cfg, db):
    """Accounts without an explicit `since` start paying from their first recorded day."""
    first = {r["account_id"]: r["first"] for r in
             db.query("SELECT account_id, MIN(day) AS first FROM usage_events GROUP BY account_id")}
    return {a["id"]: dict(a, _first_day=first.get(a["id"])) for a in cfg["accounts"]}


def prorated_fee(acct, start, end):
    """Subscription fee attributable to [start, end], day by day."""
    fee = acct.get("monthly_fee") or 0
    if not fee or end < start:
        return 0.0
    total = 0.0
    d = start
    while d <= end:
        if _fee_active(acct, d):
            total += fee / _days_in_month(d)
        d += timedelta(days=1)
    return total


def build(cfg, db, pricing, start=None, end=None, account=None, today=None):
    today = today or date.today()
    accounts = with_fee_start(cfg, db)
    selected = [account] if account in accounts else list(accounts)

    bounds = db.query("SELECT MIN(day) AS first, MAX(day) AS last FROM usage_events")[0]
    if end is None:
        end = today
    if start is None:
        start = date.fromisoformat(bounds["first"]) if bounds["first"] else end - timedelta(days=29)
    if start > end:
        start, end = end, start

    placeholders = ", ".join("?" for _ in selected)
    rows = db.query(
        f"SELECT day, account_id, model, project, {_sum_sql()}, COUNT(*) AS requests "
        f"FROM usage_events WHERE day BETWEEN ? AND ? AND account_id IN ({placeholders}) "
        "GROUP BY day, account_id, model, project",
        (start.isoformat(), end.isoformat(), *selected),
    )
    sessions = db.query(
        f"SELECT COUNT(DISTINCT session_id) AS n FROM usage_events "
        f"WHERE day BETWEEN ? AND ? AND account_id IN ({placeholders})",
        (start.isoformat(), end.isoformat(), *selected),
    )[0]["n"]

    totals = _empty_tokens()
    total_cost = 0.0
    requests = 0
    unpriced = defaultdict(int)
    daily = defaultdict(lambda: defaultdict(lambda: {"cost": 0.0, "tokens": 0}))
    by_model = {}
    by_account = {aid: {"tokens": _empty_tokens(), "cost": 0.0, "requests": 0} for aid in selected}
    by_project = defaultdict(lambda: {"cost": 0.0, "tokens": 0, "accounts": set()})

    for r in rows:
        cost = pricing.cost(r["model"], r)
        tok = total_tokens(r)
        if cost is None:
            unpriced[r["model"]] += tok
            cost = 0.0
        _add(totals, r)
        total_cost += cost
        requests += r["requests"]

        cell = daily[r["day"]][r["account_id"]]
        cell["cost"] += cost
        cell["tokens"] += tok

        m = by_model.setdefault((r["account_id"], r["model"]), {
            "account_id": r["account_id"], "model": r["model"], "tokens": _empty_tokens(),
            "cost": 0.0, "requests": 0, "priced": pricing.rates(r["model"]) is not None,
        })
        _add(m["tokens"], r)
        m["cost"] += cost
        m["requests"] += r["requests"]

        a = by_account[r["account_id"]]
        _add(a["tokens"], r)
        a["cost"] += cost
        a["requests"] += r["requests"]

        p = by_project[project_label(r["project"])]
        p["cost"] += cost
        p["tokens"] += tok
        p["accounts"].add(r["account_id"])

    fee_end = min(end, today)
    account_rows = []
    total_fees = 0.0
    for aid in selected:
        acct = accounts[aid]
        a = by_account[aid]
        fee = prorated_fee(acct, start, fee_end) if acct["kind"] == "subscription" else 0.0
        total_fees += fee
        account_rows.append({
            "id": aid, "name": acct["name"], "provider": acct["provider"], "kind": acct["kind"],
            "monthly_fee": acct.get("monthly_fee") or 0,
            "tokens": a["tokens"], "total_tokens": total_tokens(a["tokens"]),
            "cost": a["cost"], "requests": a["requests"], "fee": fee,
            "value_multiple": (a["cost"] / fee) if fee else None,
        })

    day_list = []
    d = start
    while d <= end:
        key = d.isoformat()
        day_list.append({"day": key, "accounts": {aid: daily[key][aid] for aid in daily.get(key, {})}})
        d += timedelta(days=1)

    model_rows = sorted(by_model.values(), key=lambda m: m["cost"], reverse=True)
    for m in model_rows:
        m["total_tokens"] = total_tokens(m["tokens"])

    project_rows = sorted(
        ({"project": k, "cost": v["cost"], "tokens": v["tokens"], "accounts": sorted(v["accounts"])}
         for k, v in by_project.items()),
        key=lambda p: p["cost"], reverse=True,
    )

    return {
        "range": {"start": start.isoformat(), "end": end.isoformat(),
                  "data_first": bounds["first"], "data_last": bounds["last"]},
        "accounts": [{"id": a["id"], "name": a["name"], "kind": a["kind"]} for a in cfg["accounts"]],
        "selected_account": account if account in accounts else None,
        "totals": {"tokens": totals, "total_tokens": total_tokens(totals), "cost": total_cost,
                   "fees": total_fees, "requests": requests, "sessions": sessions,
                   "value_multiple": (total_cost / total_fees) if total_fees else None},
        "daily": day_list,
        "by_account": account_rows,
        "by_model": model_rows,
        "by_project": project_rows[:25],
        "monthly": monthly(cfg, db, pricing, selected, today),
        "unpriced_models": [{"model": k, "tokens": v} for k, v in sorted(unpriced.items(), key=lambda x: -x[1])],
        "pricing_source": pricing.source,
        "last_sync": db.get_meta("last_sync"),
    }


def monthly(cfg, db, pricing, selected, today):
    """Fee vs API-equivalent value per subscription account for recent calendar months."""
    accounts = with_fee_start(cfg, db)
    subs = [aid for aid in selected if accounts[aid]["kind"] == "subscription"]
    if not subs:
        return []
    first_month = date(today.year, today.month, 1)
    for _ in range(MONTHS_SHOWN - 1):
        first_month = (first_month - timedelta(days=1)).replace(day=1)

    placeholders = ", ".join("?" for _ in subs)
    rows = db.query(
        f"SELECT substr(day, 1, 7) AS month, account_id, model, {_sum_sql()} FROM usage_events "
        f"WHERE day >= ? AND account_id IN ({placeholders}) GROUP BY month, account_id, model",
        (first_month.isoformat(), *subs),
    )
    value = defaultdict(float)
    tokens = defaultdict(int)
    for r in rows:
        value[(r["month"], r["account_id"])] += pricing.cost(r["model"], r) or 0.0
        tokens[(r["month"], r["account_id"])] += total_tokens(r)

    out = []
    m = first_month
    while m <= today:
        month_end = m.replace(day=_days_in_month(m))
        key = m.isoformat()[:7]
        for aid in subs:
            acct = accounts[aid]
            fee = (acct.get("monthly_fee") or 0) if _fee_active(acct, month_end) else 0
            if not fee and not tokens[(key, aid)]:
                continue
            v = value[(key, aid)]
            out.append({"month": key, "account_id": aid, "name": acct["name"], "fee": fee, "value": v, "tokens": tokens[(key, aid)],
                        "value_multiple": (v / fee) if fee else None, "partial": key == today.isoformat()[:7]})
        m = month_end + timedelta(days=1)
    return out
