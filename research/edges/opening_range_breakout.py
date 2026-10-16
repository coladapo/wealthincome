"""Intraday edge 1: opening-range breakout (ORB), long only, flat by 15:55 ET.

Rule, per symbol-session (minute m counts from the 09:30 open):
  range     = bars 0 .. range_minutes-1 (09:30-09:44 for the default 15-minute range)
  range_hi  = max high, range_lo = min low, range_vol = mean bar volume over the range
  signal    = the FIRST bar m >= range_minutes whose close > range_hi AND whose volume
              >= vol_mult x range_vol (and that passes the optional filters below);
              one signal per symbol-session, observed at that bar's close
  stop      = the tighter (higher) of range_lo and signal close x (1 - stop_pct)
  targets   = scale out half at 1R and the rest at 2R (R = first fill - stop), else
              flat at 15:55 -- executed by research/intraday_sim (next-bar-open fills,
              spread + slippage, 10%-of-volume partial fills, 0.25% risk cap, 1% daily stop)
Optional filters (variants):
  min_range_pct   (range_hi - range_lo) / range_lo >= this (known at the range's end)
  spy_vwap_filter SPY's close at the signal bar is above SPY's session VWAP so far
                  (cumulative from the open through the signal bar -- no look-ahead)

Universe: the frozen intraday universe minus SPY/QQQ (benchmarks).

CLI: venv/bin/python -m research.edges.opening_range_breakout --full-report <path>
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from research.intraday_bars import BENCH_ETFS, IntradayStore, SessionBars

EDGE_NAME = "opening_range_breakout"

DEFAULT_PARAMS: Dict = {
    "range_minutes": 15,
    "vol_mult": 1.5,
    "stop_pct": 0.01,
    "min_range_pct": None,
    "spy_vwap_filter": False,
}

ARMS: List[Tuple[str, Dict]] = [
    ("15-min range (DEFAULT)", {}),
    ("30-min range", {"range_minutes": 30}),
    ("15-min range, range >= 0.5% of price", {"min_range_pct": 0.005}),
    ("15-min range, SPY above its session VWAP", {"spy_vwap_filter": True}),
]


def spy_above_vwap(spy: Optional[SessionBars]) -> Optional[np.ndarray]:
    """Per minute: SPY close > cumulative session VWAP through that minute (False if no data)."""
    if spy is None:
        return None
    v = spy.volume
    pv = np.nan_to_num(spy.vwap * v)
    cv = np.cumsum(v)
    with np.errstate(invalid="ignore", divide="ignore"):
        vw = np.where(cv > 0, np.cumsum(pv) / cv, np.nan)
    c = pd.Series(spy.close).ffill().values
    return np.nan_to_num(c > vw, nan=False).astype(bool) & np.isfinite(vw)


def signal(sb: SessionBars, spy_ok: Optional[np.ndarray] = None, **params) -> Optional[Dict]:
    """The session's breakout {signal_idx, stop, range_hi, range_lo, range_vol} or None."""
    p = {**DEFAULT_PARAMS, **params}
    R = int(p["range_minutes"])
    if sb.n <= R + 2:
        return None
    hi, lo = sb.high[:R], sb.low[:R]
    if np.isfinite(hi).sum() < R // 2:
        return None
    rh, rl = float(np.nanmax(hi)), float(np.nanmin(lo))
    rv = float(np.mean(sb.volume[:R]))
    if not (rl > 0 and rv > 0):
        return None
    if p["min_range_pct"] is not None and (rh - rl) / rl < p["min_range_pct"]:
        return None
    last = sb.flat_idx - 1          # a signal must leave a bar to enter before the flat time
    c, v = sb.close[R:last], sb.volume[R:last]
    with np.errstate(invalid="ignore"):
        hit = (c > rh) & (v >= p["vol_mult"] * rv)
    if p["spy_vwap_filter"]:
        if spy_ok is None:
            return None
        ok = spy_ok[R:last]
        hit = hit & ok[:len(hit)] if len(ok) >= len(hit) else np.zeros_like(hit)
    idx = np.flatnonzero(hit)
    if not len(idx):
        return None
    m = int(R + idx[0])
    stop = max(rl, float(sb.close[m]) * (1 - p["stop_pct"]))
    return {"signal_idx": m, "stop": stop, "range_hi": rh, "range_lo": rl, "range_vol": rv}


def _universe(store: IntradayStore, symbols: Optional[Iterable[str]]) -> List[str]:
    syms = list(symbols) if symbols is not None else store.universe()
    return [s for s in syms if s not in BENCH_ETFS]


def generate_events_multi(store: IntradayStore, arms: Sequence[Tuple[str, Dict]],
                          start: Optional[str] = None, end: Optional[str] = None,
                          symbols: Optional[Iterable[str]] = None):
    """Load each symbol once; return ({arm_label: events DataFrame}, paths, spy_paths).

    events: [symbol, date, signal_idx, stop, range_hi, range_lo, range_vol]. `paths` keeps only
    the symbol-sessions some arm fired on (memory)."""
    spy_paths = store.symbol_sessions("SPY", start, end)
    spy_ok = {d: spy_above_vwap(sb) for d, sb in spy_paths.items()}
    out = {lbl: [] for lbl, _ in arms}
    paths: Dict[Tuple[str, str], SessionBars] = {}
    for s in _universe(store, symbols):
        for d, sb in store.symbol_sessions(s, start, end).items():
            for lbl, over in arms:
                sig = signal(sb, spy_ok.get(d), **over)
                if sig:
                    out[lbl].append({"symbol": s, "date": d, **sig})
                    paths[(s, d)] = sb
    cols = ["symbol", "date", "signal_idx", "stop", "range_hi", "range_lo", "range_vol"]
    evs = {lbl: (pd.DataFrame(rows, columns=cols).sort_values(["date", "signal_idx", "symbol"])
                 .reset_index(drop=True)) for lbl, rows in out.items()}
    return evs, paths, spy_paths


def generate_events(store: IntradayStore, start: Optional[str] = None, end: Optional[str] = None,
                    symbols: Optional[Iterable[str]] = None, **params):
    evs, paths, spy_paths = generate_events_multi(store, [("x", params)], start, end, symbols)
    return evs["x"], paths, spy_paths


def evaluate(events: pd.DataFrame, paths, spy_paths, sessions: Sequence[str], params: Dict,
             n_boot: int = 1000):
    from research.intraday_study import intraday_bar, run_study

    p = {**DEFAULT_PARAMS, **params}
    res = run_study(events, paths, spy_paths, sessions, baseline_min_idx=int(p["range_minutes"]),
                    n_boot=n_boot)
    return {"params": p, "events": events, "result": res, "bar": intraday_bar(res)}


def _sim_lines(label: str, sim) -> List[str]:
    s = sim.summary()

    def f(v):
        return "-" if v is None else f"{v*100:.2f}"
    mean_r = "-" if s["mean_r"] is None else f"{s['mean_r']:.3f}"
    return [f"| {label} | {s['n_trades']} | {f(s['mean_ret'])} | {f(s['win_rate'])} | {mean_r} | "
            f"{f(s['total_return'])} | {f(s['max_drawdown'])} | {s['sessions_traded']}/{s['sessions']} | "
            f"{s['loss_stop_days']} | {s['partial_entries']} | {s['forced_exits']} |"]


def _arm_section(label: str, a: Dict) -> str:
    from research.intraday_study import GROSS_SLICE, bar_table, study_table

    r = a["result"]
    sm = f"{r.params['stress_mult']:g}"
    t = r.sim.trades
    reasons = t["exit_reason"].value_counts().head(8) if len(t) else pd.Series(dtype=int)
    sk = r.sim.summary()["skipped"]
    lines = [f"## {label}", "", f"Params: `{ {k: a['params'][k] for k in sorted(a['params'])} }`", "",
             f"Signals: {len(a['events'])} symbol-sessions fired. Simulated trades: {len(t)} "
             f"(2x stress: {len(r.sim_stress.trades)}). Signals not traded: {sk}. Held-out sessions "
             f"start {r.params['holdout_start']}.", "",
             "### The intraday bar", "", bar_table(a["bar"]), "",
             "### All / train / held-out (normal cost)", "",
             study_table(r, ["all", "train", "holdout"]), "",
             f"### 2x spread + slippage", "", study_table(r, [f"stress x{sm}", f"holdout stress x{sm}"]), "",
             "### Zero cost (INFORMATIONAL -- not the bar; separates the signal from the cost model)", "",
             study_table(r, [GROSS_SLICE]), "",
             "### Strict-simulator account view ($100k start, compounding)", "",
             "| run | trades | mean trade net % | win % | mean R | total return % | max drawdown % | "
             "sessions traded | daily-stop days | partially filled entries | forced exits |",
             "|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|"]
    lines += (_sim_lines("normal cost", r.sim) + _sim_lines(f"{sm}x cost", r.sim_stress)
              + _sim_lines("zero cost (info)", r.sim_gross))
    if len(reasons):
        lines += ["", "Exit reasons (normal cost): " + ", ".join(f"{k} {v}" for k, v in reasons.items())]
    lines.append("")
    return "\n".join(lines)


def headline_table(arms: Sequence[Tuple[str, Dict]]) -> str:
    def f(v):
        return "-" if v is None else f"{v*100:.3f}"
    lines = ["| arm | trades | mean net % | vs baseline % [95% CI] | vs SPY hold % [95% CI] | 2x cost mean % "
             "[95% CI] | held-out mean % (vs base / vs SPY) | clears bar |",
             "|---|---:|---:|---|---|---|---|---|"]
    for label, a in arms:
        r = a["result"]
        sm = f"{r.params['stress_mult']:g}"
        al, st, ho = r.get("all", "strategy"), r.get(f"stress x{sm}", "strategy"), r.get("holdout", "strategy")
        lines.append(
            f"| {label} | {al['n']} | {f(al['mean'])} | {f(al['diff_base'])} [{f(al['base_ci_low'])}, "
            f"{f(al['base_ci_high'])}] | {f(al['diff_spy'])} [{f(al['spy_ci_low'])}, {f(al['spy_ci_high'])}] | "
            f"{f(st['mean'])} [{f(st['ci_low'])}, {f(st['ci_high'])}] | {f(ho['mean'])} "
            f"({f(ho['diff_base'])} / {f(ho['diff_spy'])}) | "
            f"{'YES' if a['bar']['strategy']['CLEARS BAR'] else 'no'} |")
    return "\n".join(lines)


def run_arms(db_path: Optional[str] = None, start: Optional[str] = None, end: Optional[str] = None,
             arms: Sequence[Tuple[str, Dict]] = ARMS, n_boot: int = 1000, log: bool = True):
    from research.intraday_study import log_variant

    store = IntradayStore(db_path)
    try:
        sessions = store.sessions(start, end)
        evs, paths, spy_paths = generate_events_multi(store, arms, start, end)
        coverage = {"symbols": len(_universe(store, None)), "sessions": len(sessions),
                    "first": sessions[0] if sessions else None, "last": sessions[-1] if sessions else None}
    finally:
        store.close()
    sessions = [d for d in sessions if d in spy_paths]
    out, tried = [], 0
    for label, over in arms:
        a = evaluate(evs[label], paths, spy_paths, sessions, over, n_boot=n_boot)
        if log:
            tried = log_variant(EDGE_NAME, {**a["params"], "start": start, "end": end,
                                            "sim": a["result"].params["sim"],
                                            "cost": a["result"].params["cost"]}, a["result"], db_path)
        out.append((label, a))
    return out, tried, coverage


def full_report(path: str, db_path: Optional[str] = None, start: Optional[str] = None,
                end: Optional[str] = None, read: Optional[str] = None, preamble: Optional[str] = None) -> str:
    t0 = time.time()
    arms, tried, cov = run_arms(db_path, start, end)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    clear = [lbl for lbl, a in arms if a["bar"]["strategy"]["CLEARS BAR"]]
    mech = ("Mechanical result: no arm clears the intraday bar." if not clear else
            "Mechanical result: the intraday bar is cleared by: " + "; ".join(clear) + ".")
    parts = [f"# Edge report: {EDGE_NAME} (intraday, day sleeve)", "",
             f"Generated {now} in {time.time() - t0:.0f}s. Sessions {cov['first']}..{cov['last']} "
             f"({cov['sessions']} sessions), {cov['symbols']} single names (SPY/QQQ are benchmarks).", ""]
    if preamble:
        parts += [preamble, ""]
    parts += ["THE BAR (day sleeve), judged on the strictly simulated trades (`strategy` rows): (1) beat "
              "the no-signal baseline (same symbol, same session, random entry bars, same hold, same "
              "costs) AND intraday SPY hold (SPY bought at the same minute, held to the close, "
              "cost-free), each with the session-clustered 95% CI of the paired difference excluding 0; "
              f"(2) n >= 300 trades; (3) mean > 0 with CI > 0 at 2x spread + slippage; (4) the held-out "
              "last 40% of sessions stays positive and still beats both benchmarks; (5) variants "
              "tried are reported.", "",
              f"Variants tried for this edge (distinct parameter sets in intraday_variant_runs): **{tried}**.", "",
              mech, ""]
    if read:
        parts += ["## Honest verdict", "", read, ""]
    parts += ["## Headline (strategy = strictly simulated trades)", "", headline_table(arms), ""]
    for label, a in arms:
        parts.append(_arm_section(label, a))
    text = "\n".join(parts)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    return text


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research.edges.opening_range_breakout")
    ap.add_argument("--db", default=None)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--full-report", nargs="?", const="edge-orb-intraday-report.md", default=None,
                    help="run every arm + baseline/SPY comparisons and write the markdown report here")
    ap.add_argument("--read-file", default=None, help="honest-verdict paragraph to include")
    ap.add_argument("--preamble-file", default=None, help="universe/coverage/fill-assumption notes")
    a = ap.parse_args(argv)
    if a.full_report:
        read = open(a.read_file).read().strip() if a.read_file else None
        pre = open(a.preamble_file).read().strip() if a.preamble_file else None
        full_report(a.full_report, a.db, a.start, a.end, read, pre)
        print(f"report: {a.full_report}")
        return 0
    from research.intraday_study import bar_table, study_table
    arms, tried, _ = run_arms(a.db, a.start, a.end, arms=ARMS[:1])
    r = arms[0][1]
    print(study_table(r["result"]))
    print(bar_table(r["bar"]))
    print(f"variants tried for {EDGE_NAME}: {tried}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
