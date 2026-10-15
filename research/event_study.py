"""Event-study harness: honest base rates for a list of (symbol, event_date, direction).

Timing (no look-ahead): an event on day t is acted on at the OPEN of the next
trading day (t+1) and exited at the CLOSE of trading day t+h. So h=1 is the
t+1 open-to-close move, h=5 holds from the t+1 open to the t+5 close.

Returns are per-trade, direction-signed, minus a round-trip cost of
2 x cost_bps_per_side. "EV per $100 risked" = mean net return x $100 for a $100
position (no stop is modelled, so the whole notional is the amount at risk).

Confidence intervals: bootstrap of the mean (seeded). Events that fire on the
same date are strongly correlated (a market-wide sell-off triggers dozens of
names at once), so by default the bootstrap resamples whole event DATES, not
individual events (cluster="date"); cluster="week" is a stricter check for
overlapping holding periods; cluster="none" is the plain iid bootstrap.

Slices reported per horizon: all, train (first 60% of events by time), holdout
(last 40%, untouched by parameter choice), each bear window, and stress
(all events at stress_mult x cost).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from research.bars import BarStore, connect

DEFAULT_HORIZONS = (1, 5, 10, 20)
DEFAULT_BEAR_WINDOWS = (("2022-01-03", "2022-12-30"),)


@dataclass
class EventStudyResult:
    params: Dict
    rows: List[Dict]                       # one per (slice, horizon)
    n_events_in: int
    n_events_used: int
    trades: pd.DataFrame = field(repr=False, default=None)

    def table(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows)

    def get(self, slice_name: str, horizon: int) -> Optional[Dict]:
        for r in self.rows:
            if r["slice"] == slice_name and r["horizon"] == horizon:
                return r
        return None

    def summary(self) -> str:
        lines = [
            f"events in={self.n_events_in} usable={self.n_events_used}  "
            f"cost={self.params['cost_bps_per_side']}bp/side  stress x{self.params['stress_mult']}  "
            f"CI={self.params['ci_level']:.0%} bootstrap n={self.params['n_boot']} "
            f"cluster={self.params['cluster']}",
            f"{'slice':<22}{'h':>3}{'n':>8}{'mean%':>9}{'win%':>7}{'EV/$100':>9}"
            f"{'CI low%':>9}{'CI high%':>9}",
        ]
        for r in self.rows:
            if r["n"] == 0:
                lines.append(f"{r['slice']:<22}{r['horizon']:>3}{0:>8}{'-':>9}")
                continue
            lines.append(
                f"{r['slice']:<22}{r['horizon']:>3}{r['n']:>8}{r['mean']*100:>9.3f}"
                f"{r['win_rate']*100:>7.1f}{r['ev_per_100']:>9.3f}"
                f"{r['ci_low']*100:>9.3f}{r['ci_high']*100:>9.3f}")
        return "\n".join(lines)

    def to_dict(self) -> Dict:
        return {"params": self.params, "n_events_in": self.n_events_in,
                "n_events_used": self.n_events_used, "rows": self.rows}


def bootstrap_mean_ci(values: np.ndarray, groups: Optional[np.ndarray] = None,
                      n_boot: int = 1000, level: float = 0.95, seed: int = 7) -> Tuple[float, float]:
    """Percentile bootstrap CI of the mean; if `groups` is given, resample whole groups."""
    values = np.asarray(values, dtype=float)
    if len(values) < 2:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    if groups is None:
        idx = rng.integers(0, len(values), size=(n_boot, len(values)))
        means = values[idx].mean(axis=1)
    else:
        codes, uniq = pd.factorize(groups)
        sums = np.bincount(codes, weights=values)
        counts = np.bincount(codes).astype(float)
        k = len(uniq)
        idx = rng.integers(0, k, size=(n_boot, k))
        means = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
    a = (1 - level) / 2
    lo, hi = np.quantile(means, [a, 1 - a])
    return (float(lo), float(hi))


def compute_trades(events: pd.DataFrame, store: BarStore,
                   horizons: Sequence[int] = DEFAULT_HORIZONS) -> pd.DataFrame:
    """One row per usable event with gross direction-signed returns per horizon (NaN if no data)."""
    ev = events.copy()
    ev["event_date"] = pd.to_datetime(ev["event_date"]).dt.normalize()
    if "direction" not in ev:
        ev["direction"] = 1
    out = []
    hmax = max(horizons)
    for sym, g in ev.groupby("symbol", sort=False):
        b = store.bars(sym, start=g["event_date"].min() - pd.Timedelta(days=10),
                       end=g["event_date"].max() + pd.Timedelta(days=int(hmax * 1.6) + 15))
        if b.empty:
            continue
        dates = b.index.values
        opens, closes = b["open"].values, b["close"].values
        # j = index of the last trading day <= event_date (the event day itself)
        j = np.searchsorted(dates, g["event_date"].values, side="right") - 1
        rec = pd.DataFrame({"symbol": sym, "event_date": g["event_date"].values,
                            "direction": g["direction"].values})
        entry_idx = j + 1
        valid_entry = (j >= 0) & (entry_idx < len(dates))
        ei = np.where(valid_entry, entry_idx, 0)
        rec["entry_date"] = np.where(valid_entry, dates[ei], np.datetime64("NaT", "ns"))
        entry_px = np.where(valid_entry, opens[ei], np.nan)
        rec["entry_px"] = entry_px
        for h in horizons:
            x = j + h
            ok = valid_entry & (x < len(dates))
            xi = np.where(ok, x, 0)
            exit_px = np.where(ok, closes[xi], np.nan)
            rec[f"ret_{h}"] = rec["direction"].values * (exit_px / entry_px - 1.0)
        out.append(rec[valid_entry])
    if not out:
        cols = ["symbol", "event_date", "direction", "entry_date", "entry_px"] + [f"ret_{h}" for h in horizons]
        return pd.DataFrame(columns=cols)
    trades = pd.concat(out, ignore_index=True)
    return trades.sort_values(["event_date", "symbol"]).reset_index(drop=True)


def _stats(net: np.ndarray, dates: np.ndarray, n_boot: int, level: float, seed: int,
           cluster: str) -> Dict:
    mask = ~np.isnan(net)
    net, dates = net[mask], dates[mask]
    n = int(len(net))
    if n == 0:
        return {"n": 0, "mean": None, "median": None, "win_rate": None, "ev_per_100": None,
                "ci_low": None, "ci_high": None}
    if cluster == "none":
        groups = None
    elif cluster == "week":
        groups = pd.DatetimeIndex(dates).to_period("W").astype(str).values
    else:
        groups = pd.DatetimeIndex(dates).strftime("%Y-%m-%d").values
    lo, hi = bootstrap_mean_ci(net, groups, n_boot=n_boot, level=level, seed=seed)
    m = float(net.mean())
    return {"n": n, "mean": m, "median": float(np.median(net)),
            "win_rate": float((net > 0).mean()), "ev_per_100": m * 100.0,
            "ci_low": lo, "ci_high": hi}


def run_event_study(events: pd.DataFrame,
                    horizons: Sequence[int] = DEFAULT_HORIZONS,
                    cost_bps_per_side: float = 10,
                    stress_mult: float = 2.0,
                    holdout_frac: float = 0.4,
                    bear_windows: Sequence[Tuple[str, str]] = DEFAULT_BEAR_WINDOWS,
                    store: Optional[BarStore] = None,
                    n_boot: int = 1000,
                    ci_level: float = 0.95,
                    seed: int = 7,
                    cluster: str = "date") -> EventStudyResult:
    if cluster not in ("date", "week", "none"):
        raise ValueError("cluster must be 'date', 'week' or 'none'")
    own = store is None
    store = store or BarStore()
    try:
        trades = compute_trades(events, store, horizons)
    finally:
        if own:
            store.close()
    horizons = list(horizons)
    rt_cost = 2 * cost_bps_per_side / 1e4
    params = {"horizons": horizons, "cost_bps_per_side": cost_bps_per_side,
              "stress_mult": stress_mult, "holdout_frac": holdout_frac,
              "bear_windows": [list(w) for w in bear_windows], "n_boot": n_boot,
              "ci_level": ci_level, "seed": seed, "cluster": cluster}

    dates = pd.to_datetime(trades["event_date"]).values
    n = len(trades)
    slices: List[Tuple[str, np.ndarray, float]] = [("all", np.ones(n, bool), rt_cost)]
    if n:
        cut_i = int(np.floor(n * (1 - holdout_frac)))
        cut_date = dates[min(cut_i, n - 1)]
        params["holdout_start"] = str(pd.Timestamp(cut_date).date())
        slices.append(("train", dates < cut_date, rt_cost))
        slices.append(("holdout", dates >= cut_date, rt_cost))
    for lo, hi in bear_windows:
        m = (dates >= np.datetime64(lo)) & (dates <= np.datetime64(hi))
        slices.append((f"bear {lo[:7]}..{hi[:7]}", m, rt_cost))
    slices.append((f"stress x{stress_mult:g} cost", np.ones(n, bool), rt_cost * stress_mult))
    if n:
        cut = np.datetime64(params["holdout_start"])
        slices.append((f"holdout stress x{stress_mult:g}", dates >= cut, rt_cost * stress_mult))

    rows = []
    for name, mask, cost in slices:
        for h in horizons:
            net = trades[f"ret_{h}"].values.astype(float)[mask] - cost
            rows.append({"slice": name, "horizon": h,
                         **_stats(net, dates[mask], n_boot, ci_level, seed, cluster)})
    return EventStudyResult(params=params, rows=rows, n_events_in=len(events),
                            n_events_used=n, trades=trades)


def variants_log(name: str, params: Dict, result: EventStudyResult,
                 db_path: Optional[str] = None) -> int:
    """Append one variant run to research.db `variant_runs`; return how many variants
    of this edge have been tried so far (the overfitting guard)."""
    conn = connect(db_path)
    try:
        conn.execute(
            "INSERT INTO variant_runs(edge_name, run_at, params_json, n_events, result_json) "
            "VALUES (?,?,?,?,?)",
            (name, datetime.now(timezone.utc).isoformat(timespec="seconds"),
             json.dumps(params, sort_keys=True, default=str), result.n_events_used,
             json.dumps(result.to_dict(), default=str)))
        conn.commit()
        return variants_tried(name, conn=conn)
    finally:
        conn.close()


def variants_tried(name: str, conn=None, db_path: Optional[str] = None) -> int:
    """Distinct parameter sets tried for an edge (re-running the same params is not a new variant)."""
    own = conn is None
    conn = conn or connect(db_path)
    try:
        return conn.execute("SELECT COUNT(DISTINCT params_json) FROM variant_runs WHERE edge_name=?",
                            (name,)).fetchone()[0]
    finally:
        if own:
            conn.close()
