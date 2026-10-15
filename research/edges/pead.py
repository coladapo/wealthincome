"""Edge 3: post-earnings-announcement drift (PEAD), long arm tradeable.

Event mapping (no look-ahead):
  reaction day = first full trading session in which the market can react:
    bmo     -> the report date itself if it is a trading day, else the next one
    amc     -> the first trading day strictly after the report date
    unknown -> treated like amc (next trading day) -- conservative
  event_date = reaction day, so research.event_study enters at the OPEN of the day
  AFTER the reaction day (the reaction itself is not captured; this tests the drift).
  The informational "react-open" entry mode instead sets event_date to the trading
  day before the reaction day, entering at the reaction-day open (the earliest moment
  a live system could act on a known surprise).

Signal: direction = sign of the EPS surprise.
  long  if surprise_pct >= min_surprise   (the live system can only do this arm)
  short if surprise_pct <= -min_surprise  (informational only)
  optional confirmation: earnings gap = open[reaction] / close[reaction - 1] - 1 in the
  same direction with |gap| >= gap_min.
  optional top decile: surprise above the 90th percentile of all surprises reported in
  the trailing 365 days (strictly earlier events only, so no look-ahead).

No-signal baseline: every earnings event with a known surprise, long, same mapping.

The bar (2026-10-14): beat the no-signal baseline AND SPY buy-and-hold over the same
horizon with the date-clustered 95% CI of the DIFFERENCE excluding 0; n >= 200;
positive after 2x cost; holds in the held-out last 40% and does not lose in 2022;
variants counted. The difference CIs live here (event_study.py is not edited).

CLI: venv/bin/python -m research.edges.pead --since 2019-01-01 --full-report [path]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from research.bars import BarStore

EDGE_NAME = "pead"
HORIZONS = (1, 5, 10, 20, 40, 60)
COST_BPS = 10
STRESS_MULT = 2.0
HOLDOUT_FRAC = 0.4
BEAR = ("2022-01-03", "2022-12-30")
MIN_EVENTS = 200
DEFAULT_REPORT = ("/Users/wivak/.claude/shared-context/2026-10-14-audit-wealthincome-c7e2/"
                  "artifacts/edge-pead-report.md")

DEFAULT_PARAMS: Dict = {
    "min_surprise": 5.0,      # percent
    "gap_min": None,          # percent; None = no gap confirmation
    "top_decile": False,
    "entry": "after_reaction",  # or "react_open"
    "side": "long",           # long | short | baseline
}


# --------------------------------------------------------------------------- mapping

def reaction_day(report_date, timing: str, trading_days: pd.DatetimeIndex) -> Optional[pd.Timestamp]:
    """First full session that can react to the report (see module docstring)."""
    d = pd.Timestamp(report_date).normalize()
    side = "left" if timing == "bmo" else "right"
    i = trading_days.searchsorted(d, side=side)
    return trading_days[i] if i < len(trading_days) else None


def map_reaction_days(earn: pd.DataFrame, trading_days: pd.DatetimeIndex) -> pd.Series:
    """Vectorised reaction_day for a frame with report_date + timing (NaT past the data)."""
    d = pd.to_datetime(earn["report_date"]).dt.normalize().values
    bmo = (earn["timing"].values == "bmo")
    i = np.where(bmo, trading_days.searchsorted(d, side="left"), trading_days.searchsorted(d, side="right"))
    ok = i < len(trading_days)
    vals = np.where(ok, trading_days.values[np.minimum(i, len(trading_days) - 1)], np.datetime64("NaT", "ns"))
    return pd.Series(pd.to_datetime(vals), index=earn.index)


def signal_direction(surprise_pct: pd.Series, gap_pct: Optional[pd.Series], min_surprise: float,
                     gap_min: Optional[float], side: str) -> pd.Series:
    """+1 / -1 / 0 per event."""
    s = surprise_pct.astype(float)
    if side == "baseline":
        return pd.Series(np.where(s.notna(), 1, 0), index=s.index)
    if side == "long":
        d = np.where(s >= min_surprise, 1, 0)
    elif side == "short":
        d = np.where(s <= -min_surprise, -1, 0)
    else:
        raise ValueError("side must be long, short or baseline")
    d = pd.Series(d, index=s.index)
    if gap_min is not None:
        g = gap_pct.astype(float)
        confirmed = (np.sign(g) == d) & (g.abs() >= gap_min)
        d = d.where(confirmed.fillna(False), 0)
    return d


def trailing_decile_flag(earn: pd.DataFrame, window_days: int = 365, q: float = 0.9,
                         min_history: int = 100) -> pd.Series:
    """True when surprise exceeds the q-quantile of surprises reported in the trailing
    window, using only events reported STRICTLY before this one's report date."""
    e = earn[["report_date", "surprise_pct"]].copy()
    e["rd"] = pd.to_datetime(e["report_date"])
    order = e.sort_values("rd")
    rds, sv = order["rd"].values, order["surprise_pct"].astype(float).values
    flags = np.zeros(len(order), bool)
    for k, (rd, s) in enumerate(zip(rds, sv)):
        lo = np.searchsorted(rds, rd - np.timedelta64(window_days, "D"), side="left")
        hi = np.searchsorted(rds, rd, side="left")
        hist = sv[lo:hi]
        hist = hist[~np.isnan(hist)]
        if len(hist) >= min_history and not np.isnan(s):
            flags[k] = s > np.quantile(hist, q)
    return pd.Series(flags, index=order.index).reindex(earn.index)


# --------------------------------------------------------------------------- events

def _gaps(store: BarStore, earn: pd.DataFrame) -> pd.Series:
    """Earnings gap % = open[reaction] / close[previous session] - 1, per event."""
    out = pd.Series(np.nan, index=earn.index)
    for sym, g in earn.groupby("symbol", sort=False):
        b = store.bars(sym)
        if b.empty:
            continue
        idx = b.index
        pos = idx.get_indexer(pd.to_datetime(g["reaction_date"]))
        ok = pos >= 1
        o, c = b["open"].values, b["close"].values
        p = np.where(ok, pos, 1)
        out.loc[g.index] = np.where(ok, (o[p] / c[p - 1] - 1.0) * 100.0, np.nan)
    return out


MAX_REACTION_LAG_DAYS = 7


def attach_reaction(e: pd.DataFrame, tdays: pd.DatetimeIndex,
                    max_lag_days: int = MAX_REACTION_LAG_DAYS) -> pd.DataFrame:
    """Add reaction_date + prev_session; drop reports with no session within max_lag_days
    (e.g. reports dated before the bar calendar starts would otherwise all collapse onto
    its first day)."""
    e = e.copy()
    e["reaction_date"] = map_reaction_days(e, tdays)
    lag = (e["reaction_date"] - pd.to_datetime(e["report_date"]).dt.normalize()).dt.days
    e = e[e["reaction_date"].notna() & (lag >= 0) & (lag <= max_lag_days)].copy()
    pos = tdays.get_indexer(e["reaction_date"])
    e["prev_session"] = pd.to_datetime(np.where(pos >= 1, tdays.values[np.maximum(pos - 1, 0)],
                                                np.datetime64("NaT", "ns")))
    return e


def prepare_earnings(store: BarStore, earn: pd.DataFrame, start: Optional[str] = None,
                     end: Optional[str] = None) -> pd.DataFrame:
    """Attach reaction_date, prev_session, gap_pct and top-decile flag to raw earnings rows."""
    spy = store.bars("SPY")
    tdays = pd.DatetimeIndex(spy.index if len(spy) else sorted(
        {d for s in earn["symbol"].unique() for d in store.bars(s).index}))
    e = earn[earn["surprise_pct"].notna()].copy()
    e = e[e["symbol"].isin(set(store.symbols(etfs=False)))]
    # decile history uses every reported surprise, including ones before the bar store begins
    e["top_decile"] = trailing_decile_flag(e)
    e = attach_reaction(e, tdays)
    if start is not None:
        e = e[e["reaction_date"] >= pd.Timestamp(start)]
    if end is not None:
        e = e[e["reaction_date"] <= pd.Timestamp(end)]
    e = e.drop_duplicates(subset=["symbol", "reaction_date"], keep="last").reset_index(drop=True)
    e["gap_pct"] = _gaps(store, e)
    return e


def generate_events(prepared: pd.DataFrame, **params) -> pd.DataFrame:
    """Events DataFrame[symbol, event_date, direction, surprise_pct, gap_pct]."""
    p = {**DEFAULT_PARAMS, **params}
    d = signal_direction(prepared["surprise_pct"], prepared["gap_pct"], p["min_surprise"],
                         p["gap_min"], p["side"])
    if p["top_decile"] and p["side"] != "baseline":
        d = d.where(prepared["top_decile"].fillna(False).astype(bool) & (d > 0), 0)
    sel = prepared[d != 0].copy()
    sel["direction"] = d[d != 0].astype(int)
    col = "reaction_date" if p["entry"] == "after_reaction" else "prev_session"
    if p["entry"] not in ("after_reaction", "react_open"):
        raise ValueError("entry must be after_reaction or react_open")
    sel["event_date"] = sel[col]
    sel = sel[sel["event_date"].notna()]
    return sel[["symbol", "event_date", "direction", "surprise_pct", "gap_pct"]].sort_values(
        ["event_date", "symbol"]).reset_index(drop=True)


# --------------------------------------------------------------------------- comparison

def spy_returns(trades: pd.DataFrame, store: BarStore, horizons: Sequence[int]) -> pd.DataFrame:
    """SPY buy-and-hold over each trade's exact window: open of entry_date to close h-1
    sessions later (the same window event_study uses for the stock)."""
    spy = store.bars("SPY")
    idx = spy.index
    o, c = spy["open"].values, spy["close"].values
    pos = idx.get_indexer(pd.to_datetime(trades["entry_date"]))
    out = pd.DataFrame(index=trades.index)
    for h in horizons:
        x = pos + h - 1
        ok = (pos >= 0) & (x < len(idx))
        out[f"spy_{h}"] = np.where(ok, c[np.where(ok, x, 0)] / o[np.where(ok, pos, 0)] - 1.0, np.nan)
    return out


def _date_groups(dates, cluster: str) -> np.ndarray:
    di = pd.DatetimeIndex(pd.to_datetime(dates))
    if cluster == "month":
        return di.to_period("M").astype(str).values
    if cluster == "week":
        return di.to_period("W").astype(str).values
    return di.strftime("%Y-%m-%d").values


def diff_ci(a: np.ndarray, a_dates, b: np.ndarray, b_dates, n_boot: int = 1000,
            level: float = 0.95, seed: int = 7, cluster: str = "date") -> Tuple[float, float, float]:
    """mean(a) - mean(b) with a percentile bootstrap CI that resamples whole date clusters
    JOINTLY for both samples (same draw of dates for a and b, so market-wide shocks that
    hit both sets on a date cancel instead of inflating the interval)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    ma, mb = ~np.isnan(a), ~np.isnan(b)
    a, b = a[ma], b[mb]
    ga, gb = _date_groups(np.asarray(a_dates)[ma], cluster), _date_groups(np.asarray(b_dates)[mb], cluster)
    if len(a) < 2 or len(b) < 2:
        return (float("nan"), float("nan"), float("nan"))
    codes, uniq = pd.factorize(np.concatenate([ga, gb]))
    ca, cb = codes[:len(a)], codes[len(a):]
    k = len(uniq)
    sa, na = np.bincount(ca, weights=a, minlength=k), np.bincount(ca, minlength=k).astype(float)
    sb, nb = np.bincount(cb, weights=b, minlength=k), np.bincount(cb, minlength=k).astype(float)
    rng = np.random.default_rng(seed)
    diffs = np.empty(n_boot)
    for i in range(n_boot):
        w = np.bincount(rng.integers(0, k, size=k), minlength=k).astype(float)
        da, db = w @ na, w @ nb
        diffs[i] = (w @ sa) / da - (w @ sb) / db if da and db else np.nan
    diffs = diffs[~np.isnan(diffs)]
    q = (1 - level) / 2
    lo, hi = np.quantile(diffs, [q, 1 - q])
    return (float(a.mean() - b.mean()), float(lo), float(hi))


def mean_ci(v: np.ndarray, dates, n_boot=1000, level=0.95, seed=7, cluster="date"):
    from research.event_study import bootstrap_mean_ci
    v = np.asarray(v, float)
    m = ~np.isnan(v)
    if m.sum() < 2:
        return (float("nan"), float("nan"), float("nan"))
    lo, hi = bootstrap_mean_ci(v[m], _date_groups(np.asarray(dates)[m], cluster), n_boot=n_boot,
                               level=level, seed=seed)
    return (float(v[m].mean()), lo, hi)


def compare(sig_trades: pd.DataFrame, base_trades: pd.DataFrame, store: BarStore,
            horizons: Sequence[int] = HORIZONS, cost_bps: float = COST_BPS,
            stress_mult: float = STRESS_MULT, holdout_start: Optional[str] = None,
            cluster: str = "date", n_boot: int = 1000) -> List[Dict]:
    """Per (slice, horizon): signal net mean, baseline net mean, diff vs baseline + CI,
    excess vs SPY + CI. Slices: all, holdout, bear 2022, stress (2x cost), holdout stress."""
    rt = 2 * cost_bps / 1e4
    st = sig_trades.reset_index(drop=True)
    bt = base_trades.reset_index(drop=True)
    sspy, bspy = spy_returns(st, store, horizons), spy_returns(bt, store, horizons)
    sd, bd = pd.to_datetime(st["event_date"]).values, pd.to_datetime(bt["event_date"]).values
    if holdout_start is None:
        n = len(st)
        holdout_start = str(pd.Timestamp(sd[min(int(np.floor(n * (1 - HOLDOUT_FRAC))), n - 1)]).date())
    hcut = np.datetime64(holdout_start)
    blo, bhi = np.datetime64(BEAR[0]), np.datetime64(BEAR[1])
    slices = [("all", lambda d: np.ones(len(d), bool), rt),
              ("holdout", lambda d: d >= hcut, rt),
              ("bear 2022", lambda d: (d >= blo) & (d <= bhi), rt),
              (f"stress x{stress_mult:g} cost", lambda d: np.ones(len(d), bool), rt * stress_mult),
              (f"holdout stress x{stress_mult:g}", lambda d: d >= hcut, rt * stress_mult)]
    rows = []
    for name, fn, cost in slices:
        ms, mb = fn(sd), fn(bd)
        for h in horizons:
            s = st[f"ret_{h}"].values.astype(float)[ms] - cost
            b = bt[f"ret_{h}"].values.astype(float)[mb] - cost
            xs = s - sspy[f"spy_{h}"].values[ms]
            nm, nlo, nhi = mean_ci(s, sd[ms], n_boot=n_boot, cluster=cluster)
            dm, dlo, dhi = diff_ci(s, sd[ms], b, bd[mb], n_boot=n_boot, cluster=cluster)
            xm, xlo, xhi = mean_ci(xs, sd[ms], n_boot=n_boot, cluster=cluster)
            rows.append({"slice": name, "horizon": h, "n": int((~np.isnan(s)).sum()),
                         "net": nm, "net_lo": nlo, "net_hi": nhi,
                         "win": float((s[~np.isnan(s)] > 0).mean()) if (~np.isnan(s)).any() else float("nan"),
                         "n_base": int((~np.isnan(b)).sum()), "base": float(np.nanmean(b)) if len(b) else float("nan"),
                         "d_base": dm, "d_base_lo": dlo, "d_base_hi": dhi,
                         "x_spy": xm, "x_spy_lo": xlo, "x_spy_hi": xhi})
    return rows


def _get(rows, sl, h):
    return next((r for r in rows if r["slice"] == sl and r["horizon"] == h), {})


def bar_check(rows: List[Dict], horizons: Sequence[int] = HORIZONS,
              stress_mult: float = STRESS_MULT) -> Dict[int, Dict[str, bool]]:
    def pos(v):
        return v is not None and not np.isnan(v) and v > 0
    out = {}
    for h in horizons:
        a, ho, be = _get(rows, "all", h), _get(rows, "holdout", h), _get(rows, "bear 2022", h)
        st, hst = _get(rows, f"stress x{stress_mult:g} cost", h), _get(rows, f"holdout stress x{stress_mult:g}", h)
        c = {
            "beats baseline (CI>0)": pos(a.get("d_base_lo")),
            "beats SPY (CI>0)": pos(a.get("x_spy_lo")),
            "n>=200": (a.get("n") or 0) >= MIN_EVENTS,
            "2x cost: net>0 and >SPY": pos(st.get("net")) and pos(st.get("x_spy")),
            "holdout: >baseline, >SPY, net>0 at 2x": pos(ho.get("d_base")) and pos(ho.get("x_spy"))
                                                     and pos(hst.get("net")),
            "2022: net>=0": be.get("n", 0) > 0 and be.get("net") is not None and be["net"] >= 0,
        }
        c["CLEARS BAR"] = all(c.values())
        out[h] = c
    return out


# --------------------------------------------------------------------------- report

def _p(v, nd=2):
    return "-" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v*100:.{nd}f}"


def compare_table(rows: List[Dict]) -> str:
    lines = ["| slice | h | n | net % [95% CI] | win % | baseline net % | edge - baseline % [95% CI] | "
             "edge - SPY % [95% CI] |", "|---|---:|---:|---|---:|---:|---|---|"]
    for r in rows:
        if not r["n"]:
            lines.append(f"| {r['slice']} | {r['horizon']} | 0 | - | - | - | - | - |")
            continue
        lines.append(
            f"| {r['slice']} | {r['horizon']} | {r['n']} | {_p(r['net'])} [{_p(r['net_lo'])}, {_p(r['net_hi'])}] | "
            f"{_p(r['win'], 1)} | {_p(r['base'])} | {_p(r['d_base'])} [{_p(r['d_base_lo'])}, {_p(r['d_base_hi'])}] | "
            f"{_p(r['x_spy'])} [{_p(r['x_spy_lo'])}, {_p(r['x_spy_hi'])}] |")
    return "\n".join(lines)


def bar_table(chk: Dict[int, Dict[str, bool]]) -> str:
    keys = list(next(iter(chk.values())).keys())
    lines = ["| h | " + " | ".join(keys) + " |", "|---:|" + "---|" * len(keys)]
    for h, c in chk.items():
        lines.append(f"| {h} | " + " | ".join("yes" if c[k] else "no" for k in keys) + " |")
    return "\n".join(lines)


REPORT_VARIANTS = [
    ("surprise >= 10%", {"min_surprise": 10.0}),
    ("surprise >= 5% AND gap >= 2% same direction", {"gap_min": 2.0}),
    ("top-decile surprise (trailing 365d, no look-ahead)", {"top_decile": True}),
    ("entry at the reaction-day open (captures the gap; informational timing check)",
     {"entry": "react_open"}),
]


def run_variant(prepared: pd.DataFrame, store: BarStore, params: Dict, base_trades_by_entry: Dict,
                since: str, until: Optional[str], db_path: Optional[str], cluster="date"):
    from research.event_study import run_event_study, variants_log
    ev = generate_events(prepared, **params)
    res = run_event_study(ev[["symbol", "event_date", "direction"]], horizons=HORIZONS,
                          cost_bps_per_side=COST_BPS, stress_mult=STRESS_MULT,
                          holdout_frac=HOLDOUT_FRAC, bear_windows=(BEAR,), store=store)
    tried = variants_log(EDGE_NAME, {**params, "since": since, "until": until, "cost_bps": COST_BPS,
                                     "horizons": list(HORIZONS)}, res, db_path=db_path)
    if params["entry"] not in base_trades_by_entry:
        bev = generate_events(prepared, **{**params, "side": "baseline", "gap_min": None,
                                           "top_decile": False})
        base_trades_by_entry[params["entry"]] = run_event_study(
            bev[["symbol", "event_date", "direction"]], horizons=HORIZONS, cost_bps_per_side=COST_BPS,
            stress_mult=STRESS_MULT, holdout_frac=HOLDOUT_FRAC, bear_windows=(BEAR,), store=store)
    bres = base_trades_by_entry[params["entry"]]
    rows = compare(res.trades, bres.trades, store, holdout_start=res.params.get("holdout_start"),
                   cluster=cluster)
    rows_month = compare(res.trades, bres.trades, store, holdout_start=res.params.get("holdout_start"),
                         cluster="month")
    return ev, res, rows, rows_month, tried


def full_report(path: str = DEFAULT_REPORT, since: str = "2019-01-01", until: Optional[str] = None,
                db_path: Optional[str] = None, read: Optional[str] = None) -> Dict:
    from research.earnings import coverage, load_earnings
    from research.report import results_table

    store = BarStore(db_path)
    out: Dict = {}
    try:
        earn = load_earnings(store.conn)
        prepared = prepare_earnings(store, earn, since, until)
        cov = coverage(store.conn, since=since)
        bases: Dict = {}
        default = {**DEFAULT_PARAMS}
        ev, res, rows, rows_month, tried = run_variant(prepared, store, default, bases, since, until,
                                                       db_path)
        variants = []
        for label, over in REPORT_VARIANTS:
            vp = {**DEFAULT_PARAMS, **over}
            vev, vres, vrows, vrows_m, tried = run_variant(prepared, store, vp, bases, since, until, db_path)
            variants.append((label, vp, vev, vres, vrows, vrows_m))
        sp = {**DEFAULT_PARAMS, "side": "short"}
        sev, sres, srows, _, tried = run_variant(prepared, store, sp, bases, since, until, db_path)
        base_res = bases["after_reaction"]
    finally:
        store.close()

    chk = bar_check(rows)
    chk_m = bar_check(rows_month)
    vchk = [(label, bar_check(vrows), bar_check(vrows_m)) for label, _, _, _, vrows, vrows_m in variants]
    out.update({"coverage": cov, "default_rows": rows, "bar": chk, "bar_month": chk_m,
                "variants_tried": tried, "variant_bars": vchk, "n_default": res.n_events_used,
                "holdout_start": res.params.get("holdout_start")})

    tim = prepared["timing"].value_counts().to_dict()
    sur = prepared["surprise_pct"]
    parts = [
        "# Edge report: post-earnings-announcement drift (PEAD)", "",
        f"Generated by `venv/bin/python -m research.edges.pead --since {since} --full-report`.", "",
    ]
    if read:
        parts += ["## Honest read", "", read, ""]
    parts += [
        "## Earnings data coverage", "",
        "Source: yfinance `Ticker.get_earnings_dates(limit=60)` (Yahoo earnings calendar). Each row has "
        "the report timestamp, EPS estimate (consensus), reported EPS and surprise %. Timing is read from "
        "the timestamp's clock time: before 09:30 ET = before open (bmo), 16:00 or later = after close "
        "(amc), anything else = unknown (treated as amc: react next session).", "",
        f"- Coverage (report dates since {since}): `{json.dumps(cov, default=str)}`",
        f"- Events with a surprise, mapped to a reaction day inside the bar store: {len(prepared)} "
        f"(timing {tim}).",
        f"- Surprise %: median {sur.median():.2f}, share >= +5%: {(sur >= 5).mean():.1%}, "
        f"share <= -5%: {(sur <= -5).mean():.1%}.",
        "- Limitations: EPS only (no revenue surprise, no guidance); Yahoo's stored estimate is not a "
        "guaranteed point-in-time consensus; surprise % is unstable when the estimate is near zero; "
        "universe is CURRENT S&P 500 constituents (survivorship bias, numbers are an upper bound).", "",
        "## Method", "",
        "Reaction day = first full session that can react (bmo -> same day if a session, amc/unknown -> next "
        "session). event_date = reaction day, so the harness enters at the NEXT open (day after the reaction) "
        "and exits at the close h sessions after the reaction day. This tests the drift, not the gap. "
        f"Cost {COST_BPS} bp/side ({2*COST_BPS} bp round trip), stress = 2x. Horizons {list(HORIZONS)} sessions.",
        "",
        "Comparisons (net of cost on both sides where it applies):",
        "- **edge - baseline**: mean net return of the signal events minus mean net return of ALL earnings "
        "events (no surprise filter, long, same timing). CI: bootstrap that resamples whole event dates "
        "jointly for both sets.",
        "- **edge - SPY**: per event, net return minus SPY's return over the exact same open-to-close window; "
        "CI: bootstrap of the mean resampling whole event dates (or months, in the robustness tables).",
        f"- Holdout = last 40% of signal events by date (starts {res.params.get('holdout_start')}); the same "
        "cut date is applied to the baseline. Bear = 2022-01-03..2022-12-30.", "",
        "## Bar summary: every variant x horizon", "",
        "`date` = the bar as written (CIs clustered by event date). `month` = the same checks with CIs "
        "clustered by calendar month. Holds of 20-60 sessions overlap across different event dates, so "
        "date clustering understates uncertainty at those horizons; month is the honest check there.", "",
        "| variant | " + " | ".join(f"h{h} date / month" for h in HORIZONS) + " |",
        "|---|" + "---|" * len(HORIZONS),
    ]
    yn = {True: "PASS", False: "no"}
    for label, c_d, c_m in [("default: surprise >= 5%", chk, chk_m)] + vchk:
        parts.append(f"| {label} | " + " | ".join(
            f"{yn[c_d[h]['CLEARS BAR']]} / {yn[c_m[h]['CLEARS BAR']]}" for h in HORIZONS) + " |")
    parts += [
        "",
        "## Default: long, surprise >= +5%", "",
        f"Events: {res.n_events_in} generated, {res.n_events_used} usable.", "",
        compare_table(rows), "", "### Bar check (default, date-clustered)", "", bar_table(chk), "",
        "### Robustness: same comparison, CI clustered by calendar MONTH", "",
        compare_table([r for r in rows_month if r["slice"] in ("all", "holdout", "holdout stress x2")]), "",
        bar_table(chk_m), "",
        "## No-signal baseline: every earnings event, long", "",
        f"Events usable: {base_res.n_events_used}.", "", results_table(base_res), "",
    ]
    for label, vp, vev, vres, vrows, vrows_m in variants:
        parts += [f"## Variant: {label}", "", f"Params: `{json.dumps(vp, default=str)}`; events usable "
                  f"{vres.n_events_used}.", "", compare_table(vrows), "",
                  "Bar check, date-clustered:", "", bar_table(bar_check(vrows)), "",
                  "Month-clustered (all / holdout / holdout at 2x cost):", "",
                  compare_table([r for r in vrows_m if r["slice"] in ("all", "holdout", "holdout stress x2")]),
                  "", bar_table(bar_check(vrows_m)), ""]
    parts += ["## Informational: short arm (surprise <= -5%), NOT tradeable by the live system", "",
              "Returns are direction-signed (positive = the short made money). The baseline comparison is "
              "against the long baseline, so 'edge - baseline' is not meaningful here; read the net and "
              "SPY columns (edge - SPY for a short = short return minus SPY long return).", "",
              f"Events usable: {sres.n_events_used}.", "",
              compare_table([r for r in srows if r["slice"] in ("all", "holdout", "bear 2022")]), "",
              "## Variants tried", "",
              f"Distinct parameter sets logged for `{EDGE_NAME}` in variant_runs: **{tried}** "
              "(default + 4 listed variants + the informational short arm). Any 'best variant' number is "
              "optimistic by that many looks.", ""]
    text = "\n".join(parts)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    out["text"] = text
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research.edges.pead")
    ap.add_argument("--since", default="2019-01-01")
    ap.add_argument("--until", default=None)
    ap.add_argument("--db", default=None)
    ap.add_argument("--full-report", nargs="?", const=DEFAULT_REPORT, default=None,
                    help="write the full report (default path: the audit artifacts folder)")
    ap.add_argument("--read-file", default=None, help="honest-read paragraph to include")
    a = ap.parse_args(argv)
    if a.full_report:
        read = open(a.read_file).read().strip() if a.read_file else None
        out = full_report(a.full_report, a.since, a.until, a.db, read)
        print(compare_table([r for r in out["default_rows"] if r["slice"] == "all"]))
        print(bar_table(out["bar"]))
        print("default (month CI)", {h: c["CLEARS BAR"] for h, c in out["bar_month"].items()})
        for label, chk, chk_m in out["variant_bars"]:
            print(label, "date:", {h: c["CLEARS BAR"] for h, c in chk.items()},
                  "month:", {h: c["CLEARS BAR"] for h, c in chk_m.items()})
        print(f"variants tried: {out['variants_tried']}  report: {a.full_report}")
        return 0
    store = BarStore(a.db)
    try:
        from research.earnings import load_earnings
        prepared = prepare_earnings(store, load_earnings(store.conn), a.since, a.until)
        ev = generate_events(prepared)
    finally:
        store.close()
    print(f"{len(ev)} default PEAD events since {a.since}; use --full-report for the study")
    return 0


if __name__ == "__main__":
    sys.exit(main())
