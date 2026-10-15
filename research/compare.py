"""Edge-vs-benchmark comparison: the date-clustered CI of the DIFFERENCE.

The event study answers "is the edge's mean return > 0?". The tightened bar asks a
harder question: does the edge beat (a) its own no-signal baseline (same filters,
no trigger) and (b) SPY buy-and-hold over the SAME holding window?

Both comparisons are paired per event: for each event we subtract the benchmark's
return for the same event date and horizon, then bootstrap the mean of those
excess returns resampling whole event DATES (same clustering as the harness).

  vs baseline: excess = ret_gross - direction * baseline_mean_gross(date, h)
               (both legs pay the same cost, so costs cancel; for a short arm the
               no-signal comparison is shorting the same filtered names)
  vs SPY:      excess = (ret_gross - round-trip cost) - spy_gross(date, h)
               (SPY buy-and-hold is treated as cost-free, which is conservative
               for the edge; not direction-signed: a short arm must beat holding SPY)
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from research.bars import BarStore
from research.event_study import EventStudyResult, bootstrap_mean_ci, compute_trades

MIN_EVENTS = 200


def per_date_mean(trades: pd.DataFrame, horizons: Sequence[int]) -> pd.DataFrame:
    """Mean gross LONG return per event_date and horizon (direction undone)."""
    cols = [f"ret_{h}" for h in horizons]
    if trades.empty:
        return pd.DataFrame(columns=cols, index=pd.DatetimeIndex([], name="event_date"))
    t = trades[["event_date", "direction"] + cols].copy()
    for c in cols:
        t[c] = t[c].astype(float) * t["direction"].astype(float)
    t["event_date"] = pd.to_datetime(t["event_date"])
    return t.groupby("event_date")[cols].mean()


def spy_by_date(store: BarStore, event_dates: Sequence, horizons: Sequence[int],
                symbol: str = "SPY") -> pd.DataFrame:
    """SPY gross return from the next-open entry to the t+h close, for each event date."""
    d = pd.DatetimeIndex(pd.to_datetime(pd.Series(event_dates)).dt.normalize().unique()).sort_values()
    ev = pd.DataFrame({"symbol": symbol, "event_date": d, "direction": 1})
    return per_date_mean(compute_trades(ev, store, horizons), horizons)


def excess_returns(trades: pd.DataFrame, bench: pd.DataFrame, horizons: Sequence[int],
                   rt_cost: float = 0.0, signed: bool = True) -> pd.DataFrame:
    """Per-event excess return vs `bench` (indexed by event_date). NaN where either side is missing.

    signed=True:  excess = ret - direction * bench   (costs cancel; rt_cost ignored)
    signed=False: excess = (ret - rt_cost) - bench
    """
    ed = pd.DatetimeIndex(pd.to_datetime(trades["event_date"]))
    out = pd.DataFrame({"event_date": ed, "direction": trades["direction"].values})
    b = bench.reindex(ed)
    for h in horizons:
        r = trades[f"ret_{h}"].values.astype(float)
        bv = b[f"ret_{h}"].values.astype(float)
        if signed:
            out[f"ex_{h}"] = r - out["direction"].values.astype(float) * bv
        else:
            out[f"ex_{h}"] = (r - rt_cost) - bv
    return out


def _diff_stats(ex: np.ndarray, dates: np.ndarray, n_boot: int, level: float, seed: int) -> Dict:
    m = ~np.isnan(ex)
    ex, dates = ex[m], dates[m]
    if len(ex) == 0:
        return {"n": 0, "diff": None, "ci_low": None, "ci_high": None}
    groups = pd.DatetimeIndex(dates).strftime("%Y-%m-%d").values
    lo, hi = bootstrap_mean_ci(ex, groups, n_boot=n_boot, level=level, seed=seed)
    return {"n": int(len(ex)), "diff": float(ex.mean()), "ci_low": lo, "ci_high": hi}


def compare(result: EventStudyResult, bench: pd.DataFrame, kind: str,
            n_boot: int = 1000, level: float = 0.95, seed: int = 7) -> List[Dict]:
    """Rows of {slice, horizon, n, edge_mean, bench_mean, diff, ci_low, ci_high}.

    kind="baseline" (direction-signed, cost-neutral) or "spy" (edge net of cost minus SPY).
    edge_mean / bench_mean are net of the slice's cost (SPY: gross, cost-free hold).
    Slices mirror the harness: all, holdout, bear windows, stress cost, holdout stress.
    """
    if kind not in ("baseline", "spy"):
        raise ValueError("kind must be 'baseline' or 'spy'")
    p = result.params
    horizons = p["horizons"]
    trades = result.trades
    rt = 2 * p["cost_bps_per_side"] / 1e4
    sm = p["stress_mult"]
    dates = pd.to_datetime(trades["event_date"]).values
    n = len(trades)
    ho = dates >= np.datetime64(p["holdout_start"]) if (n and p.get("holdout_start")) else np.zeros(n, bool)
    slices = [("all", np.ones(n, bool), rt), ("holdout", ho, rt)]
    for lo, hi in p["bear_windows"]:
        slices.append((f"bear {lo[:7]}..{hi[:7]}",
                       (dates >= np.datetime64(lo)) & (dates <= np.datetime64(hi)), rt))
    slices.append((f"stress x{sm:g} cost", np.ones(n, bool), rt * sm))
    slices.append((f"holdout stress x{sm:g}", ho, rt * sm))

    b = bench.reindex(pd.DatetimeIndex(pd.to_datetime(trades["event_date"])))
    dirs_all = trades["direction"].values.astype(float)
    rows = []
    for name, mask, cost in slices:
        ex = excess_returns(trades, bench, horizons, rt_cost=cost, signed=(kind == "baseline"))
        for h in horizons:
            e = ex[f"ex_{h}"].values[mask]
            ok = ~np.isnan(e)
            r = trades[f"ret_{h}"].values.astype(float)[mask][ok]
            bv = b[f"ret_{h}"].values.astype(float)[mask][ok]
            st = _diff_stats(e, dates[mask], n_boot, level, seed)
            if ok.any():
                edge_mean = float((r - cost).mean())
                bench_mean = (float((dirs_all[mask][ok] * bv - cost).mean()) if kind == "baseline"
                              else float(bv.mean()))
            else:
                edge_mean = bench_mean = None
            rows.append({"slice": name, "horizon": h, "edge_mean": edge_mean,
                         "bench_mean": bench_mean, **st})
    return rows


def get_row(rows: List[Dict], slice_name: str, horizon: int) -> Optional[Dict]:
    for r in rows:
        if r["slice"] == slice_name and r["horizon"] == horizon:
            return r
    return None


def compare_table(rows: List[Dict], bench_label: str) -> str:
    def f(v):
        return "-" if v is None else f"{v*100:.3f}"
    lines = [f"| slice | h | n | edge net % | {bench_label} % | difference % | 95% CI low % | 95% CI high % |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        lines.append(f"| {r['slice']} | {r['horizon']} | {r['n']} | {f(r['edge_mean'])} | "
                     f"{f(r['bench_mean'])} | {f(r['diff'])} | {f(r['ci_low'])} | {f(r['ci_high'])} |")
    return "\n".join(lines)


def tightened_bar(result: EventStudyResult, vs_base: List[Dict],
                  vs_spy: List[Dict]) -> Dict[int, Dict[str, bool]]:
    """Per horizon, the tightened (2026-10-14) bar:
    1. beats its no-signal baseline AND SPY, difference CI excludes 0 (all events)
    2. n >= 200
    3. positive after stress (2x) cost: mean > 0 and CI low > 0
    4. holdout: 2x-cost mean > 0 and still beats baseline; bear slice does not lose
    """
    sm = result.params["stress_mult"]
    bear_names = sorted({r["slice"] for r in result.rows if r["slice"].startswith("bear")})
    out = {}
    for h in result.params["horizons"]:
        allr = result.get("all", h) or {}
        st = result.get(f"stress x{sm:g} cost", h) or {}
        hs = result.get(f"holdout stress x{sm:g}", h) or {}
        bb = get_row(vs_base, "all", h) or {}
        bs = get_row(vs_spy, "all", h) or {}
        hb = get_row(vs_base, "holdout", h) or {}
        bears = [result.get(x, h) or {} for x in bear_names]
        c = {
            "n>=200": (allr.get("n") or 0) >= MIN_EVENTS,
            "beats baseline (CI>0)": bool(bb.get("n")) and bb["ci_low"] > 0,
            "beats SPY (CI>0)": bool(bs.get("n")) and bs["ci_low"] > 0,
            "2x cost EV>0, CI>0": bool(st.get("n")) and st["mean"] > 0 and st["ci_low"] > 0,
            "holdout 2x EV>0 & beats baseline": (bool(hs.get("n")) and hs["mean"] > 0
                                                 and bool(hb.get("n")) and hb["diff"] > 0),
            "bear EV>=0": bool(bears) and all(x.get("n") and x["mean"] >= 0 for x in bears),
        }
        c["CLEARS BAR"] = all(c.values())
        out[h] = c
    return out


def tightened_bar_table(chk: Dict[int, Dict[str, bool]]) -> str:
    keys = list(next(iter(chk.values())).keys())
    lines = ["| h | " + " | ".join(keys) + " |", "|---:|" + "---|" * len(keys)]
    for h, c in chk.items():
        lines.append(f"| {h} | " + " | ".join("yes" if c[k] else "no" for k in keys) + " |")
    return "\n".join(lines)
