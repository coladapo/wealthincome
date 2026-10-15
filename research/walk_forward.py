"""Walk-forward evaluation of a Strategy at the whole-account level.

Windows (default): 24-month train / 6-month test, stepping 6 months, from 2019-01-01
to the last stored session. The first test window therefore starts 2021-01-01.

For each window:
  1. params = strategy.fit(AsOfData(train_end))   (default fit = no-op -> same params)
  2. simulate the test window with those params, starting from the PREVIOUS test
     window's ending portfolio (cash, positions, stops, queued orders carry over),
     so the stitched out-of-sample equity is one continuous account -- no artificial
     liquidation/re-entry at window boundaries.
Stitching requires step == test length (non-overlapping, gap-free test windows).

Reported over the stitched out-of-sample dates, at 1x (10 bp/side) and 2x cost:
  whole-account total return, CAGR, Sharpe, max drawdown, average exposure
  (invested / equity), annual turnover (traded notional / equity, both sides);
  daily EXCESS return vs SPY buy-and-hold (close-to-close, fully invested,
  cost-free -- conservative for the strategy) and vs the champion's equity series
  over the dates both exist, with bootstrap 95% CIs of the mean daily excess,
  clustered by date (research/compare.py's _diff_stats) and, more conservatively,
  by calendar month (captures autocorrelation within a month).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from research.asof import AsOfData, MarketData
from research.compare import _diff_stats
from research.event_study import bootstrap_mean_ci
from research.portfolio_sim import Chassis, SimState, simulate, summarize


@dataclass
class Window:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp

    def as_dict(self) -> Dict[str, str]:
        return {k: getattr(self, k).strftime("%Y-%m-%d") for k in
                ("train_start", "train_end", "test_start", "test_end")}


def make_windows(start="2019-01-01", end=None, train_months: int = 24, test_months: int = 6,
                 step_months: int = 6) -> List[Window]:
    if step_months != test_months:
        raise ValueError("stitching needs step_months == test_months (gap-free, non-overlapping tests)")
    s = pd.Timestamp(start)
    end = pd.Timestamp(end) if end is not None else pd.Timestamp.today().normalize()
    out = []
    while True:
        ts = s + pd.DateOffset(months=train_months)
        if ts > end:
            break
        te = min(ts + pd.DateOffset(months=test_months) - pd.Timedelta(days=1), end)
        out.append(Window(s, ts - pd.Timedelta(days=1), ts, te))
        s = s + pd.DateOffset(months=step_months)
    return out


@dataclass
class StitchedRun:
    cost_mult: float
    daily: pd.DataFrame
    trades: pd.DataFrame
    violations: Dict[str, int]
    summary: Dict


@dataclass
class WalkForwardResult:
    strategy_id: str
    version: str
    chassis: Chassis
    windows: List[Dict]
    runs: Dict[float, StitchedRun]
    vs_spy: Dict[float, Dict]
    vs_champion: Dict[float, Dict]
    spy_summary: Dict
    notes: List[str] = field(default_factory=list)

    @property
    def base(self) -> StitchedRun:
        return self.runs[min(self.runs)]


def excess_stats(strat_ret: pd.Series, bench_ret: pd.Series, n_boot: int = 2000,
                 level: float = 0.95, seed: int = 7) -> Dict:
    """Mean daily excess (strategy - benchmark) with date- and month-clustered bootstrap CIs."""
    j = pd.concat([strat_ret.rename("s"), bench_ret.rename("b")], axis=1, join="inner").dropna()
    if len(j) < 2:
        return {"n_days": int(len(j)), "mean_daily": None}
    ex = (j["s"] - j["b"]).values
    dates = j.index.values
    d = _diff_stats(ex, dates, n_boot, level, seed)
    months = pd.DatetimeIndex(j.index).strftime("%Y-%m").values
    mlo, mhi = bootstrap_mean_ci(ex, months, n_boot=n_boot, level=level, seed=seed)
    return {
        "n_days": int(len(j)), "start": j.index[0].strftime("%Y-%m-%d"),
        "end": j.index[-1].strftime("%Y-%m-%d"),
        "strategy_total": float((1 + j["s"]).prod() - 1), "bench_total": float((1 + j["b"]).prod() - 1),
        "mean_daily": d["diff"], "ci_low": d["ci_low"], "ci_high": d["ci_high"],
        "ci_month_low": mlo, "ci_month_high": mhi,
        "ann_excess": d["diff"] * 252, "ann_ci_low": d["ci_low"] * 252, "ann_ci_high": d["ci_high"] * 252,
        "ann_ci_month_low": mlo * 252, "ann_ci_month_high": mhi * 252,
    }


def excess_from_equity(strat_equity: pd.Series, bench_equity: pd.Series, n_boot: int = 2000,
                       level: float = 0.95, seed: int = 7) -> Dict:
    """excess_stats over the dates BOTH equity curves were observed.

    Returns are taken between consecutive common dates, so a benchmark with missing days
    (e.g. the live account's equity log has gaps when the trader was down) is compared
    over exactly the same intervals instead of a multi-day return against a one-day one.
    """
    j = pd.concat([strat_equity.astype(float).rename("s"), bench_equity.astype(float).rename("b")],
                  axis=1, join="inner").dropna().sort_index()
    r = j.pct_change().iloc[1:]
    return excess_stats(r["s"], r["b"], n_boot=n_boot, level=level, seed=seed)


def spy_returns(market: MarketData, dates: pd.DatetimeIndex) -> pd.Series:
    c = market.frame("SPY")["close"].reindex(dates)
    r = c.pct_change()
    if len(r):
        r.iloc[0] = 0.0
    return r.rename("spy")


def walk_forward(strategy, market: MarketData, start="2019-01-01", end=None,
                 train_months: int = 24, test_months: int = 6, step_months: int = 6,
                 chassis: Optional[Chassis] = None, cost_mults: Sequence[float] = (1.0, 2.0),
                 initial_cash: float = 100_000.0, champion_equity: Optional[pd.Series] = None,
                 test_start=None, n_boot: int = 2000, seed: int = 7, now=None) -> WalkForwardResult:
    """Run the walk-forward. `test_start` (optional) drops test windows ending before it."""
    td = market.trading_days()
    end = pd.Timestamp(end) if end is not None else td[-1]
    wins = make_windows(start, end, train_months, test_months, step_months)
    if test_start is not None:
        wins = [w for w in wins if w.test_end >= pd.Timestamp(test_start)]
        if wins and wins[0].test_start < pd.Timestamp(test_start):
            wins[0] = Window(wins[0].train_start, wins[0].train_end, pd.Timestamp(test_start),
                             wins[0].test_end)
    if not wins:
        raise ValueError("no walk-forward windows in the requested range")
    chassis = chassis or getattr(strategy, "chassis", None)

    fitted = []
    for w in wins:
        p = strategy.fit(AsOfData(w.train_end.date(), market, after_close=True, now=now))
        fitted.append(strategy.with_params(p) if p else strategy)

    runs: Dict[float, StitchedRun] = {}
    for cm in cost_mults:
        state: Optional[SimState] = None
        dailies, trades, viol = [], [], {}
        for k, (w, strat) in enumerate(zip(wins, fitted)):
            res = simulate(strat, w.test_start, w.test_end, market, chassis=chassis, cost_mult=cm,
                           initial_cash=initial_cash, state=state, now=now)
            state = res.state
            d = res.daily.copy()
            d["window"] = k
            dailies.append(d)
            t = res.trades.copy()
            t["window"] = k
            trades.append(t)
            for kk, v in res.violations.items():
                viol[kk] = viol.get(kk, 0) + v
            chassis_used = res.chassis
        daily = pd.concat(dailies)
        if len(daily):
            daily.iloc[0, daily.columns.get_loc("ret")] = 0.0
        tr = pd.concat(trades, ignore_index=True)
        runs[cm] = StitchedRun(cm, daily, tr, viol, summarize(daily, tr))

    base = runs[min(runs)].daily
    spy_r = spy_returns(market, base.index)
    spy_eq = (1 + spy_r.fillna(0)).cumprod() * initial_cash
    spy_summary = summarize(pd.DataFrame({"equity": spy_eq, "ret": spy_r.fillna(0)}))
    vs_spy = {cm: excess_stats(r.daily["ret"].iloc[1:], spy_r.iloc[1:], n_boot, seed=seed)
              for cm, r in runs.items()}
    vs_champ: Dict[float, Dict] = {}
    notes = []
    if champion_equity is not None and len(champion_equity) > 1:
        for cm, r in runs.items():
            vs_champ[cm] = excess_from_equity(r.daily["equity"], champion_equity, n_boot, seed=seed)
        notes.append(f"champion series covers {champion_equity.index[0].date()}..."
                     f"{champion_equity.index[-1].date()} only; the vs-champion comparison uses the "
                     "dates both were observed (returns between common dates)")
    return WalkForwardResult(strategy_id=strategy.id, version=strategy.version,
                             chassis=chassis_used, windows=[w.as_dict() for w in wins],
                             runs=runs, vs_spy=vs_spy, vs_champion=vs_champ,
                             spy_summary=spy_summary, notes=notes)


def _pct(v, nd=2):
    return "-" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v * 100:.{nd}f}%"


def _num(v, nd=2):
    return "-" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:.{nd}f}"


def format_table(res: WalkForwardResult) -> str:
    lines = [f"Walk-forward: {res.strategy_id} v{res.version[:12]}  chassis: {res.chassis.label()}",
             f"windows: {len(res.windows)} (first test {res.windows[0]['test_start']}, "
             f"last test ends {res.windows[-1]['test_end']})", "",
             "| run | total return | CAGR | Sharpe | max DD | avg exposure | turnover/yr | trades | stops |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    s = res.spy_summary
    lines.append(f"| SPY buy-and-hold (benchmark) | {_pct(s['total_return'])} | {_pct(s['cagr'])} | "
                 f"{_num(s['sharpe'])} | {_pct(s['max_drawdown'])} | 100% | - | - | - |")
    for cm, r in res.runs.items():
        m = r.summary
        lines.append(f"| strategy @ {cm:g}x cost | {_pct(m['total_return'])} | {_pct(m['cagr'])} | "
                     f"{_num(m['sharpe'])} | {_pct(m['max_drawdown'])} | {_pct(m.get('avg_exposure'), 1)} | "
                     f"{_num(m.get('annual_turnover'))}x | {m.get('n_trades', 0)} | {m.get('n_stops', 0)} |")
    lines += ["", "| excess vs | cost | days | annualised mean excess | 95% CI (date clusters) | "
                  "95% CI (month clusters) |", "|---|---:|---:|---:|---|---|"]
    for label, block in (("SPY", res.vs_spy), ("champion (real)", res.vs_champion)):
        for cm, e in block.items():
            if e.get("mean_daily") is None:
                lines.append(f"| {label} | {cm:g}x | {e.get('n_days', 0)} | - | - | - |")
                continue
            lines.append(f"| {label} | {cm:g}x | {e['n_days']} | {_pct(e['ann_excess'])} | "
                         f"[{_pct(e['ann_ci_low'])}, {_pct(e['ann_ci_high'])}] | "
                         f"[{_pct(e['ann_ci_month_low'])}, {_pct(e['ann_ci_month_high'])}] |")
    viol = res.base.violations
    if viol:
        lines += ["", "chassis interventions: " + ", ".join(f"{k}={v}" for k, v in sorted(viol.items()))]
    for n in res.notes:
        lines.append(f"note: {n}")
    return "\n".join(lines)
