"""Intraday event study: honest base rates for minute-bar signals, against the intraday bar.

Each event is (symbol, date, signal_idx[, stop]) where signal_idx is the minute (since the
session open) whose CLOSE fired the signal. Timing is the strict simulator's: entry at the
open of the next printed bar after the signal bar, exit at the open of the bar h minutes
later, both paying half the spread + slippage (research/intraday_sim.CostModel).

Horizons: 5, 15, 30, 60 minutes (capped at the 15:55 flat bar), "close" (exit at the flat
bar), and "strategy" = the trades the strict simulator actually took (stops, targets,
partial fills, risk cap, daily stop). The bar is judged on "strategy"; the fixed horizons
show where any edge lives.

Benchmarks, paired per event:
  baseline = no-signal: the same symbol in the same session, entered at random bars
             (n_base draws, seeded), held for the same horizon (for "strategy": the same
             number of minutes as the trade, capped at the flat bar), same costs. Costs
             nearly cancel, so this isolates the signal's timing. Draws are taken only from
             the signal bar onward (never before it, and never before baseline_min_idx):
             a random entry placed BEFORE the signal on a session the signal later fired
             on would be conditioned on the future (it "knows" the breakout comes), which
             biases the benchmark upward.
  SPY      = intraday SPY hold: buy SPY at the same entry minute (cost-free, conservative
             for the edge, as in research/compare.py); fixed horizons exit SPY at the same
             minute as the event, "close"/"strategy" hold SPY to the session's final close.
CIs: date-clustered bootstrap (whole sessions resampled) of the mean, and of the mean
paired DIFFERENCE using research.compare's own estimator (imported, not copied).
Slices: all, train (first 60% of sessions), holdout (last 40% of sessions), 2x stress
(spread and slippage doubled; the strategy is re-simulated), holdout 2x stress, and an
informational zero-cost slice (not part of the bar) that separates the signal from the
cost model.
"""

from __future__ import annotations

import json
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from research.compare import _diff_stats, bootstrap_mean_ci
from research.intraday_bars import SessionBars, connect
from research.intraday_sim import CostModel, SimConfig, SimResult, simulate

FIXED_HORIZONS: Tuple = (5, 15, 30, 60, "close")
# Informational only (never part of the bar): separates signal quality from the cost model.
GROSS_SLICE = "gross, zero cost (info)"
ZERO_COST = CostModel(spread_floor_bps=0.0, slippage_bps=0.0, spread_mult=0.0, slip_mult=0.0)
HORIZONS: Tuple = FIXED_HORIZONS + ("strategy",)
MIN_TRADES = 300


def exec_idx(sb: SessionBars, i: int) -> Optional[int]:
    """First minute >= i (inside the session) with a printed bar, else None."""
    if i < 0:
        i = 0
    ok = np.flatnonzero(np.isfinite(sb.open[i:]) & (sb.volume[i:] > 0))
    return int(i + ok[0]) if len(ok) else None


def _last_close(sb: SessionBars) -> float:
    c = sb.close[np.isfinite(sb.close)]
    return float(c[-1]) if len(c) else float("nan")


def _round_trip(sb: SessionBars, e: int, x_target: int, c: float) -> float:
    """Net long return entering at open[e], exiting at the first printed open >= x_target
    (capped at the flat bar); if nothing prints, sell at the last close."""
    x_target = min(x_target, sb.flat_idx)
    x = exec_idx(sb, x_target)
    px_out = sb.open[x] if x is not None else _last_close(sb)
    return float(px_out * (1 - c) / (sb.open[e] * (1 + c)) - 1.0)


def _spy_ret(spy: Optional[SessionBars], e: int, x_target: Optional[int]) -> float:
    """Gross SPY return from the open of minute e (or next print) to minute x_target's open,
    or to the session's final close when x_target is None."""
    if spy is None:
        return float("nan")
    ei = exec_idx(spy, e)
    if ei is None:
        return float("nan")
    if x_target is None:
        out = _last_close(spy)
    else:
        xi = exec_idx(spy, min(x_target, spy.flat_idx))
        out = spy.open[xi] if xi is not None else _last_close(spy)
    return float(out / spy.open[ei] - 1.0)


def _rng(seed: int, symbol: str, d: str) -> np.random.Generator:
    return np.random.default_rng([seed, zlib.crc32(f"{symbol}|{d}".encode())])


def _baseline_entries(sb: SessionBars, min_idx: int, n_base: int, rng) -> List[int]:
    """Entry minutes for no-signal draws: a random 'signal' bar r in [min_idx, flat-2], entry
    at the next printed bar."""
    hi = sb.flat_idx - 1
    if hi <= min_idx:
        return []
    out = []
    for r in rng.integers(min_idx, hi, size=n_base):
        e = exec_idx(sb, int(r) + 1)
        if e is not None and e < sb.flat_idx:
            out.append(e)
    return out


def _x_for(h, e: int, sb: SessionBars) -> int:
    return sb.flat_idx if h == "close" else e + int(h)


def event_returns(events: pd.DataFrame, paths: Dict[Tuple[str, str], SessionBars],
                  spy_paths: Dict[str, SessionBars], cost: CostModel,
                  baseline_min_idx: int = 0, n_base: int = 5, seed: int = 7) -> pd.DataFrame:
    """Per usable event: ret_h, base_h, spy_h for each fixed horizon (NaN where impossible)."""
    rows = []
    for ev in events[["symbol", "date", "signal_idx"]].itertuples(index=False):
        sb = paths[(ev.symbol, ev.date)]
        e = exec_idx(sb, int(ev.signal_idx) + 1)
        if e is None or e >= sb.flat_idx:
            continue
        c = cost.per_side(sb)
        spy = spy_paths.get(ev.date)
        bents = _baseline_entries(sb, max(baseline_min_idx, int(ev.signal_idx)), n_base,
                                  _rng(seed, ev.symbol, ev.date))
        r = {"symbol": ev.symbol, "date": ev.date, "signal_idx": int(ev.signal_idx), "entry_idx": e,
             "cost_per_side": c}
        for h in FIXED_HORIZONS:
            r[f"ret_{h}"] = _round_trip(sb, e, _x_for(h, e, sb), c)
            b = [_round_trip(sb, be, _x_for(h, be, sb), c) for be in bents]
            r[f"base_{h}"] = float(np.mean(b)) if b else float("nan")
            r[f"spy_{h}"] = _spy_ret(spy, e, None if h == "close" else e + int(h))
        rows.append(r)
    cols = ["symbol", "date", "signal_idx", "entry_idx", "cost_per_side"] + \
        [f"{k}_{h}" for h in FIXED_HORIZONS for k in ("ret", "base", "spy")]
    return pd.DataFrame(rows, columns=cols)


def strategy_returns(sim: SimResult, paths: Dict[Tuple[str, str], SessionBars],
                     spy_paths: Dict[str, SessionBars], baseline_min_idx: int = 0,
                     n_base: int = 5, seed: int = 7) -> pd.DataFrame:
    """Per simulated trade: ret_strategy (the sim's net), base_strategy (random entries in the
    same symbol-session from the signal bar on, held the same minutes, same per-side cost),
    spy_strategy (SPY from
    the trade's first fill minute to the final close)."""
    rows = []
    for t in sim.trades.itertuples(index=False):
        sb = paths[(t.symbol, t.date)]
        hold = max(int(t.exit_idx) - int(t.entry_idx), 1)
        bents = _baseline_entries(sb, max(baseline_min_idx, int(t.signal_idx)), n_base,
                                  _rng(seed + 1, t.symbol, t.date))
        b = [_round_trip(sb, be, be + hold, t.cost_per_side) for be in bents]
        rows.append({"symbol": t.symbol, "date": t.date, "ret_strategy": t.ret,
                     "base_strategy": float(np.mean(b)) if b else float("nan"),
                     "spy_strategy": _spy_ret(spy_paths.get(t.date), int(t.entry_idx), None),
                     "r_mult": t.r_mult, "exit_reason": t.exit_reason})
    return pd.DataFrame(rows, columns=["symbol", "date", "ret_strategy", "base_strategy", "spy_strategy",
                                       "r_mult", "exit_reason"])


@dataclass
class IntradayStudyResult:
    params: Dict
    rows: List[Dict]
    events: pd.DataFrame = field(repr=False, default=None)        # fixed-horizon frame (normal cost)
    strategy: pd.DataFrame = field(repr=False, default=None)      # per-trade frame (normal cost)
    sim: SimResult = field(repr=False, default=None)
    sim_stress: SimResult = field(repr=False, default=None)
    sim_gross: SimResult = field(repr=False, default=None)

    def get(self, slice_name: str, horizon) -> Optional[Dict]:
        for r in self.rows:
            if r["slice"] == slice_name and str(r["horizon"]) == str(horizon):
                return r
        return None

    def to_dict(self) -> Dict:
        return {"params": self.params, "rows": self.rows,
                "sim": self.sim.summary() if self.sim else None,
                "sim_stress": self.sim_stress.summary() if self.sim_stress else None,
                "sim_gross": self.sim_gross.summary() if self.sim_gross else None}


def _slice_row(name: str, h, df: pd.DataFrame, mask: np.ndarray, n_boot: int, level: float,
               seed: int) -> Dict:
    r = df[f"ret_{h}"].values.astype(float)[mask]
    b = df[f"base_{h}"].values.astype(float)[mask]
    s = df[f"spy_{h}"].values.astype(float)[mask]
    dates = pd.to_datetime(df["date"]).values[mask]
    ok = ~np.isnan(r)
    r, b, s, dates = r[ok], b[ok], s[ok], dates[ok]
    row = {"slice": name, "horizon": str(h), "n": int(len(r))}
    if len(r) == 0:
        return {**row, "mean": None, "win_rate": None, "ci_low": None, "ci_high": None,
                "base_mean": None, "diff_base": None, "base_ci_low": None, "base_ci_high": None,
                "spy_mean": None, "diff_spy": None, "spy_ci_low": None, "spy_ci_high": None}
    groups = pd.DatetimeIndex(dates).strftime("%Y-%m-%d").values
    lo, hi = bootstrap_mean_ci(r, groups, n_boot=n_boot, level=level, seed=seed)
    db = _diff_stats(r - b, dates, n_boot, level, seed)
    ds = _diff_stats(r - s, dates, n_boot, level, seed)
    return {**row, "mean": float(r.mean()), "win_rate": float((r > 0).mean()), "ci_low": lo, "ci_high": hi,
            "base_mean": float(np.nanmean(b)) if np.isfinite(b).any() else None,
            "diff_base": db["diff"], "base_ci_low": db["ci_low"], "base_ci_high": db["ci_high"],
            "spy_mean": float(np.nanmean(s)) if np.isfinite(s).any() else None,
            "diff_spy": ds["diff"], "spy_ci_low": ds["ci_low"], "spy_ci_high": ds["ci_high"]}


def run_study(events: pd.DataFrame, paths: Dict[Tuple[str, str], SessionBars],
              spy_paths: Dict[str, SessionBars], sessions: Sequence[str],
              sim_config: Optional[SimConfig] = None, baseline_min_idx: int = 0, n_base: int = 5,
              holdout_frac: float = 0.4, stress_mult: float = 2.0, n_boot: int = 1000,
              ci_level: float = 0.95, seed: int = 7) -> IntradayStudyResult:
    """events: [symbol, date, signal_idx, stop]; sessions: every session in the study window
    (defines the held-out cut, and sessions with no signal stay in the equity series)."""
    cfg = sim_config or SimConfig()
    stress_cfg = SimConfig(**{**cfg.__dict__, "cost": cfg.cost.stressed(stress_mult)})
    sessions = sorted(sessions)
    cut = sessions[int(np.floor(len(sessions) * (1 - holdout_frac)))] if sessions else None

    gross_cfg = SimConfig(**{**cfg.__dict__, "cost": ZERO_COST})
    ev_n = event_returns(events, paths, spy_paths, cfg.cost, baseline_min_idx, n_base, seed)
    ev_s = event_returns(events, paths, spy_paths, stress_cfg.cost, baseline_min_idx, n_base, seed)
    ev_g = event_returns(events, paths, spy_paths, ZERO_COST, baseline_min_idx, n_base, seed)
    sim_n = simulate(events, paths, cfg, sessions)
    sim_s = simulate(events, paths, stress_cfg, sessions)
    sim_g = simulate(events, paths, gross_cfg, sessions)
    st_n = strategy_returns(sim_n, paths, spy_paths, baseline_min_idx, n_base, seed)
    st_s = strategy_returns(sim_s, paths, spy_paths, baseline_min_idx, n_base, seed)
    st_g = strategy_returns(sim_g, paths, spy_paths, baseline_min_idx, n_base, seed)

    def masks(df):
        n = len(df)
        d = df["date"].values.astype(str) if n else np.array([], dtype=str)
        ho = (d >= cut) if cut else np.zeros(n, bool)
        return np.ones(n, bool), ~ho, ho

    rows = []
    sm = f"{stress_mult:g}"
    for h in HORIZONS:
        dn, ds, dg = (st_n, st_s, st_g) if h == "strategy" else (ev_n, ev_s, ev_g)
        a, tr, ho = masks(dn)
        a2, _, ho2 = masks(ds)
        a3 = masks(dg)[0]
        for name, df, m in (("all", dn, a), ("train", dn, tr), ("holdout", dn, ho),
                            (f"stress x{sm}", ds, a2), (f"holdout stress x{sm}", ds, ho2),
                            (GROSS_SLICE, dg, a3)):
            rows.append(_slice_row(name, h, df, m, n_boot, ci_level, seed))
    params = {"horizons": [str(h) for h in HORIZONS], "cost": cfg.cost.__dict__, "stress_mult": stress_mult,
              "holdout_frac": holdout_frac, "holdout_start": cut, "n_base": n_base,
              "baseline_min_idx": baseline_min_idx, "n_boot": n_boot, "ci_level": ci_level, "seed": seed,
              "sessions": len(sessions), "first_session": sessions[0] if sessions else None,
              "last_session": sessions[-1] if sessions else None, "n_events_in": int(len(events)),
              "sim": {k: v for k, v in cfg.__dict__.items() if k != "cost"}}
    return IntradayStudyResult(params=params, rows=rows, events=ev_n, strategy=st_n, sim=sim_n, sim_stress=sim_s,
                                sim_gross=sim_g)


def intraday_bar(result: IntradayStudyResult, horizons: Sequence = HORIZONS) -> Dict[str, Dict[str, bool]]:
    """Per horizon, the intraday bar (day sleeve):
    1. beats the no-signal baseline AND intraday SPY hold, difference CI excluding 0 (all)
    2. n >= 300
    3. positive after 2x spread+slippage: mean > 0 and CI low > 0
    4. holds in the held-out last 40% of sessions: mean > 0 and beats baseline and SPY (point)
    """
    sm = f"{result.params['stress_mult']:g}"
    out = {}
    for h in horizons:
        h = str(h)
        al = result.get("all", h) or {}
        st = result.get(f"stress x{sm}", h) or {}
        ho = result.get("holdout", h) or {}
        c = {
            f"n>={MIN_TRADES}": (al.get("n") or 0) >= MIN_TRADES,
            "beats baseline (CI>0)": bool(al.get("n")) and al["base_ci_low"] is not None and al["base_ci_low"] > 0,
            "beats SPY (CI>0)": bool(al.get("n")) and al["spy_ci_low"] is not None and al["spy_ci_low"] > 0,
            "2x cost EV>0, CI>0": bool(st.get("n")) and st["mean"] > 0 and st["ci_low"] > 0,
            "holdout EV>0 & beats both": (bool(ho.get("n")) and ho["mean"] > 0 and (ho["diff_base"] or 0) > 0
                                          and (ho["diff_spy"] or 0) > 0),
        }
        c["CLEARS BAR"] = all(c.values())
        out[h] = c
    return out


# --------------------------------------------------------------------------- tables

def _f(v, nd=3):
    return "-" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v*100:.{nd}f}"


def study_table(result: IntradayStudyResult, slices: Optional[Sequence[str]] = None) -> str:
    lines = ["| slice | h | n | mean net % | win % | 95% CI % | vs baseline % [95% CI] | "
             "vs SPY hold % [95% CI] |", "|---|---|---:|---:|---:|---|---|---|"]
    for r in result.rows:
        if slices and r["slice"] not in slices:
            continue
        if not r["n"]:
            lines.append(f"| {r['slice']} | {r['horizon']} | 0 | - | - | - | - | - |")
            continue
        lines.append(f"| {r['slice']} | {r['horizon']} | {r['n']} | {_f(r['mean'])} | {_f(r['win_rate'], 1)} | "
                     f"[{_f(r['ci_low'])}, {_f(r['ci_high'])}] | "
                     f"{_f(r['diff_base'])} [{_f(r['base_ci_low'])}, {_f(r['base_ci_high'])}] | "
                     f"{_f(r['diff_spy'])} [{_f(r['spy_ci_low'])}, {_f(r['spy_ci_high'])}] |")
    return "\n".join(lines)


def bar_table(chk: Dict[str, Dict[str, bool]]) -> str:
    keys = list(next(iter(chk.values())).keys())
    lines = ["| h | " + " | ".join(keys) + " |", "|---|" + "---|" * len(keys)]
    for h, c in chk.items():
        lines.append(f"| {h} | " + " | ".join("yes" if c[k] else "no" for k in keys) + " |")
    return "\n".join(lines)


# --------------------------------------------------------------------------- variant log

def log_variant(edge_name: str, params: Dict, result: IntradayStudyResult,
                db_path: Optional[str] = None) -> int:
    """Append one run to intraday.db `intraday_variant_runs`; return distinct variants tried."""
    conn = connect(db_path)
    try:
        n = int(len(result.sim.trades)) if result.sim is not None else 0
        conn.execute("INSERT INTO intraday_variant_runs(edge_name, run_at, params_json, n_trades, result_json) "
                     "VALUES (?,?,?,?,?)",
                     (edge_name, datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      json.dumps(params, sort_keys=True, default=str), n,
                      json.dumps(result.to_dict(), default=str)))
        conn.commit()
        return variants_tried(edge_name, conn=conn)
    finally:
        conn.close()


def variants_tried(edge_name: str, conn=None, db_path: Optional[str] = None) -> int:
    own = conn is None
    conn = conn or connect(db_path)
    try:
        return conn.execute("SELECT COUNT(DISTINCT params_json) FROM intraday_variant_runs WHERE edge_name=?",
                            (edge_name,)).fetchone()[0]
    finally:
        if own:
            conn.close()
