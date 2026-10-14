"""Real-money gate — nine machine-checkable conditions that must ALL pass
before the paper trader is considered for live money.

Read-only. Used by GET /gate (backend/api.py) and the daily digest.

"Clean window": per-trade stats are only trustworthy for position_lifecycle
rows with id >= CLEAN_START_ID (closed from 2026-06-18). The window starts at
the opened_at of the first clean row.

Regime proxy for item (c): the DB stores no per-day regime column. Each
main-trader prompt in ai_decisions.prompt_user carries the line
"MARKET REGIME: <BULL|CAUTION|BEAR> (...)" that the trader acted on, so a
day's regime is the most common one across that day's prompts. Days with no
prompt fall back to the regime_at_entry of positions opened that day.
"""

from __future__ import annotations

import random
import re
import sqlite3
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import backend.db as _db

CLEAN_START_ID = 58
BOOTSTRAP_RESAMPLES = 1000
BOOTSTRAP_SEED = 20260618

_REGIME = re.compile(r"MARKET REGIME: ([A-Z_]+)")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{_db.DB_PATH}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _item(id_: str, label: str, value: Any, threshold: str, ok: bool) -> Dict[str, Any]:
    return {"id": id_, "label": label, "value": value, "threshold": threshold, "pass": bool(ok)}


def _bootstrap_ci(pnls: List[float]) -> Optional[tuple]:
    if len(pnls) < 2:
        return None
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(pnls)
    means = sorted(sum(rng.choices(pnls, k=n)) / n for _ in range(BOOTSTRAP_RESAMPLES))
    return (round(means[int(0.025 * BOOTSTRAP_RESAMPLES)], 2),
            round(means[int(0.975 * BOOTSTRAP_RESAMPLES) - 1], 2))


def _day_regimes(conn, start: str) -> Dict[str, str]:
    votes: Dict[str, Counter] = {}
    for r in conn.execute(
        "SELECT date(decided_at) AS d,"
        " substr(prompt_user, instr(prompt_user, 'MARKET REGIME: '), 30) AS frag"
        " FROM ai_decisions"
        " WHERE decided_at >= ? AND instr(prompt_user, 'MARKET REGIME: ') > 0",
        (start,),
    ):
        m = _REGIME.match(r["frag"] or "")
        if m:
            votes.setdefault(r["d"], Counter())[m.group(1)] += 1
    fallback: Dict[str, Counter] = {}
    for r in conn.execute(
        "SELECT date(opened_at) AS d, regime_at_entry AS reg FROM position_lifecycle"
        " WHERE id >= ? AND regime_at_entry IS NOT NULL",
        (CLEAN_START_ID,),
    ):
        if r["d"] not in votes:
            fallback.setdefault(r["d"], Counter())[r["reg"].split(":")[0].upper()] += 1
    return {d: c.most_common(1)[0][0] for d, c in {**fallback, **votes}.items()}


def _max_drawdown_pct(values: List[float]) -> float:
    peak, worst = None, 0.0
    for v in values:
        peak = v if peak is None or v > peak else peak
        if peak:
            worst = max(worst, (peak - v) / peak * 100)
    return round(worst, 2)


def compute_gate(now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now()
    conn = _conn()
    try:
        cfg = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM config")}
        first = conn.execute(
            "SELECT min(opened_at) FROM position_lifecycle WHERE id >= ?", (CLEAN_START_ID,)
        ).fetchone()[0]
        window_start = first or now.isoformat()

        pnls = [r[0] for r in conn.execute(
            "SELECT realized_pnl FROM position_lifecycle"
            " WHERE id >= ? AND status = 'closed' AND realized_pnl IS NOT NULL ORDER BY id",
            (CLEAN_START_ID,),
        )]
        n_closed = len(pnls)
        items: List[Dict[str, Any]] = []

        # (a) sample size
        items.append(_item("a", "100+ clean closed trades", n_closed, ">= 100", n_closed >= 100))

        # (b) edge: PF, expectancy, bootstrap CI of the mean excluding 0
        gross_win = sum(p for p in pnls if p > 0)
        gross_loss = -sum(p for p in pnls if p <= 0)
        pf = round(gross_win / gross_loss, 2) if gross_loss else None  # no losses yet → unproven
        expectancy = round(sum(pnls) / n_closed, 2) if n_closed else None
        ci = _bootstrap_ci(pnls)
        edge_ok = (pf is not None and pf >= 1.3 and expectancy is not None and expectancy > 0
                   and ci is not None and ci[0] > 0)
        items.append(_item(
            "b", "Proven edge (PF, expectancy, 95% CI)",
            {"profit_factor": pf, "expectancy": expectancy, "ci95_mean_pnl": list(ci) if ci else None},
            "PF >= 1.3, expectancy > 0, CI excludes 0", edge_ok,
        ))

        # (c) tested outside a bull market
        regimes = _day_regimes(conn, window_start)
        non_bull = sorted(d for d, r in regimes.items() if r != "BULL")
        items.append(_item("c", "20+ trading days outside BULL regime", len(non_bull), ">= 20",
                           len(non_bull) >= 20))

        # (d) capital actually deployed
        cutoff = (now - timedelta(days=30)).isoformat()
        dep = conn.execute(
            "SELECT avg(1.0 - cash / portfolio_value) FROM equity_snapshots"
            " WHERE snapshot_at >= ? AND portfolio_value > 0",
            (cutoff,),
        ).fetchone()[0]
        dep_pct = round(dep * 100, 1) if dep is not None else None
        items.append(_item("d", "Average deployment >= 40% (30d)", dep_pct, ">= 40%",
                           dep_pct is not None and dep_pct >= 40))

        # (e) drawdown over the clean window
        values = [r[0] for r in conn.execute(
            "SELECT portfolio_value FROM equity_snapshots WHERE snapshot_at >= ? ORDER BY snapshot_at, id",
            (window_start,),
        )]
        mdd = _max_drawdown_pct(values) if values else None
        items.append(_item("e", "Max drawdown < 8% (clean window)", mdd, "< 8%",
                           mdd is not None and mdd < 8))

        # (f) (g) safety switches, set by config
        pdt = (cfg.get("pdt_guard_in_paper") or "false").strip().lower()
        items.append(_item("f", "PDT guard active in paper", pdt, "== true", pdt == "true"))
        vfc = (cfg.get("validator_fail_closed") or "false").strip().lower()
        items.append(_item("g", "Validator fails closed", vfc, "== true", vfc == "true"))

        # (h) ledger reconciles with the broker equity curve
        first_snap = conn.execute(
            "SELECT portfolio_value FROM equity_snapshots WHERE snapshot_at >= ?"
            " ORDER BY snapshot_at, id LIMIT 1",
            (window_start,),
        ).fetchone()
        last_snap = conn.execute(
            "SELECT portfolio_value, unrealized_pnl FROM equity_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if first_snap and last_snap:
            gap = round(sum(pnls) + (last_snap["unrealized_pnl"] or 0)
                        - (last_snap["portfolio_value"] - first_snap["portfolio_value"]), 2)
        else:
            gap = None
        items.append(_item("h", "Ledger reconciles with equity (within $50)", gap, "|gap| <= 50",
                           gap is not None and abs(gap) <= 50))

        # (i) live shadow
        try:
            shadow = int(cfg.get("live_shadow_days") or 0)
        except ValueError:
            shadow = 0
        items.append(_item("i", "30+ days of live shadow trading", shadow, ">= 30", shadow >= 30))

        passed = sum(1 for i in items if i["pass"])
        failing = [i for i in items if not i["pass"]]
        return {
            "items": items,
            "passed_count": passed,
            "total": len(items),
            "summary": f"gate: {passed}/{len(items)}",
            "next_blocker": failing[0]["label"] if failing else None,
            "clean_window_start": window_start,
            "non_bull_days": non_bull,
            "computed_at": now.isoformat(timespec="seconds"),
        }
    finally:
        conn.close()


if __name__ == "__main__":
    import json
    print(json.dumps(compute_gate(), indent=2, default=str))
