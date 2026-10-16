"""Plan-usage report from the llm_calls ledger (every headless model call).

`format_usage_line()` → the digest line, e.g.
    Usage: 54 brain + 3 validator calls · 2.6M tokens (1.1M cache-read) · $4.5 nominal ·
    plan 5h 73% / week 89% (resets Wed 5am) · 30d $112
`usage_summary()` → today + 30-day totals by caller, for GET /usage.
"Nominal" = list-price value of the tokens; the plan is a flat subscription.
Days are local calendar dates of llm_calls.called_at.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional

_CALLER_ORDER = ("brain", "validator", "research", "other")

_SQL = """
    SELECT caller, count(*) AS calls,
           sum(outcome = 'ok') AS ok,
           coalesce(sum(input_tokens), 0) AS input_tokens,
           coalesce(sum(output_tokens), 0) AS output_tokens,
           coalesce(sum(cache_read_tokens), 0) AS cache_read_tokens,
           coalesce(sum(cache_write_tokens), 0) AS cache_write_tokens,
           coalesce(sum(nominal_usd), 0.0) AS nominal_usd
    FROM llm_calls WHERE date(called_at) >= ?
    GROUP BY caller
"""


def _totals(conn: sqlite3.Connection, since: str) -> Dict[str, Any]:
    by_caller: Dict[str, Dict[str, Any]] = {}
    for r in conn.execute(_SQL, (since,)).fetchall():
        d = dict(zip(("caller", "calls", "ok", "input_tokens", "output_tokens",
                      "cache_read_tokens", "cache_write_tokens", "nominal_usd"), r))
        d["total_tokens"] = (d["input_tokens"] + d["output_tokens"]
                             + d["cache_read_tokens"] + d["cache_write_tokens"])
        d["nominal_usd"] = round(d["nominal_usd"], 4)
        by_caller[d.pop("caller")] = d
    keys = ("calls", "ok", "input_tokens", "output_tokens", "cache_read_tokens",
            "cache_write_tokens", "total_tokens", "nominal_usd")
    total = {k: sum(c[k] for c in by_caller.values()) for k in keys}
    total["nominal_usd"] = round(total["nominal_usd"], 4)
    return {"since": since, "by_caller": by_caller, "total": total}


def _connect(db_path: Optional[str]) -> sqlite3.Connection:
    import backend.db as _db
    return sqlite3.connect(f"file:{db_path or _db.DB_PATH}?mode=ro", uri=True, timeout=30)


def usage_summary(db_path: Optional[str] = None, today: Optional[date] = None,
                  plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from core.plan_guard import plan_state
    today = today or date.today()
    conn = _connect(db_path)
    try:
        out = {"today": _totals(conn, today.isoformat()),
               "last_30d": _totals(conn, (today - timedelta(days=29)).isoformat())}
    finally:
        conn.close()
    out["plan"] = plan if plan is not None else plan_state()
    return out


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def _fmt_usd(x: float) -> str:
    return f"${x:.0f}" if x >= 100 else f"${x:.1f}"


def _fmt_reset(epoch) -> str:
    try:
        dt = datetime.fromtimestamp(float(epoch))
    except (TypeError, ValueError, OSError):
        return ""
    hour = dt.strftime("%I").lstrip("0")
    mins = f":{dt.minute:02d}" if dt.minute else ""
    return f"{dt.strftime('%a')} {hour}{mins}{dt.strftime('%p').lower()}"


def format_usage_line(summary: Optional[Dict[str, Any]] = None) -> str:
    s = summary if summary is not None else usage_summary()
    t, by = s["today"]["total"], s["today"]["by_caller"]
    if by:
        calls = " + ".join(f"{by[c]['calls']} {c}" for c in _CALLER_ORDER if c in by)
        parts = [f"Usage: {calls} calls",
                 f"{_fmt_tokens(t['total_tokens'])} tokens ({_fmt_tokens(t['cache_read_tokens'])} cache-read)",
                 f"{_fmt_usd(t['nominal_usd'])} nominal"]
    else:
        parts = ["Usage: no model calls today"]
    p = s.get("plan") or {}
    if p.get("fresh"):
        five = f"{p['five_hour_pct']:.0f}%" if p.get("five_hour_pct") is not None else "?"
        week = f"{p['week_pct']:.0f}%" if p.get("week_pct") is not None else "?"
        reset = _fmt_reset(p.get("week_resets_at"))
        parts.append(f"plan 5h {five} / week {week}" + (f" (resets {reset})" if reset else ""))
    else:
        parts.append("plan n/a")
    parts.append(f"30d {_fmt_usd(s['last_30d']['total']['nominal_usd'])}")
    return " · ".join(parts)


if __name__ == "__main__":
    print(format_usage_line())
