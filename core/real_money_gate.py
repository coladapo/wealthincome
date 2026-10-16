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

Item (f) used to be "PDT guard active in paper". FINRA's Rule 4210 amendment
(effective 2026-06-04) retired the pattern-day-trader rule and Alpaca runs the
intraday-margin framework in production, so it is replaced by (f'): the day
sleeve's intraday exposure stays within margin excess and it is flat by the
close. While the approved sleeve allocation (research.db sleeve_allocation) gives
the day sleeve 0 there is no intraday exposure to check: the item reports
"not applicable" and passes. Once Chris allocates the day sleeve a share, the
item FAILS until a real exposure/flat-at-close check is built with that sleeve.
"""

from __future__ import annotations

import random
import re
import sqlite3
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import backend.db as _db


def _reconciliation_status():
    """Indirection so tests can monkeypatch; the real one reads broker_fills/fifo_realized."""
    from backend.reconciliation import reconciliation_status
    return reconciliation_status()


def _day_sleeve_weight() -> Optional[float]:
    """The day sleeve's approved capital share from the research store (read-only).

    No store / no sleeve_allocation table / no rows -> 0.0: an allocation can only be
    created by research.strategy.set_allocation (Chris), so none means no day sleeve.
    Any other read error -> None (unknown; the gate item fails closed).
    """
    import os
    from research.bars import DEFAULT_DB_PATH
    if not os.path.exists(DEFAULT_DB_PATH):
        return 0.0
    try:
        conn = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True, timeout=30)
        try:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sleeve_allocation'"
                            ).fetchone() is None:
                return 0.0
            row = conn.execute("SELECT weight FROM sleeve_allocation WHERE sleeve='day' AND date="
                               "(SELECT MAX(date) FROM sleeve_allocation)").fetchone()
            return float(row[0]) if row else 0.0
        finally:
            conn.close()
    except Exception:
        return None

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
        # Same view of config the running system has: code defaults overlaid by
        # whatever rows exist. A missing row means "default", not "false".
        cfg = dict(getattr(_db, "DEFAULT_CONFIG", {}))
        cfg.update({r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM config")})
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

        # (f') day-sleeve intraday exposure (replaces the retired PDT-guard item, see module doc)
        day_w = _day_sleeve_weight()
        if day_w is None:
            items.append(_item("f'", "Day-sleeve intraday exposure within margin excess and flat by close",
                               "unknown — sleeve allocation unreadable", "day sleeve 0, or check passes",
                               False))
        elif day_w <= 0:
            items.append(_item("f'", "Day-sleeve intraday exposure within margin excess and flat by close",
                               "not applicable — no day sleeve yet", "day sleeve 0, or check passes", True))
        else:
            items.append(_item("f'", "Day-sleeve intraday exposure within margin excess and flat by close",
                               f"day sleeve {day_w:.0%} allocated; exposure/flat-at-close check not built",
                               "day sleeve 0, or check passes", False))

        # (g) safety switch, set by config
        vfc = str(cfg.get("validator_fail_closed") or "false").strip().lower()
        items.append(_item("g", "Validator fails closed", vfc, "== true", vfc == "true"))

        # (h) books reconcile with the broker: realized P&L rebuilt FIFO from the
        # broker's own fill history (backend.reconciliation, scripts/reconcile_ledger.py)
        # plus current unrealized must equal the account's equity change since the
        # $100k start, within $50 — and the rebuild must be fresh (<= 7 days).
        # (The old lifecycle-only check hid a $5.5k gap of unbooked partial exits.)
        start_snap = conn.execute(
            "SELECT portfolio_value FROM equity_snapshots ORDER BY snapshot_at, id LIMIT 1"
        ).fetchone()
        last_snap = conn.execute(
            "SELECT portfolio_value, unrealized_pnl FROM equity_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        gap, recon_as_of = None, None
        try:
            recon = _reconciliation_status()
            recon_as_of = recon.get("as_of")
            if recon_as_of and start_snap and last_snap:
                age_days = (now - datetime.fromisoformat(str(recon_as_of)[:19])).days
                if age_days <= 7:
                    gap = round(recon["broker_realized"] + (last_snap["unrealized_pnl"] or 0)
                                - (last_snap["portfolio_value"] - start_snap["portfolio_value"]), 2)
        except Exception:  # reconciliation never run / table missing → item fails
            gap = None
        items.append(_item("h", "Books reconcile with broker fills (within $50, rebuilt <= 7d)",
                           {"residual": gap, "reconciled_at": recon_as_of}, "|residual| <= 50",
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
