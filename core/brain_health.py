"""Brain health — how often the trader's LLM call actually came back with a decision.

Source of truth is the DB (the log lives on whichever machine ran the trader,
so it can be missing or partial — e.g. the 2026-10-14 laptop→mini move):
  asks      = market-open cycles that reached the brain that day, i.e. cycles
              with an ai_decisions row OR an errors row 'llm_no_decision'
  failures  = errors 'llm_no_decision' on market-open cycles that day
  successes = asks − failures
The DB can't tell a timeout from another failure, so `timeouts` is 0 there.
Days are cycles.started_at local dates.

Fallback (DB unavailable, or an explicit log_path is passed) parses
logs/trader.log. One "ask" starts at the trader's
    INFO trader — Asking <provider>/<model> to analyze N symbols...
and resolves to the first outcome after it:
    INFO core.llm_router — [<provider>/<model>] tokens: ...      → success
    ERROR core.llm_router — LLM provider '<p>' timed out after Ns → timeout
    ERROR core.llm_router — LLM provider '<p>' failed: ...        → failure
    WARNING trader — LLM returned no decision                     → failure
("no decision" after a tokens line means the reply didn't parse — that
success is downgraded to a failure.) An ask still in flight at the end of
the log is reported as `pending` and left out of the rate.

Log timestamps are local time, so `day` is a local calendar date.
"""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import date
from typing import Dict, List, Optional

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = os.path.join(_BASE, "logs", "trader.log")

_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2}")
_ASK = re.compile(r" trader — Asking \S+ to analyze ")
_OK = re.compile(r" core\.llm_router — \[[^\]]+\] tokens: ")
_TIMEOUT = re.compile(r" core\.llm_router — LLM provider '[^']+' timed out after ")
_FAILED = re.compile(r" core\.llm_router — LLM provider '[^']+' failed: ")
_NO_DECISION = re.compile(r" trader — LLM returned no decision")


def _empty(day: str) -> Dict:
    return {"date": day, "asks": 0, "successes": 0, "timeouts": 0,
            "failures": 0, "pending": 0, "success_rate_pct": None}


def _parse(log_path: str) -> Dict[str, Dict]:
    """date-string → stats, for every day that appears in the log."""
    days: Dict[str, Dict] = {}
    if not os.path.exists(log_path):
        return days

    current_day: Optional[str] = None
    outcome: Optional[str] = None  # None = in flight; 'ok' | 'timeout' | 'failed'
    in_ask = False

    def finish():
        if not in_ask or current_day is None:
            return
        s = days.setdefault(current_day, _empty(current_day))
        if outcome == "ok":
            s["successes"] += 1
        elif outcome == "timeout":
            s["timeouts"] += 1
        elif outcome == "failed":
            s["failures"] += 1
        else:
            s["pending"] += 1

    with open(log_path, errors="replace") as f:
        for line in f:
            m = _TS.match(line)
            if not m:
                continue
            if _ASK.search(line):
                finish()
                current_day = m.group(1)
                days.setdefault(current_day, _empty(current_day))
                in_ask, outcome = True, None
            elif not in_ask:
                continue
            elif _OK.search(line):
                if outcome is None:
                    outcome = "ok"
            elif _TIMEOUT.search(line):
                if outcome is None:
                    outcome = "timeout"
            elif _FAILED.search(line):
                if outcome is None:
                    outcome = "failed"
            elif _NO_DECISION.search(line):
                if outcome in (None, "ok"):
                    outcome = "failed"
    finish()

    for s in days.values():
        s["asks"] = s["successes"] + s["timeouts"] + s["failures"]
        if s["asks"]:
            s["success_rate_pct"] = round(s["successes"] / s["asks"] * 100, 1)
    return days


_DB_SQL = """
    SELECT date(c.started_at) AS d,
           count(*) AS asks,
           sum(EXISTS (SELECT 1 FROM errors e WHERE e.cycle_id = c.id
                       AND e.error_type = 'llm_no_decision')) AS failures
    FROM cycles c
    WHERE c.market_open = 1 {day_filter}
      AND (EXISTS (SELECT 1 FROM ai_decisions a WHERE a.cycle_id = c.id)
           OR EXISTS (SELECT 1 FROM errors e WHERE e.cycle_id = c.id
                      AND e.error_type = 'llm_no_decision'))
    GROUP BY d
"""


def _from_db(day: Optional[str] = None) -> Dict[str, Dict]:
    """date-string → stats from the DB. Raises sqlite3.Error if the DB is unavailable."""
    import backend.db as _db
    conn = sqlite3.connect(f"file:{_db.DB_PATH}?mode=ro", uri=True, timeout=30)
    try:
        sql = _DB_SQL.format(day_filter="AND date(c.started_at) = ?" if day else "")
        rows = conn.execute(sql, (day,) if day else ()).fetchall()
    finally:
        conn.close()
    days: Dict[str, Dict] = {}
    for d, asks, failures in rows:
        s = _empty(d)
        s.update(asks=asks, failures=failures, successes=asks - failures, source="db",
                 success_rate_pct=round((asks - failures) / asks * 100, 1) if asks else None)
        days[d] = s
    return days


def _days(day: Optional[str], log_path: Optional[str]) -> Dict[str, Dict]:
    if log_path is None:
        try:
            return _from_db(day)
        except sqlite3.Error:
            pass
    days = _parse(log_path or LOG_PATH)
    for s in days.values():
        s["source"] = "log"
    return days


def brain_stats(day: Optional[date] = None, log_path: Optional[str] = None) -> Dict:
    """Stats for one trading day (default: today, local time)."""
    key = (day or date.today()).isoformat()
    return _days(key, log_path).get(key) or _empty(key)


def recent_sessions(n: int = 5, log_path: Optional[str] = None) -> List[Dict]:
    """The last n days that had at least one ask, newest first."""
    days = _days(None, log_path)
    active = [s for s in days.values() if s["asks"] or s["pending"]]
    return sorted(active, key=lambda s: s["date"], reverse=True)[:n]


if __name__ == "__main__":
    import json
    print(json.dumps({"today": brain_stats(), "recent": recent_sessions()}, indent=2))
