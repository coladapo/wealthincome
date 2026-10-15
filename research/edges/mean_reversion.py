"""Edge 1: oversold-quality mean reversion (long only).

Rule, evaluated on the close of day t using only data up to t:
  quality  = close[t - trend_lag] > SMA(sma_trend)[t - trend_lag]   (trend intact before the dip)
             AND mean(close * volume, dv_window)[t] > min_dollar_vol
  trigger  = RSI(rsi_period)[t] < rsi_max
             OR close[t] <= SMA(sma_short)[t] - atr_mult * ATR(atr_period)[t]
  direction = +1
Optional cooldown: after an event, ignore new triggers on the same symbol for
`cooldown` trading days (0 = every triggering day is an event).

The event study then enters at the t+1 open (see research.event_study).

CLI: venv/bin/python -m research.edges.mean_reversion --since 2019-01-01
"""

from __future__ import annotations

import argparse
import sys
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from research.bars import ETFS, BarStore

EDGE_NAME = "mean_reversion"

DEFAULT_PARAMS: Dict = {
    "sma_trend": 200,
    "trend_lag": 60,
    "min_dollar_vol": 20e6,
    "dv_window": 20,
    "rsi_period": 2,
    "rsi_max": 10.0,
    "atr_period": 14,
    "atr_mult": 2.5,
    "sma_short": 10,
    "use_atr_trigger": True,
    "cooldown": 0,
}


def wilder_rsi(close: pd.Series, period: int) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = up / dn
    rsi = 100 - 100 / (1 + rs)
    return rsi.where(dn != 0, 100.0).where(up.notna())


def wilder_atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()],
                   axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def signals(df: pd.DataFrame, **params) -> pd.DataFrame:
    """Indicator frame + boolean `event` column for one symbol's bars (index = date)."""
    p = {**DEFAULT_PARAMS, **params}
    c = df["close"]
    sma_t = c.rolling(p["sma_trend"], min_periods=p["sma_trend"]).mean()
    trend_ok = (c.shift(p["trend_lag"]) > sma_t.shift(p["trend_lag"]))
    dollar_vol = (c * df["volume"]).rolling(p["dv_window"], min_periods=p["dv_window"]).mean()
    liquid = dollar_vol > p["min_dollar_vol"]
    rsi = wilder_rsi(c, p["rsi_period"])
    trig = rsi < p["rsi_max"]
    atr = wilder_atr(df, p["atr_period"])
    sma_s = c.rolling(p["sma_short"], min_periods=p["sma_short"]).mean()
    if p["use_atr_trigger"]:
        trig = trig | (c <= sma_s - p["atr_mult"] * atr)
    raw = (trend_ok & liquid & trig).fillna(False).astype(bool)
    event = raw
    if p["cooldown"] > 0:
        keep = np.zeros(len(raw), bool)
        last = -10 ** 9
        for i, v in enumerate(raw.values):
            if v and i - last > p["cooldown"]:
                keep[i] = True
                last = i
        event = pd.Series(keep, index=raw.index)
    return pd.DataFrame({"close": c, "sma_trend": sma_t, "trend_ok": trend_ok, "dollar_vol": dollar_vol,
                         "rsi": rsi, "atr": atr, "sma_short": sma_s, "raw_trigger": raw, "event": event})


def generate_events(bars_store: BarStore, start: Optional[str] = None, end: Optional[str] = None,
                    symbols: Optional[Iterable[str]] = None, **params) -> pd.DataFrame:
    """Events DataFrame[symbol, event_date, direction] for the whole stored universe (ETFs excluded)."""
    p = {**DEFAULT_PARAMS, **params}
    syms: List[str] = list(symbols) if symbols is not None else bars_store.symbols(etfs=False)
    warm = None
    if start is not None:
        # warm-up so SMA200 lagged by trend_lag is defined at `start`
        warm = (pd.Timestamp(start) - pd.Timedelta(days=int((p["sma_trend"] + p["trend_lag"]) * 1.6) + 30))
    out = []
    for s in syms:
        if s in {e[0] for e in ETFS} and symbols is None:
            continue
        df = bars_store.bars(s, start=warm, end=end)
        if len(df) < p["sma_trend"] + p["trend_lag"]:
            continue
        sig = signals(df, **p)
        d = sig.index[sig["event"].values]
        if start is not None:
            d = d[d >= pd.Timestamp(start)]
        if len(d):
            out.append(pd.DataFrame({"symbol": s, "event_date": d, "direction": 1}))
    if not out:
        return pd.DataFrame(columns=["symbol", "event_date", "direction"])
    return pd.concat(out, ignore_index=True).sort_values(["event_date", "symbol"]).reset_index(drop=True)


def generate_baseline_events(bars_store: BarStore, start: Optional[str] = None,
                             end: Optional[str] = None, every: int = 5, **params) -> pd.DataFrame:
    """Comparison set: symbol-days that pass the SAME quality filter but with no trigger,
    sampled every `every` trading days. The edge is only interesting if it beats this
    (otherwise the 'edge' is just owning trending large caps in a rising market)."""
    p = {**DEFAULT_PARAMS, **params}
    warm = None
    if start is not None:
        warm = pd.Timestamp(start) - pd.Timedelta(days=int((p["sma_trend"] + p["trend_lag"]) * 1.6) + 30)
    out = []
    for s in bars_store.symbols(etfs=False):
        df = bars_store.bars(s, start=warm, end=end)
        if len(df) < p["sma_trend"] + p["trend_lag"]:
            continue
        sig = signals(df, **p)
        ok = (sig["trend_ok"] & (sig["dollar_vol"] > p["min_dollar_vol"])).fillna(False).to_numpy(bool)
        ok = ok & ((np.arange(len(ok)) % every) == 0)
        d = sig.index[ok]
        if start is not None:
            d = d[d >= pd.Timestamp(start)]
        if len(d):
            out.append(pd.DataFrame({"symbol": s, "event_date": d, "direction": 1}))
    if not out:
        return pd.DataFrame(columns=["symbol", "event_date", "direction"])
    return pd.concat(out, ignore_index=True).sort_values(["event_date", "symbol"]).reset_index(drop=True)


# Pre-declared variants for the first report. Every one is logged in variant_runs.
REPORT_VARIANTS = [
    ("one event per episode (cooldown 5 days)", {"cooldown": 5}),
    ("deeper oversold, RSI(2) < 5 only (no ATR arm)", {"rsi_max": 5.0, "use_atr_trigger": False}),
    ("trend measured today (trend_lag 0)", {"trend_lag": 0}),
]


def full_report(path: str, since: str = "2019-01-01", until: Optional[str] = None,
                db_path: Optional[str] = None, read: Optional[str] = None) -> str:
    from research.event_study import run_event_study, variants_log
    from research.report import write_edge_report, results_table

    store = BarStore(db_path)
    try:
        ev = generate_events(store, since, until)
        base = run_event_study(ev, store=store)
        variants_log(EDGE_NAME, {**DEFAULT_PARAMS, "since": since, "until": until, "cost_bps": 10},
                     base, db_path=db_path)
        robust = run_event_study(ev, store=store, cluster="week")
        vres = []
        for label, over in REPORT_VARIANTS:
            vp = {**DEFAULT_PARAMS, **over}
            r = run_event_study(generate_events(store, since, until, **vp), store=store)
            tried = variants_log(EDGE_NAME, {**vp, "since": since, "until": until, "cost_bps": 10},
                                 r, db_path=db_path)
            vres.append((label, vp, r))
        bl = run_event_study(generate_baseline_events(store, since, until), store=store)
    finally:
        store.close()
    pre = ("Universe: current S&P 500 constituents + SPY/QQQ/IWM (ETFs excluded from events), "
           "daily bars 2019-01-02 onward from yfinance (adjusted). SURVIVORSHIP BIAS: names that "
           "left the index are missing, so every number here is an upper bound. Entry next-day open, "
           "exit close of t+h. 'EV per $100' = mean net return on a $100 position.\n\n"
           "### Baseline: same quality filter, no trigger (every 5th qualifying day)\n\n"
           "The edge only adds value if it beats this. Same costs and slices.\n\n"
           + results_table(bl))
    return write_edge_report(EDGE_NAME, base, DEFAULT_PARAMS, path, variants=vres,
                             variants_tried=tried, read=read, robustness=robust, preamble=pre)


def main(argv: Optional[List[str]] = None) -> int:
    from research.event_study import run_event_study, variants_log

    ap = argparse.ArgumentParser(prog="python -m research.edges.mean_reversion")
    ap.add_argument("--since", default="2019-01-01")
    ap.add_argument("--until", default=None)
    ap.add_argument("--db", default=None)
    ap.add_argument("--cost-bps", type=float, default=10)
    ap.add_argument("--report", default=None, help="write a markdown report to this path")
    ap.add_argument("--full-report", default=None,
                    help="default + pre-declared variants + baseline, written to this path")
    ap.add_argument("--read-file", default=None, help="honest-read paragraph to include")
    for k, v in DEFAULT_PARAMS.items():
        flag = "--" + k.replace("_", "-")
        if isinstance(v, bool):
            ap.add_argument(flag, type=lambda x: x.lower() in ("1", "true", "yes"), default=v)
        else:
            ap.add_argument(flag, type=type(v), default=v)
    a = ap.parse_args(argv)
    params = {k: getattr(a, k) for k in DEFAULT_PARAMS}

    if a.full_report:
        read = open(a.read_file).read().strip() if a.read_file else None
        full_report(a.full_report, a.since, a.until, a.db, read)
        print(f"report: {a.full_report}")
        return 0

    store = BarStore(a.db)
    try:
        events = generate_events(store, a.since, a.until, **params)
        result = run_event_study(events, cost_bps_per_side=a.cost_bps, store=store)
    finally:
        store.close()
    logged = {**params, "since": a.since, "until": a.until, "cost_bps": a.cost_bps}
    tried = variants_log(EDGE_NAME, logged, result, db_path=a.db)
    print(f"{EDGE_NAME} params: {params}")
    print(result.summary())
    print(f"variants tried for {EDGE_NAME}: {tried}")
    if a.report:
        from research.report import write_edge_report
        write_edge_report(EDGE_NAME, result, logged, a.report, variants_tried=tried)
        print(f"report: {a.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
