"""One digest line on the shadow challengers (read-only on data/research.db).

    Shadow: 2 challengers · best +0.4%/mo vs champion (n=12d) · SPY capped −0.2%/mo

"best" = the challenger (registry status shadow, not a reference series) with the
highest mean daily excess return over the champion's real equity, on the dates both
were observed, x21 for a month; n = days compared. "SPY capped" is the same number for
SPY held under the live caps. format_shadow_line() never raises: any failure returns
None and the digest simply omits the line.
"""

from __future__ import annotations

import sqlite3
import sys
from typing import Optional

from research.bars import DEFAULT_DB_PATH

DAYS_PER_MONTH = 21


def _mo(v: Optional[float]) -> str:
    if v is None:
        return "n/a"
    x = v * DAYS_PER_MONTH * 100
    return f"{'+' if x >= 0 else '−'}{abs(x):.1f}%/mo"


def shadow_summary(conn: sqlite3.Connection) -> dict:
    from research import strategy as registry
    from research.promotion import load_benchmark, load_shadow
    from research.shadow import CHAMPION_SERIES, REFERENCE_IDS
    from research.walk_forward import excess_from_equity

    champ = load_benchmark(conn, CHAMPION_SERIES)

    def excess(sid, ver):
        sh = load_shadow(conn, sid, ver)
        if len(sh) < 2 or len(champ) < 2:
            return None, max(len(sh) - 1, 0)
        e = excess_from_equity(sh["equity"], champ, n_boot=200)
        return e.get("mean_daily"), int(e.get("n_days") or 0)

    rows = [r for r in registry.list_strategies(status="shadow", conn=conn)
            if r["id"] not in REFERENCE_IDS and not r["reference_only"]]
    best = None
    for r in rows:
        m, n = excess(r["id"], r["version"])
        if best is None or (m is not None and (best["mean"] is None or m > best["mean"])):
            best = {"id": r["id"], "mean": m, "n": n}
    spy = None
    caps = [r for r in registry.list_strategies(conn=conn) if r["id"] == "spy_hold_capped"]
    if caps:
        m, n = excess(caps[-1]["id"], caps[-1]["version"])
        spy = {"mean": m, "n": n}
    return {"n_challengers": len(rows), "best": best, "spy_capped": spy}


def format_shadow_line(conn: Optional[sqlite3.Connection] = None,
                       db_path: Optional[str] = None) -> Optional[str]:
    own = conn is None
    try:
        if own:
            conn = sqlite3.connect(f"file:{db_path or DEFAULT_DB_PATH}?mode=ro", uri=True, timeout=10)
        has = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='shadow_equity'").fetchone()
        if not has:
            return "Shadow: not started"
        s = shadow_summary(conn)
        k = s["n_challengers"]
        parts = [f"Shadow: {k} challenger{'s' if k != 1 else ''}"]
        b = s["best"]
        if b is not None:
            parts.append(f"best {_mo(b['mean'])} vs champion (n={b['n']}d)")
        if s["spy_capped"] is not None:
            parts.append(f"SPY capped {_mo(s['spy_capped']['mean'])}")
        return " · ".join(parts)
    except Exception as e:
        print(f"shadow line failed: {type(e).__name__}: {e}", file=sys.stderr)
        return None
    finally:
        if own and conn is not None:
            conn.close()
