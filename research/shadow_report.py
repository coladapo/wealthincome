"""One digest line on the shadow strategies, one segment per sleeve (read-only on data/research.db).

    Shadow · swing: champion_replay −0.2% (2d) · long: — · treasury: treasury_sweep +0.0% (0d)

A sleeve (registry horizon, in research.strategy.HORIZONS order) gets a segment when it
has any registered strategy. The segment names the sleeve's best challenger (registry
status shadow, not a reference series) by shadow TOTAL return since it entered shadow
(research.shadow: dividends credited), with n = shadow trading days (daily returns, the
start day not counted, as in promotion rule P1). "(k)" after the sleeve = k challengers
when there is more than one; "—" = no challenger in that sleeve. A registry champion of
the sleeve is appended as ", champion <id> ±x%". format_shadow_line() never raises: any
failure returns None and the digest simply omits the line.
"""

from __future__ import annotations

import sqlite3
import sys
from typing import Optional

from research.bars import DEFAULT_DB_PATH


def _pct(v: Optional[float]) -> str:
    if v is None:
        return "n/a"
    x = v * 100
    return f"{'+' if x >= 0 else '−'}{abs(x):.1f}%"


def shadow_summary(conn: sqlite3.Connection) -> dict:
    """{sleeve: {"n_challengers", "best": {id, ret, n} | None, "champion": {id, ret, n} | None}}
    for every sleeve that has a registered strategy, in HORIZONS order."""
    from research import strategy as registry
    from research.promotion import load_shadow
    from research.shadow import REFERENCE_IDS

    def record(r):
        sh = load_shadow(conn, r["id"], r["version"])
        ret = float(sh["equity"].iloc[-1] / sh["equity"].iloc[0] - 1.0) if len(sh) else None
        return {"id": r["id"], "ret": ret, "n": max(len(sh) - 1, 0)}

    rows = registry.list_strategies(conn=conn)
    out = {}
    for h in registry.HORIZONS:
        mine = [r for r in rows if r["horizon"] == h]
        if not mine:
            continue
        ch = [record(r) for r in mine if r["status"] == "shadow"
              and r["id"] not in REFERENCE_IDS and not r["reference_only"]]
        ch.sort(key=lambda x: (x["ret"] is not None, x["ret"] or 0.0, x["n"]), reverse=True)
        champs = [r for r in mine if r["status"] == "champion"]
        out[h] = {"n_challengers": len(ch), "best": ch[0] if ch else None,
                  "champion": record(champs[-1]) if champs else None}
    return out


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
        if not s:
            return "Shadow: no strategies registered"
        parts = ["Shadow"]
        for h, v in s.items():
            b = v["best"]
            k = f" ({v['n_challengers']})" if v["n_challengers"] > 1 else ""
            seg = f"{h}{k}: " + ("—" if b is None else f"{b['id']} {_pct(b['ret'])} ({b['n']}d)")
            c = v["champion"]
            if c is not None:
                seg += f", champion {c['id']} {_pct(c['ret'])}"
            parts.append(seg)
        return " · ".join(parts)
    except Exception as e:
        print(f"shadow line failed: {type(e).__name__}: {e}", file=sys.stderr)
        return None
    finally:
        if own and conn is not None:
            conn.close()
