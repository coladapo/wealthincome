"""Edge 2: daily-bar overnight gap fill.

Rule for gap day t (all filters use data through t-1, the trigger uses the t open):
  gap      = open[t] / close[t-1] - 1
  liquid   = mean(close * volume, dv_window) as of t-1 >= min_dollar_vol
  optional = close[t-1] > SMA(sma_trend)[t-1]                (require_above_sma)
             close fell on each of the `down_days` days ending t-1  (require_down)
  side "down": gap <= -min_gap -> long  (+1), betting on a fill back up to close[t-1]
  side "up":   gap >=  min_gap -> short (-1), betting on a fill down. INFORMATIONAL ONLY:
               the live system cannot short.

Entry timing (`entry`):
  "next_open" (default, the harness convention): event_date = t, so the trade enters at the
      t+1 open. Fully tradable from daily data, but misses any fill on the gap day itself.
  "gap_open": event_date = t-1, so the harness enters at the gap-day open itself. OPTIMISTIC:
      it assumes a fill at the opening print, which is also the price the trigger is read from.

Fill rate: share of events where price touches close[t-1] during the held window
(high >= level for longs, low <= level for shorts), for each horizon h.

Earnings-day gaps are NOT excluded: there is no cheap historical earnings-date source in
the repo (core/earnings_scraper.py is forward-looking only). Many of the largest gaps are
earnings reactions, which tend to drift rather than fill (PEAD), so this biases against
the gap-fill rule; the PEAD study covers that population.

CLI: venv/bin/python -m research.edges.gap_fill --since 2019-01-01 --full-report <path>
"""

from __future__ import annotations

import argparse
import sys
from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

from research.bars import ETFS, BarStore

EDGE_NAME = "gap_fill"
HORIZONS = (1, 5, 10, 20)

DEFAULT_PARAMS: Dict = {
    "side": "down",
    "min_gap": 0.02,
    "min_dollar_vol": 20e6,
    "dv_window": 20,
    "sma_trend": 200,
    "require_above_sma": False,
    "require_down": False,
    "down_days": 3,
    "entry": "next_open",
}


def signals(df: pd.DataFrame, **params) -> pd.DataFrame:
    """Per-day frame for one symbol (index = date): gap, filters, `event` (gap-day flag),
    `direction`, fill `level` (= prior close) and `filled_gap_day` (touched level on day t)."""
    p = {**DEFAULT_PARAMS, **params}
    if p["side"] not in ("down", "up"):
        raise ValueError("side must be 'down' or 'up'")
    c, o = df["close"], df["open"]
    prev = c.shift(1)
    gap = o / prev - 1.0
    dollar_vol = (c * df["volume"]).rolling(p["dv_window"], min_periods=p["dv_window"]).mean().shift(1)
    filt = dollar_vol >= p["min_dollar_vol"]
    sma = c.rolling(p["sma_trend"], min_periods=p["sma_trend"]).mean().shift(1)
    above = prev > sma
    if p["require_above_sma"]:
        filt = filt & above
    k = p["down_days"]
    down = (c.diff() < 0).astype(float).rolling(k, min_periods=k).sum().shift(1) == k
    if p["require_down"]:
        filt = filt & down
    filt = filt.fillna(False).astype(bool)
    if p["side"] == "down":
        trig, direction = gap <= -p["min_gap"], 1
        filled_day = df["high"] >= prev
    else:
        trig, direction = gap >= p["min_gap"], -1
        filled_day = df["low"] <= prev
    event = (filt & trig.fillna(False)).astype(bool)
    return pd.DataFrame({"gap": gap, "level": prev, "dollar_vol": dollar_vol, "above_sma": above,
                         "down_run": down, "filter": filt, "event": event,
                         "direction": direction, "filled_gap_day": filled_day.fillna(False)})


def _rows(df: pd.DataFrame, mask: np.ndarray, sig: pd.DataFrame, entry: str) -> pd.DataFrame:
    """Turn gap-day flags into event rows, applying the entry-timing date shift."""
    idx = np.flatnonzero(mask)
    if entry == "gap_open":
        idx = idx[idx >= 1]
        ev_dates = df.index[idx - 1]
    elif entry == "next_open":
        ev_dates = df.index[idx]
    else:
        raise ValueError("entry must be 'next_open' or 'gap_open'")
    return pd.DataFrame({"event_date": ev_dates, "gap_date": df.index[idx],
                         "direction": sig["direction"].values[idx] if len(idx) else [],
                         "gap": sig["gap"].values[idx], "level": sig["level"].values[idx],
                         "filled_gap_day": sig["filled_gap_day"].values[idx].astype(bool)})


def _warm(start: Optional[str], p: Dict):
    if start is None:
        return None
    return pd.Timestamp(start) - pd.Timedelta(days=int((p["sma_trend"] + p["dv_window"]) * 1.6) + 30)


def _universe(store: BarStore, symbols: Optional[Iterable[str]]) -> List[str]:
    if symbols is not None:
        return list(symbols)
    etf = {e[0] for e in ETFS}
    return [s for s in store.symbols(etfs=False) if s not in etf]


def generate_events(store: BarStore, start: Optional[str] = None, end: Optional[str] = None,
                    symbols: Optional[Iterable[str]] = None, baseline: bool = False,
                    **params) -> pd.DataFrame:
    """Events [symbol, event_date, gap_date, direction, gap, level, filled_gap_day].

    baseline=True: the no-signal comparison set -- every day that passes the SAME filters,
    with no gap trigger, same entry timing, direction +1 (the long return of the filtered set).
    The date filter (>= start) is applied to the gap day.
    """
    p = {**DEFAULT_PARAMS, **params}
    out = []
    for s in _universe(store, symbols):
        df = store.bars(s, start=_warm(start, p), end=end)
        if len(df) < p["dv_window"] + 2:
            continue
        sig = signals(df, **p)
        mask = (sig["filter"] if baseline else sig["event"]).to_numpy(bool, copy=True)
        if start is not None:
            mask &= np.asarray(df.index >= pd.Timestamp(start))
        r = _rows(df, mask, sig, p["entry"])
        if baseline:
            r["direction"] = 1
        if len(r):
            r.insert(0, "symbol", s)
            out.append(r)
    cols = ["symbol", "event_date", "gap_date", "direction", "gap", "level", "filled_gap_day"]
    if not out:
        return pd.DataFrame(columns=cols)
    return pd.concat(out, ignore_index=True).sort_values(["event_date", "symbol"]).reset_index(drop=True)


def fill_flags(events: pd.DataFrame, store: BarStore,
               horizons: Sequence[int] = HORIZONS) -> pd.DataFrame:
    """Per event: fill_h = price touched `level` on some bar in the held window
    (bars j+1..j+h after the event-date bar j, i.e. exactly the bars the trade is open);
    NaN when the window runs past the data."""
    out = []
    hmax = max(horizons)
    for sym, g in events.groupby("symbol", sort=False):
        ed = pd.to_datetime(g["event_date"]).dt.normalize()
        b = store.bars(sym, start=ed.min() - pd.Timedelta(days=10),
                       end=ed.max() + pd.Timedelta(days=int(hmax * 1.6) + 15))
        if b.empty:
            continue
        j = np.searchsorted(b.index.values, ed.values, side="right") - 1
        lvl = g["level"].values.astype(float)
        d = g["direction"].values
        rec = g[["symbol", "event_date", "direction"]].copy()
        for h in horizons:
            fwd_hi = b["high"].rolling(h).max().shift(-h).values     # max high over bars i+1..i+h
            fwd_lo = b["low"].rolling(h).min().shift(-h).values
            ok = (j >= 0) & (j + h < len(b))
            jj = np.where(ok, j, 0)
            hit = np.where(d > 0, fwd_hi[jj] >= lvl, fwd_lo[jj] <= lvl).astype(float)
            rec[f"fill_{h}"] = np.where(ok, hit, np.nan)
        out.append(rec)
    if not out:
        return pd.DataFrame(columns=["symbol", "event_date", "direction"] + [f"fill_{h}" for h in horizons])
    return pd.concat(out, ignore_index=True)


def fill_rates(flags: pd.DataFrame, horizons: Sequence[int] = HORIZONS) -> Dict[int, Dict]:
    return {h: {"n": int(flags[f"fill_{h}"].notna().sum()),
                "rate": (float(flags[f"fill_{h}"].mean()) if flags[f"fill_{h}"].notna().any() else None)}
            for h in horizons}


# Every run below is logged in variant_runs (the overfitting guard).
ARMS = [
    ("gap-down >= 2%, long (DEFAULT)", {}),
    ("gap-up >= 2%, short (INFORMATIONAL: live system cannot short)", {"side": "up"}),
    ("gap-down >= 2%, long, entered at the gap-day open (OPTIMISTIC timing check)", {"entry": "gap_open"}),
    ("gap-down >= 3%, long", {"min_gap": 0.03}),
    ("gap-down >= 2%, long, prior close above its 200-day SMA", {"require_above_sma": True}),
    ("gap-down >= 2%, long, after 3 straight down closes", {"require_down": True}),
]

# Survivorship check, run only when an index-membership file is supplied: keep only days on
# which the name was ALREADY in the S&P 500 (Wikipedia "Date added"). Removes the
# pre-addition look-ahead (names that joined after a big run-up); still misses removed names.
MEMBERSHIP_ARMS = [
    ("gap-down >= 2%, long, IN-INDEX DAYS ONLY (survivorship check)", {"in_index_only": True}),
    ("gap-down >= 2%, long, above 200-day SMA, IN-INDEX DAYS ONLY (survivorship check)",
     {"require_above_sma": True, "in_index_only": True}),
]


def load_membership(path: str) -> pd.Series:
    """CSV [symbol, date_added] -> Series symbol -> Timestamp (date the name joined the index)."""
    m = pd.read_csv(path, parse_dates=["date_added"])
    return m.dropna(subset=["date_added"]).set_index("symbol")["date_added"]


def in_index_only(events: pd.DataFrame, membership: pd.Series) -> pd.DataFrame:
    """Keep events whose event_date is on/after the symbol's index-addition date."""
    da = events["symbol"].map(membership)
    keep = da.notna() & (pd.to_datetime(events["event_date"]) >= da)
    return events[keep.values].reset_index(drop=True)


def evaluate(store: BarStore, since: str, until: Optional[str], params: Dict,
             baseline_cache: Optional[Dict] = None, spy_cache: Optional[Dict] = None,
             membership: Optional[pd.Series] = None) -> Dict:
    """Event study + baseline/SPY difference CIs + fill rates + tightened bar for one arm."""
    from research.compare import compare, per_date_mean, spy_by_date, tightened_bar
    from research.event_study import compute_trades, run_event_study

    p = {**DEFAULT_PARAMS, **params}
    restrict = bool(p.get("in_index_only"))
    if restrict and membership is None:
        raise ValueError("in_index_only needs a membership file")
    ev = generate_events(store, since, until, **p)
    if restrict:
        ev = in_index_only(ev, membership)
    res = run_event_study(ev[["symbol", "event_date", "direction"]], horizons=HORIZONS, store=store)
    bkey = (p["min_dollar_vol"], p["dv_window"], p["require_above_sma"], p["require_down"],
            p["down_days"], p["sma_trend"], p["entry"], restrict)
    baseline_cache = {} if baseline_cache is None else baseline_cache
    if bkey not in baseline_cache:
        bev = generate_events(store, since, until, baseline=True, **p)
        if restrict:
            bev = in_index_only(bev, membership)
        btr = compute_trades(bev[["symbol", "event_date", "direction"]], store, HORIZONS)
        baseline_cache[bkey] = (per_date_mean(btr, HORIZONS), len(btr))
    bench, n_base = baseline_cache[bkey]
    spy_cache = {} if spy_cache is None else spy_cache
    if p["entry"] not in spy_cache:
        # SPY over every trading day in range; per-date lookup handles any event set
        days = store.bars("SPY", start=since, end=until).index
        spy_cache[p["entry"]] = spy_by_date(store, days, HORIZONS)
    spy = spy_cache[p["entry"]]
    vs_base = compare(res, bench, "baseline")
    vs_spy = compare(res, spy, "spy")
    flags = fill_flags(ev, store, HORIZONS)
    fr = fill_rates(flags, HORIZONS)
    same_day = float(ev["filled_gap_day"].mean()) if len(ev) else None
    return {"params": p, "events": ev, "result": res, "vs_base": vs_base, "vs_spy": vs_spy,
            "n_baseline": n_base, "fill": fr, "filled_gap_day": same_day,
            "bar": tightened_bar(res, vs_base, vs_spy), "diag": concentration(res, bench)}


def concentration(res, bench: pd.DataFrame, horizons: Sequence[int] = (10, 20), top: int = 10) -> List[Dict]:
    """Is the excess vs baseline broad or a few names' fat tail? Per horizon: median excess,
    share of events beating the baseline, month-clustered CI (stricter for 20-day overlaps),
    the top contributing names and the mean excess without them."""
    from research.compare import excess_returns
    from research.event_study import bootstrap_mean_ci

    t = res.trades
    out = []
    if t.empty:
        return out
    ex = excess_returns(t, bench, horizons, signed=True)
    for h in horizons:
        e = ex[f"ex_{h}"].values
        ok = ~np.isnan(e)
        sym = t["symbol"].values[ok]
        e = e[ok]
        months = pd.DatetimeIndex(t["event_date"].values[ok]).to_period("M").astype(str).values
        lo, hi = bootstrap_mean_ci(e, months)
        contrib = pd.Series(e).groupby(sym).sum().sort_values(ascending=False)
        names = list(contrib.index[:top])
        rest = e[~np.isin(sym, names)]
        out.append({"horizon": h, "mean": float(e.mean()), "median": float(np.median(e)),
                    "share_pos": float((e > 0).mean()), "month_ci": (lo, hi), "top": names,
                    "mean_ex_top": float(rest.mean()) if len(rest) else None})
    return out


def _arm_section(label: str, a: Dict) -> str:
    from research.compare import compare_table, tightened_bar_table
    from research.report import results_table

    r = a["result"]
    p = a["params"]
    fr = a["fill"]
    lines = [f"## {label}", "", f"Params: `{ {k: p[k] for k in sorted(p)} }`", "",
             f"Events: {r.n_events_in} generated, {r.n_events_used} usable. Holdout starts "
             f"{r.params.get('holdout_start', '-')}. Cost {r.params['cost_bps_per_side']} bp/side "
             f"(2x = {2 * r.params['cost_bps_per_side']:g}). CIs bootstrap-clustered by event date. "
             f"No-signal baseline: {a['n_baseline']} filtered symbol-days.", "",
             "### Tightened bar", "", tightened_bar_table(a["bar"]), "",
             "### Edge returns (all / train / held-out / bear / 2x cost)", "", results_table(r), "",
             "### vs no-signal baseline (same filters, no gap trigger) -- difference CI", "",
             compare_table(a["vs_base"], "baseline net"), "",
             "### vs SPY buy-and-hold over the same window -- difference CI", "",
             compare_table(a["vs_spy"], "SPY (cost-free)"), "",
             "### Fill rate (touched the prior close while the trade was open)", "",
             "| h | n | fill rate % |", "|---:|---:|---:|"]
    for h, v in fr.items():
        rate = "-" if v["rate"] is None else f"{v['rate'] * 100:.1f}"
        lines.append(f"| {h} | {v['n']} | {rate} |")
    if p["entry"] == "next_open" and a["filled_gap_day"] is not None:
        lines += ["", f"Already filled on the gap day itself (before this entry): "
                      f"{a['filled_gap_day']*100:.1f}% of events."]
    if a.get("diag"):
        lines += ["", "### Is the excess vs baseline broad, or a few names?", "",
                  "| h | mean excess % | median excess % | events beating baseline % | "
                  "95% CI clustered by MONTH % | mean excess without top-10 names % | top-10 contributors |",
                  "|---:|---:|---:|---:|---|---:|---|"]
        for d in a["diag"]:
            lo, hi = d["month_ci"]
            wo = "-" if d["mean_ex_top"] is None else f"{d['mean_ex_top']*100:.3f}"
            lines.append(f"| {d['horizon']} | {d['mean']*100:.3f} | {d['median']*100:.3f} | "
                         f"{d['share_pos']*100:.1f} | [{lo*100:.3f}, {hi*100:.3f}] | {wo} | "
                         f"{', '.join(d['top'])} |")
    lines.append("")
    return "\n".join(lines)


def headline_table(arms: Sequence) -> str:
    from research.compare import get_row
    lines = ["| arm | h | n | net % (10bp) | net % (2x cost) | vs baseline % [95% CI] | "
             "vs SPY % [95% CI] | held-out 2x % | 2022 bear % | fill % | clears bar |",
             "|---|---:|---:|---:|---:|---|---|---:|---:|---:|---|"]

    def f(v):
        return "-" if v is None else f"{v*100:.2f}"
    for label, a in arms:
        r = a["result"]
        sm = r.params["stress_mult"]
        bear = next((x for x in {rr["slice"] for rr in r.rows} if x.startswith("bear")), None)
        for h in HORIZONS:
            al, st = r.get("all", h), r.get(f"stress x{sm:g} cost", h)
            hs = r.get(f"holdout stress x{sm:g}", h)
            br = r.get(bear, h) if bear else None
            b, s = get_row(a["vs_base"], "all", h), get_row(a["vs_spy"], "all", h)
            fr = a["fill"][h]["rate"]
            lines.append(
                f"| {label.split(' (')[0]} | {h} | {al['n']} | {f(al['mean'])} | {f(st['mean'])} | "
                f"{f(b['diff'])} [{f(b['ci_low'])}, {f(b['ci_high'])}] | "
                f"{f(s['diff'])} [{f(s['ci_low'])}, {f(s['ci_high'])}] | {f(hs['mean'])} | "
                f"{f(br['mean']) if br else '-'} | {'-' if fr is None else f'{fr*100:.1f}'} | "
                f"{'YES' if a['bar'][h]['CLEARS BAR'] else 'no'} |")
    return "\n".join(lines)


def full_report(path: str, since: str = "2019-01-01", until: Optional[str] = None,
                db_path: Optional[str] = None, read: Optional[str] = None,
                membership_csv: Optional[str] = None) -> str:
    import os
    from datetime import datetime, timezone
    from research.event_study import variants_log

    membership = load_membership(membership_csv) if membership_csv else None
    store = BarStore(db_path)
    arms = []
    tried = 0
    try:
        bc, sc = {}, {}
        for label, over in ARMS + (MEMBERSHIP_ARMS if membership is not None else []):
            a = evaluate(store, since, until, over, bc, sc, membership=membership)
            tried = variants_log(EDGE_NAME, {**a["params"], "since": since, "until": until, "cost_bps": 10},
                                 a["result"], db_path=db_path)
            arms.append((label, a))
    finally:
        store.close()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    any_clear = [(lbl, h) for lbl, a in arms for h in HORIZONS if a["bar"][h]["CLEARS BAR"]]
    mech = ("Mechanical result: no arm clears the tightened bar at any horizon."
            if not any_clear else
            "Mechanical result: bar cleared by " + "; ".join(f"{l} @ h={h}" for l, h in any_clear) + ".")
    parts = [f"# Edge report: {EDGE_NAME}", "", f"Generated {now}. Data {since} to {until or 'latest'}.", "",
             "Universe: current S&P 500 constituents (ETFs excluded from events), adjusted daily bars "
             "from yfinance. SURVIVORSHIP BIAS: names that left the index are missing. Earnings-day "
             "gaps are NOT excluded (no cheap historical earnings-date source; the PEAD study covers "
             "earnings gaps). Default entry = next open after the gap day (harness convention), exit "
             "close of t+h. Net = after 10 bp/side; 2x cost = 20 bp/side.", "",
             "THE BAR (tightened 2026-10-14), per horizon: (1) beat the no-signal baseline (same "
             "filters, no gap trigger) AND SPY buy-and-hold over the same window, with the "
             "date-clustered 95% CI of the paired difference excluding 0; (2) n >= 200; (3) mean > 0 "
             "with CI > 0 at 2x cost; (4) held-out last 40% stays positive at 2x cost and still beats "
             "the baseline, and the 2022 bear slice does not lose; (5) variants tried reported below. "
             "The gap-up/short arm and the gap-open-entry arm are informational (cannot short live; "
             "opening-print fills are optimistic).", "",
             f"Variants tried for this edge (distinct parameter sets in variant_runs): **{tried}**.", "",
             mech, ""]
    if read:
        parts += ["## Honest verdict", "", read, ""]
    parts += ["## Headline", "", headline_table(arms), ""]
    for label, a in arms:
        parts.append(_arm_section(label, a))
    text = "\n".join(parts)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    return text


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research.edges.gap_fill")
    ap.add_argument("--since", default="2019-01-01")
    ap.add_argument("--until", default=None)
    ap.add_argument("--db", default=None)
    ap.add_argument("--full-report", nargs="?", const="edge-gap-fill-report.md", default=None,
                    help="run every arm + baseline/SPY comparisons and write the markdown report here")
    ap.add_argument("--read-file", default=None, help="honest-verdict paragraph to include")
    ap.add_argument("--membership-csv", default=None,
                    help="CSV [symbol,date_added]: adds the in-index-days-only survivorship arms")
    a = ap.parse_args(argv)
    if a.full_report:
        read = open(a.read_file).read().strip() if a.read_file else None
        full_report(a.full_report, a.since, a.until, a.db, read, a.membership_csv)
        print(f"report: {a.full_report}")
        return 0
    from research.compare import tightened_bar_table
    from research.event_study import variants_log
    store = BarStore(a.db)
    try:
        r = evaluate(store, a.since, a.until, {})
    finally:
        store.close()
    tried = variants_log(EDGE_NAME, {**r["params"], "since": a.since, "until": a.until, "cost_bps": 10},
                         r["result"], db_path=a.db)
    print(r["result"].summary())
    print(tightened_bar_table(r["bar"]))
    print(f"variants tried for {EDGE_NAME}: {tried}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
