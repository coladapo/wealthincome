"""Promotion and demotion rules for the champion-challenger loop. Rules in code, no judgment.

Everything is PER SLEEVE (registry ``horizon``: day | swing | long | treasury). A
challenger is judged against two series of its own sleeve:

  CHAMPION  the registry champion OF ITS SLEEVE (its shadow equity); a sleeve with no
            champion falls back to the sleeve's benchmark (SLEEVE_FALLBACK):
              treasury  cash at 0 (a flat series)
              long      SPY (total return, benchmark_equity "SPY")
              swing     the live picker's real paper equity (benchmark_equity "champion")
              day       SPY (an intraday SPY hold is not available on daily bars)
  MARKET    SPY for day / swing / long; cash at 0 for treasury (SLEEVE_MARKET). The
            treasury sleeve's benchmark to beat is the risk-free 0 (sleeve-design.md):
            requiring a T-bill fund to beat SPY would make the sleeve unpromotable by
            construction.

PROMOTION never happens here. ``evaluate_promotion`` only writes a PROPOSAL file
(data/persistent/promotion-proposals/<strategy>-<date>.md) and posts one line to the
#wealthincome channel when ALL of these hold over the challenger's whole shadow
record (research/shadow.py), compared on the dates both series were observed:

  P1  >= 60 shadow trading days (daily returns, i.e. the start day is not counted)
  P2  mean daily excess return vs the sleeve CHAMPION > 0 with the date-clustered 95%
      bootstrap CI entirely above 0 (research.compare via
      research.walk_forward.excess_from_equity)
  P3  the same vs the MARKET series (SPY close-to-close total return, cost-free)
  P4  still positive at 2x cost: with every virtual fill's cost doubled
      (ret_2x = ret - cost_day / equity_prev_day) the mean daily excess vs the
      champion AND vs the market are both > 0
  P5  max drawdown over the shadow window no deeper than the champion's over the
      same dates
  P6  no calendar month in which the challenger trails the market by more than 3 points
  P7  deflated Sharpe ratio (research.selection) > 0.5, deflated by the number of
      trials logged for the strategy's pre-registered hypothesis (N >= 1)

Reference strategies (research.shadow.REFERENCE_IDS, or any registry row marked
reference_only) are never evaluated or proposed.

The only path to champion is Chris, by hand:
    venv/bin/python -m research.pipeline promote <id> <version> --approved-by chris
which calls research.strategy.set_status(..., approved_by="chris").

DEMOTION is automatic (``evaluate_demotion``), run daily on every sleeve's champion:
  D1  over the last 60 shadow trading days the champion's mean daily excess vs its
      sleeve's MARKET series (SPY; cash for treasury) has a date-clustered 95% CI
      entirely below 0 AND its max drawdown inside that same 60-day window is worse
      than -8%;   or
  D2  its equity has fallen more than 12% below its equity on the promotion day.
On D1 or D2 the champion is set to ``retired`` and the PREVIOUS champion OF THE SAME
SLEEVE (the most recently demoted registry row of that horizon that was itself once
promoted by Chris) is restored to ``champion`` -- the rollback path. With no previous
champion in the sleeve the rollback target is the sleeve's fallback: for swing the
live trader, which is the champion by definition (nothing to change); for the others
the sleeve has no champion and is judged against its benchmark.
Each demotion writes data/persistent/demotion-reports/<strategy>-<date>.md and posts
one line. The rollback passes approved_by="chris" to the registry because it restores
a version Chris already approved; no NEW version can reach champion this way.

THE LIVE ENGINE SWITCH IS NOT HERE. A registry champion only trades live once
backend/engine_registry.py (Phase C, not built) is wired and the config key
``live_engine`` is flipped to it. Until then the live trader (backend/trader.py) is the
champion by definition, the registry champion is a record only, and the champion's
equity used by P2/P5 is the live account's real equity (benchmark_equity "champion").
The demotion rules read the registry champion's SHADOW equity (its real equity once
Phase C exists).
"""

from __future__ import annotations

import importlib.util
import math
import os
import sqlite3
import sys
from datetime import date
from typing import Callable, Dict, List, Optional

import pandas as pd

from research import experiments, selection
from research import strategy as registry
from research.bars import DEFAULT_DB_PATH, REPO_ROOT, connect
from research.walk_forward import excess_from_equity

MIN_SHADOW_DAYS = 60
MONTH_GAP_LIMIT = 0.03
DSR_MIN = 0.5
COST_STRESS = 2.0
DEMOTE_WINDOW = 60
DEMOTE_DRAWDOWN = 0.08
DEMOTE_FALL_FROM_PROMOTION = 0.12

CASH = "cash"
SLEEVE_FALLBACK = {"treasury": CASH, "long": "SPY", "swing": "champion", "day": "SPY"}
SLEEVE_MARKET = {"treasury": CASH, "long": "SPY", "swing": "SPY", "day": "SPY"}
FALLBACK_LABEL = {CASH: "cash", "SPY": "SPY", "champion": "the live picker's real equity"}

DATA_DIR = os.path.dirname(os.path.abspath(DEFAULT_DB_PATH))
PROPOSAL_DIR = os.path.join(DATA_DIR, "persistent", "promotion-proposals")
DEMOTION_DIR = os.path.join(DATA_DIR, "persistent", "demotion-reports")
DIGEST_SCRIPT = os.path.join(REPO_ROOT, "scripts", "daily_digest.py")


# --------------------------------------------------------------------------- series helpers

def max_drawdown(eq: pd.Series) -> float:
    eq = eq.astype(float).dropna()
    if len(eq) == 0:
        return 0.0
    return float((eq / eq.cummax() - 1.0).min())


def stressed_equity(shadow: pd.DataFrame, mult: float = COST_STRESS) -> pd.Series:
    """Equity with every day's virtual costs scaled by `mult` (returns re-compounded)."""
    eq = shadow["equity"].astype(float)
    extra = shadow["costs"].astype(float) * (mult - 1.0)
    prev = eq.shift(1).fillna(eq.iloc[0])
    r = eq / prev - 1.0 - extra / prev
    return (float(eq.iloc[0]) * (1.0 + r).cumprod()).rename("equity")


def monthly_gaps(strat_eq: pd.Series, bench_eq: pd.Series) -> pd.Series:
    """Per calendar month: strategy return minus benchmark return, on common dates."""
    j = pd.concat([strat_eq.astype(float).rename("s"), bench_eq.astype(float).rename("b")],
                  axis=1, join="inner").dropna().sort_index()
    r = j.pct_change().iloc[1:]
    if r.empty:
        return pd.Series(dtype=float)
    m = (1 + r).groupby(r.index.to_period("M")).prod() - 1
    return (m["s"] - m["b"]).rename("gap")


def _ok(v) -> bool:
    return v is not None and isinstance(v, (int, float)) and math.isfinite(v)


# --------------------------------------------------------------------------- the rules (pure)

def promotion_checks(shadow: pd.DataFrame, champion_eq: Optional[pd.Series], spy_close: pd.Series,
                     n_trials: int = 1, trial_sharpes: Optional[List[float]] = None,
                     market_label: str = "SPY") -> Dict:
    """Evaluate P1..P7. `shadow`: DataFrame indexed by date with equity, costs columns.
    `spy_close` is the sleeve's market series (SPY, or cash for treasury: `market_label`)."""
    shadow = shadow.sort_index()
    eq = shadow["equity"].astype(float)
    n_days = max(len(eq) - 1, 0)
    champ = champion_eq if champion_eq is not None else pd.Series(dtype=float)
    vs_c = excess_from_equity(eq, champ) if len(champ) else {"n_days": 0, "mean_daily": None}
    vs_s = excess_from_equity(eq, spy_close)
    eq2 = stressed_equity(shadow)
    vs_c2 = excess_from_equity(eq2, champ) if len(champ) else {"n_days": 0, "mean_daily": None}
    vs_s2 = excess_from_equity(eq2, spy_close)

    common = eq.index.intersection(champ.index) if len(champ) else pd.DatetimeIndex([])
    dd_s = max_drawdown(eq.reindex(common)) if len(common) else None
    dd_c = max_drawdown(champ.reindex(common)) if len(common) else None
    gaps = monthly_gaps(eq, spy_close)
    r = eq.pct_change().iloc[1:].values
    dsr = selection.deflated_sharpe(r, n_trials=max(int(n_trials), 1), trial_sharpes=trial_sharpes)

    def pos_ci(x):
        return _ok(x.get("mean_daily")) and x["mean_daily"] > 0 and _ok(x.get("ci_low")) and x["ci_low"] > 0

    checks = {
        f"P1 >= {MIN_SHADOW_DAYS} shadow days": n_days >= MIN_SHADOW_DAYS,
        "P2 beats champion (CI > 0)": pos_ci(vs_c),
        f"P3 beats {market_label} (CI > 0)": pos_ci(vs_s),
        f"P4 positive at {COST_STRESS:g}x cost vs champion and {market_label}":
            (_ok(vs_c2.get("mean_daily")) and vs_c2["mean_daily"] > 0
             and _ok(vs_s2.get("mean_daily")) and vs_s2["mean_daily"] > 0),
        "P5 max drawdown <= champion's": dd_s is not None and dd_c is not None and dd_s >= dd_c - 1e-12,
        f"P6 no month > {MONTH_GAP_LIMIT * 100:g} pts behind {market_label}":
            len(gaps) > 0 and float(gaps.min()) >= -MONTH_GAP_LIMIT - 1e-12,
        f"P7 deflated Sharpe > {DSR_MIN}": _ok(dsr.get("dsr")) and dsr["dsr"] > DSR_MIN,
    }
    return {"passed": all(checks.values()), "checks": checks,
            "failing": [k for k, v in checks.items() if not v],
            "n_days": n_days, "vs_champion": vs_c, "vs_spy": vs_s,
            "vs_champion_2x": vs_c2, "vs_spy_2x": vs_s2,
            "max_drawdown": dd_s, "champion_max_drawdown": dd_c,
            "worst_month_gap": float(gaps.min()) if len(gaps) else None,
            "worst_month": str(gaps.idxmin()) if len(gaps) else None,
            "dsr": dsr}


def demotion_checks(champ_eq: pd.Series, spy_close: pd.Series,
                    promotion_equity: Optional[float], market_label: str = "SPY") -> Dict:
    """Evaluate D1, D2 on the champion's equity series (indexed by date) vs the sleeve's market."""
    champ_eq = champ_eq.astype(float).dropna().sort_index()
    j = pd.concat([champ_eq.rename("s"), spy_close.astype(float).rename("b")],
                  axis=1, join="inner").dropna().sort_index()
    win = j.iloc[-(DEMOTE_WINDOW + 1):]
    have_window = len(win) - 1 >= DEMOTE_WINDOW
    ex = excess_from_equity(win["s"], win["b"]) if have_window else {"n_days": len(win) - 1, "mean_daily": None}
    dd = max_drawdown(win["s"]) if have_window else None
    d1 = bool(have_window and _ok(ex.get("ci_high")) and ex["ci_high"] < 0
              and dd is not None and dd < -DEMOTE_DRAWDOWN)
    fall = (float(champ_eq.iloc[-1]) / promotion_equity - 1.0
            if promotion_equity and len(champ_eq) else None)
    d2 = bool(fall is not None and fall < -DEMOTE_FALL_FROM_PROMOTION)
    checks = {f"D1 {DEMOTE_WINDOW}d excess vs {market_label} CI < 0 and drawdown > {DEMOTE_DRAWDOWN:.0%}": d1,
              f"D2 fallen > {DEMOTE_FALL_FROM_PROMOTION:.0%} from promotion-day equity": d2}
    return {"demote": d1 or d2, "checks": checks, "window_excess": ex, "window_drawdown": dd,
            "fall_from_promotion": fall, "n_days": int(len(champ_eq) - 1)}


# --------------------------------------------------------------------------- data access

def load_shadow(conn: sqlite3.Connection, sid: str, version: str) -> pd.DataFrame:
    df = pd.read_sql_query("SELECT date, equity, cash, invested_pct, daily_return, costs FROM shadow_equity "
                           "WHERE strategy_id=? AND version=? ORDER BY date", conn, params=(sid, version),
                           parse_dates=["date"])
    return df.set_index("date")


def load_benchmark(conn: sqlite3.Connection, series: str) -> pd.Series:
    df = pd.read_sql_query("SELECT date, equity FROM benchmark_equity WHERE series=? ORDER BY date", conn,
                           params=(series,), parse_dates=["date"])
    return df.set_index("date")["equity"].astype(float).rename(series)


def cash_series(index) -> pd.Series:
    """Cash at 0%: a flat equity series on `index`."""
    return pd.Series(1.0, index=pd.DatetimeIndex(index), name=CASH)


def _sleeve(row: Dict) -> str:
    return row.get("horizon") or "swing"


def sleeve_champion(conn: sqlite3.Connection, horizon: str) -> Optional[Dict]:
    """The registry champion of one sleeve (most recently promoted if, oddly, several)."""
    rows = registry.list_strategies(status="champion", horizon=horizon, conn=conn)
    rows.sort(key=lambda r: (r["promoted_at"] or "", r["created_at"]))
    return rows[-1] if rows else None


def benchmark_series(conn: sqlite3.Connection, name: str, index) -> pd.Series:
    """A sleeve benchmark by name: cash (flat on `index`) or a benchmark_equity series."""
    return cash_series(index) if name == CASH else load_benchmark(conn, name)


def sleeve_comparators(conn: sqlite3.Connection, row: Dict, index) -> Dict:
    """What `row` is judged against inside its sleeve: {champion: (label, series),
    market: (label, series)}. `index`: the dates of the series being judged (for cash)."""
    h = _sleeve(row)
    champ = sleeve_champion(conn, h)
    if champ is not None and (champ["id"], champ["version"]) != (row["id"], row["version"]):
        c = (f"{h} champion {champ['id']} v{champ['version'][:12]}",
             load_shadow(conn, champ["id"], champ["version"])["equity"].astype(float))
    else:
        fb = SLEEVE_FALLBACK[h]
        c = (f"{FALLBACK_LABEL[fb]} (no {h} champion)", benchmark_series(conn, fb, index))
    mk = SLEEVE_MARKET[h]
    return {"champion": c, "market": (FALLBACK_LABEL[mk], benchmark_series(conn, mk, index))}


def hypothesis_trials(conn: sqlite3.Connection, hypothesis_id: Optional[str]):
    if not hypothesis_id:
        return 1, None
    experiments.ensure_schema(conn)
    res = experiments.trial_results(hypothesis_id, conn=conn)
    srs = [t["result"].get("sharpe_daily") for t in res]
    srs = [s for s in srs if _ok(s)]
    return max(len(res), 1), (srs or None)


# --------------------------------------------------------------------------- posting

def digest_poster() -> Callable[[str], bool]:
    """The daily digest's webhook helper, imported from scripts/daily_digest.py (never prints the URL)."""
    spec = importlib.util.spec_from_file_location("wealthincome_daily_digest", DIGEST_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return lambda text: mod._post_via_webhook(text, attempts=2, delay=5)


def _post(text: str, post: bool, poster) -> bool:
    if not post:
        return False
    try:
        return bool((poster or digest_poster())(text))
    except Exception as e:
        print(f"promotion post failed: {type(e).__name__}", file=sys.stderr)
        return False


# --------------------------------------------------------------------------- promotion

def _pct(v, nd=2):
    return "-" if not _ok(v) else f"{v * 100:+.{nd}f}%"


def _proposal_text(row: Dict, chk: Dict, today: str) -> str:
    c, s = chk["vs_champion"], chk["vs_spy"]
    m = chk.get("market_label", "SPY")
    lines = [f"# Promotion proposal: {row['id']} v{row['version'][:12]} ({today})", "",
             f"Sleeve: {chk.get('sleeve', _sleeve(row))}; champion compared: "
             f"{chk.get('champion_label', 'champion')}; market: {m}.", "",
             "Every rule in research/promotion.py passed. This is a PROPOSAL; nothing has changed.", "",
             "To approve (Chris only):", "",
             f"    venv/bin/python -m research.pipeline promote {row['id']} {row['version'][:12]} --approved-by chris",
             "", "A registry champion does not trade live until backend/engine_registry.py (Phase C) is "
             "wired and config `live_engine` is flipped.", "",
             f"- params: {registry.canonical_params(row['params'])}",
             f"- hypothesis: {row.get('hypothesis_id')}",
             f"- shadow days: {chk['n_days']}",
             f"- excess vs champion: {_pct(c.get('mean_daily'), 3)}/day, 95% CI "
             f"[{_pct(c.get('ci_low'), 3)}, {_pct(c.get('ci_high'), 3)}] (n={c.get('n_days')})",
             f"- excess vs {m}: {_pct(s.get('mean_daily'), 3)}/day, 95% CI "
             f"[{_pct(s.get('ci_low'), 3)}, {_pct(s.get('ci_high'), 3)}] (n={s.get('n_days')})",
             f"- at 2x cost: vs champion {_pct(chk['vs_champion_2x'].get('mean_daily'), 3)}/day, "
             f"vs {m} {_pct(chk['vs_spy_2x'].get('mean_daily'), 3)}/day",
             f"- max drawdown {_pct(chk['max_drawdown'])} vs champion {_pct(chk['champion_max_drawdown'])}",
             f"- worst month vs {m}: {_pct(chk['worst_month_gap'])} ({chk['worst_month']})",
             f"- deflated Sharpe {chk['dsr'].get('dsr'):.3f} (N={chk['dsr'].get('n_trials')} trials)", "",
             "| rule | pass |", "|---|---|"]
    lines += [f"| {k} | {'yes' if v else 'no'} |" for k, v in chk["checks"].items()]
    return "\n".join(lines) + "\n"


def evaluate_promotion(row: Dict, conn: sqlite3.Connection, today=None, post: bool = True,
                       poster=None, out_dir: Optional[str] = None) -> Dict:
    """Rule check for one challenger; writes a proposal + posts one line only when all pass."""
    from research.shadow import REFERENCE_IDS
    if row["id"] in REFERENCE_IDS or row.get("reference_only"):
        return {"id": row["id"], "version": row["version"], "passed": False, "skipped": "reference only"}
    if row["status"] != "shadow":
        return {"id": row["id"], "version": row["version"], "passed": False,
                "skipped": f"status {row['status']} (only shadow strategies are proposed)"}
    shadow = load_shadow(conn, row["id"], row["version"])
    if len(shadow) < 2:
        return {"id": row["id"], "version": row["version"], "passed": False, "n_days": max(len(shadow) - 1, 0),
                "failing": [f"P1 >= {MIN_SHADOW_DAYS} shadow days"]}
    n, srs = hypothesis_trials(conn, row.get("hypothesis_id"))
    cmp_ = sleeve_comparators(conn, row, shadow.index)
    (c_label, c_eq), (m_label, m_eq) = cmp_["champion"], cmp_["market"]
    chk = promotion_checks(shadow, c_eq, m_eq, n_trials=n, trial_sharpes=srs, market_label=m_label)
    chk.update(sleeve=_sleeve(row), champion_label=c_label, market_label=m_label)
    out = {"id": row["id"], "version": row["version"], "sleeve": _sleeve(row), "vs": c_label,
           **{k: chk[k] for k in ("passed", "failing", "n_days")}}
    if not chk["passed"]:
        return out
    today = pd.Timestamp(today or date.today()).strftime("%Y-%m-%d")
    d = out_dir or PROPOSAL_DIR
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{row['id']}-{today}.md")
    out["proposal"] = path
    if os.path.exists(path):          # already proposed today: no second post
        return out
    with open(path, "w") as f:
        f.write(_proposal_text(row, chk, today))
    c = chk["vs_champion"]
    out["posted"] = _post(f":chart_with_upwards_trend: Promotion proposal: {row['id']} ({_sleeve(row)} sleeve) "
                          f"beat {c_label} ({_pct(c['mean_daily'] * 21, 1)}/mo) and {m_label} over "
                          f"{chk['n_days']} shadow days; "
                          f"waiting for Chris -- proposal in data/persistent/promotion-proposals/",
                          post, poster)
    return out


# --------------------------------------------------------------------------- demotion

def previous_champion(conn: sqlite3.Connection, exclude, horizon: Optional[str] = None) -> Optional[Dict]:
    """The most recently demoted former champion (of sleeve `horizon` when given)."""
    rows = [r for r in registry.list_strategies(conn=conn, horizon=horizon)
            if r["status"] == "retired" and r["promoted_at"] and r["demoted_at"]
            and (r["id"], r["version"]) != tuple(exclude) and not r["reference_only"]]
    rows.sort(key=lambda r: (r["demoted_at"], r["promoted_at"]))
    return rows[-1] if rows else None


def promotion_day_equity(shadow: pd.DataFrame, promoted_at: Optional[str]) -> Optional[float]:
    if not promoted_at or shadow.empty:
        return None
    d = pd.Timestamp(str(promoted_at)[:10])
    before = shadow[shadow.index <= d]
    return float(before["equity"].iloc[-1] if len(before) else shadow["equity"].iloc[0])


def evaluate_demotion(row: Dict, conn: sqlite3.Connection, today=None, post: bool = True,
                      poster=None, out_dir: Optional[str] = None) -> Dict:
    """Automatic demotion of a sleeve's champion with rollback to that sleeve's previous champion."""
    h = _sleeve(row)
    shadow = load_shadow(conn, row["id"], row["version"])
    out = {"id": row["id"], "version": row["version"], "sleeve": h, "demoted": False}
    if len(shadow) < 2:
        out["skipped"] = "no shadow record yet"
        return out
    mk = SLEEVE_MARKET[h]
    chk = demotion_checks(shadow["equity"], benchmark_series(conn, mk, shadow.index),
                          promotion_day_equity(shadow, row.get("promoted_at")), market_label=FALLBACK_LABEL[mk])
    out["checks"] = chk["checks"]
    if not chk["demote"]:
        return out
    today = pd.Timestamp(today or date.today()).strftime("%Y-%m-%d")
    why = "; ".join(k for k, v in chk["checks"].items() if v)
    registry.set_status(row["id"], row["version"], "retired", notes=f"auto-demoted {today}: {why}", conn=conn)
    prev = previous_champion(conn, (row["id"], row["version"]), horizon=h)
    if prev is not None:
        registry.set_status(prev["id"], prev["version"], "champion", approved_by=registry.APPROVER,
                            notes=(f"automatic rollback {today} after demotion of {row['id']} "
                                   f"{row['version'][:12]}; restores the champion approved "
                                   f"{str(prev['promoted_at'])[:10]}"), conn=conn)
        target = f"{prev['id']} v{prev['version'][:12]}"
    elif h == "swing":
        target = "the live trader (champion by definition; no previous registry champion)"
    else:
        target = (f"no {h} champion (no previous {h} registry champion); the {h} sleeve is judged "
                  f"against its benchmark, {FALLBACK_LABEL[SLEEVE_FALLBACK[h]]}")
    out.update({"demoted": True, "reason": why, "rollback_to": target})
    d = out_dir or DEMOTION_DIR
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{row['id']}-{today}.md")
    ex = chk["window_excess"]
    with open(path, "w") as f:
        f.write("\n".join([
            f"# Demotion: {row['id']} v{row['version'][:12]} ({h} sleeve, {today})", "",
            f"Automatic (research/promotion.py). Rule(s) hit: {why}.", "",
            f"- set to retired; rolled back to: {target}",
            f"- {DEMOTE_WINDOW}-day excess vs {FALLBACK_LABEL[mk]}: {_pct(ex.get('mean_daily'), 3)}/day, 95% CI "
            f"[{_pct(ex.get('ci_low'), 3)}, {_pct(ex.get('ci_high'), 3)}]",
            f"- max drawdown in that window: {_pct(chk['window_drawdown'])}",
            f"- change since promotion-day equity: {_pct(chk['fall_from_promotion'])}",
            f"- promoted_at {row.get('promoted_at')}; shadow days {chk['n_days']}", ""]))
    out["report"] = path
    print(f"DEMOTION {row['id']} {row['version'][:12]}: {why}; rollback to {target}", flush=True)
    out["posted"] = _post(f":warning: {h.capitalize()} champion {row['id']} auto-demoted ({why}); rolled back to {target}. "
                          f"Report in data/persistent/demotion-reports/", post, poster)
    return out


# --------------------------------------------------------------------------- daily entry point

def evaluate_all(conn: Optional[sqlite3.Connection] = None, post: bool = True, poster=None,
                 out_dir: Optional[str] = None, today=None, db_path: Optional[str] = None) -> Dict:
    own = conn is None
    conn = conn or connect(db_path)
    try:
        from research.shadow import ensure_schema
        ensure_schema(conn)
        out: Dict = {"evaluated": [], "proposals": [], "demotions": []}
        for row in registry.list_strategies(status="champion", conn=conn):
            d = evaluate_demotion(row, conn, today=today, post=post, poster=poster,
                                  out_dir=os.path.join(out_dir, "demotions") if out_dir else None)
            if d["demoted"]:
                out["demotions"].append(d)
        for row in registry.list_strategies(status="shadow", conn=conn):
            p = evaluate_promotion(row, conn, today=today, post=post, poster=poster, out_dir=out_dir)
            out["evaluated"].append(p)
            if p.get("proposal"):
                out["proposals"].append(p["proposal"])
        return out
    finally:
        if own:
            conn.close()
