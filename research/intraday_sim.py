"""STRICT intraday simulator for the day sleeve (long only, flat every session).

Execution rules (deliberately harsher than research/portfolio_sim.py):
  * No fills on the signal bar. A signal observed on bar m (at its close) can only
    trade from the OPEN of bar m+1. Exit triggers seen on bar m also execute from m+1.
    A minute with no trades has no open, so the order waits for the next printed bar.
  * Fill price = that open +/- (half the assumed spread + latency slippage):
      buy  = open * (1 + spread/2 + slippage)      sell = open * (1 - spread/2 - slippage)
    spread  = per symbol-session estimate: median of (high - low) / close over the
              session's first 5 minutes, floored at 2 bp (see estimate_spread)
    slippage = 1 bp; commission 0. Stress variant: spread x2 and slippage x2.
  * Partial fills: one bar fills at most 10% of that bar's volume. The rest rolls to
    the next bar's open. Unfilled ENTRY shares are cancelled when an exit triggers, at
    the flat time, after the daily loss stop, or at session end. Unfilled EXIT shares
    keep rolling; anything still open at the last bar is force-sold at its close
    (minus costs) and flagged `forced`.
  * Hard flat by 15:55 ET (5 minutes before the session close, so 12:55 on early-close
    days): exits for everything are placed for the open of the 15:55 bar; no entry
    fills at or after it.
  * Sizing: per-trade risk cap 0.25% of the session-start account. shares =
    floor(0.0025 x equity / (est_fill - stop)), est_fill = signal close + cost. Gross
    exposure (open + pending notional) is capped at gross_cap x equity (default 1.0x,
    no intraday leverage). The cap is ex-ante: a gap through the stop can lose more.
  * Daily loss stop: once realized + marked P&L (marks at bar closes) reaches -1% of the
    session-start account, no new entries that session and pending entries are cancelled.
  * Exits: stop when a bar's low <= stop; targets when a bar's high >= target (targets
    in R multiples of the first fill: target_k = entry + k x (entry - stop), each
    closing its fraction of the filled shares); if stop and target touch on the same bar
    the stop wins. All execute at the next bar's open (so a stop can fill well below
    the stop price -- this is a stop-market at next-open, not a resting stop).

Outputs: one row per trade, per-minute equity for each session, and a daily table.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from research.intraday_bars import SessionBars

DEFAULT_TARGETS: Tuple[Tuple[float, float], ...] = ((1.0, 0.5), (2.0, 0.5))


@dataclass(frozen=True)
class CostModel:
    spread_floor_bps: float = 2.0
    slippage_bps: float = 1.0
    spread_window: int = 5
    spread_mult: float = 1.0
    slip_mult: float = 1.0

    def stressed(self, mult: float = 2.0) -> "CostModel":
        return CostModel(self.spread_floor_bps, self.slippage_bps, self.spread_window,
                         self.spread_mult * mult, self.slip_mult * mult)

    def per_side(self, sb: SessionBars) -> float:
        """Fractional cost per side: half the (scaled) spread + (scaled) slippage."""
        spread = estimate_spread(sb, self.spread_window, self.spread_floor_bps)
        return self.spread_mult * spread / 2.0 + self.slip_mult * self.slippage_bps / 1e4


def estimate_spread(sb: SessionBars, window: int = 5, floor_bps: float = 2.0) -> float:
    """Median (high - low) / close over the first `window` minutes, floored at floor_bps.

    A 1-minute bar's range is a PROXY: it includes price movement, so for very liquid
    names it overstates the quoted spread (conservative), while the first minutes are
    the widest of the day (also conservative for later entries)."""
    h, l, c = sb.high[:window], sb.low[:window], sb.close[:window]
    r = (h - l) / c
    r = r[np.isfinite(r)]
    est = float(np.median(r)) if len(r) else 0.0
    return max(est, floor_bps / 1e4)


@dataclass
class SimConfig:
    start_equity: float = 100_000.0
    risk_frac: float = 0.0025
    daily_loss_stop: float = 0.01
    max_volume_frac: float = 0.10
    gross_cap: float = 1.0
    targets: Tuple[Tuple[float, float], ...] = DEFAULT_TARGETS
    cost: CostModel = field(default_factory=CostModel)


@dataclass
class _Pos:
    symbol: str
    sb: SessionBars
    signal_idx: int
    stop: float
    cost: float
    intended: int
    entry_pending: int
    ref_px: float
    qty: int = 0
    filled: int = 0
    entry_cash: float = 0.0
    exit_cash: float = 0.0
    exit_qty: int = 0
    exit_pending: int = 0
    first_fill_idx: Optional[int] = None
    first_fill_px: Optional[float] = None
    last_exit_idx: Optional[int] = None
    reasons: List[str] = field(default_factory=list)
    targets_hit: int = 0
    closing_all: bool = False
    forced: bool = False
    last_close: float = float("nan")

    @property
    def avg_entry(self) -> float:
        return self.entry_cash / self.filled if self.filled else float("nan")

    def committed(self) -> float:
        return (self.qty + self.entry_pending) * self.ref_px


@dataclass
class SimResult:
    trades: pd.DataFrame
    equity: pd.DataFrame        # date, minute, equity (marked at bar closes)
    daily: pd.DataFrame         # date, start_equity, end_equity, pnl, ret, n_trades, loss_stop_hit
    skipped: Dict[str, int]
    config: SimConfig

    def summary(self) -> Dict:
        t, d = self.trades, self.daily
        eq = d["end_equity"].values if len(d) else np.array([self.config.start_equity])
        peak = np.maximum.accumulate(np.r_[self.config.start_equity, eq])
        dd = float(np.min(np.r_[self.config.start_equity, eq] / peak - 1.0))
        return {"n_trades": int(len(t)),
                "mean_ret": float(t["ret"].mean()) if len(t) else None,
                "win_rate": float((t["pnl"] > 0).mean()) if len(t) else None,
                "mean_r": float(t["r_mult"].mean()) if len(t) else None,
                "total_return": float(eq[-1] / self.config.start_equity - 1.0),
                "max_drawdown": dd,
                "sessions": int(len(d)),
                "sessions_traded": int((d["n_trades"] > 0).sum()) if len(d) else 0,
                "loss_stop_days": int(d["loss_stop_hit"].sum()) if len(d) else 0,
                "forced_exits": int(t["forced"].sum()) if len(t) else 0,
                "partial_entries": int((t["filled"] < t["intended"]).sum()) if len(t) else 0,
                "skipped": dict(self.skipped)}


def _next_valid(sb: SessionBars, i: int) -> bool:
    return 0 <= i < sb.n and np.isfinite(sb.open[i]) and sb.volume[i] > 0


def simulate(signals: pd.DataFrame, paths: Dict[Tuple[str, str], SessionBars],
             config: Optional[SimConfig] = None, sessions: Optional[Sequence[str]] = None) -> SimResult:
    """signals: [symbol, date, signal_idx, stop]; paths[(symbol, date)] -> SessionBars.

    Sessions are run in date order with compounding equity; within a session all
    symbols share one minute clock (risk cap, gross cap and the daily stop interact)."""
    cfg = config or SimConfig()
    equity = cfg.start_equity
    skipped = {"stop_above_entry": 0, "too_small": 0, "gross_cap": 0, "after_loss_stop": 0,
               "too_late": 0, "no_fill": 0}
    trades, eq_rows, daily = [], [], []
    sig = signals.sort_values(["date", "signal_idx", "symbol"]) if len(signals) else signals
    by_date = {d: g for d, g in sig.groupby("date", sort=True)} if len(sig) else {}
    all_dates = sorted(set(by_date) | set(sessions or []))
    tgts = list(cfg.targets)
    for d in all_dates:
        g = by_date.get(d)
        eq0 = equity
        if g is None or not len(g):
            daily.append({"date": d, "start_equity": eq0, "end_equity": eq0, "pnl": 0.0, "ret": 0.0,
                          "n_trades": 0, "loss_stop_hit": False})
            continue
        sbs = [paths[(s, d)] for s in g["symbol"]]
        n = max(sb.n for sb in sbs)
        flat = min(sb.flat_idx for sb in sbs)
        by_idx: Dict[int, List[Tuple[str, float, SessionBars]]] = {}
        for (s, i, stp), sb in zip(g[["symbol", "signal_idx", "stop"]].itertuples(index=False), sbs):
            by_idx.setdefault(int(i), []).append((s, float(stp), sb))
        positions: List[_Pos] = []
        realized = 0.0
        stopped = False
        first = min(by_idx)
        eq_rows.extend((d, m, eq0) for m in range(min(first, n)))   # nothing can happen before
        for m in range(first, n):
            # 1) orders placed at the end of m-1 execute at the open of m
            for p in positions:
                sb = p.sb
                if not _next_valid(sb, m):
                    continue
                cap = int(math.floor(cfg.max_volume_frac * sb.volume[m]))
                if p.exit_pending > 0:
                    q = min(p.exit_pending, cap)
                    if q > 0:
                        px = sb.open[m] * (1 - p.cost)
                        p.exit_cash += q * px
                        realized += q * (px - p.avg_entry)
                        p.qty -= q
                        p.exit_qty += q
                        p.exit_pending -= q
                        p.last_exit_idx = m
                elif p.entry_pending > 0 and m < flat and not stopped:
                    q = min(p.entry_pending, cap)
                    if q > 0:
                        px = sb.open[m] * (1 + p.cost)
                        if p.first_fill_idx is None:
                            p.first_fill_idx, p.first_fill_px = m, px
                        p.entry_cash += q * px
                        p.filled += q
                        p.qty += q
                        p.entry_pending -= q
            # 2) bar m prints: triggers on its high/low; flat rule; marks at its close
            unreal = 0.0
            for p in positions:
                sb = p.sb
                if np.isfinite(sb.close[m]):
                    p.last_close = sb.close[m]
                if p.qty > 0 and not p.closing_all and np.isfinite(sb.low[m]):
                    if sb.low[m] <= p.stop:
                        p.reasons.append("stop")
                        p.closing_all, p.entry_pending, p.exit_pending = True, 0, p.qty
                    else:
                        R = p.first_fill_px - p.stop
                        while p.targets_hit < len(tgts) and R > 0 and \
                                sb.high[m] >= p.first_fill_px + tgts[p.targets_hit][0] * R:
                            k = p.targets_hit
                            p.targets_hit += 1
                            p.entry_pending = 0
                            p.reasons.append(f"target{tgts[k][0]:g}R")
                            if p.targets_hit == len(tgts):
                                p.closing_all, p.exit_pending = True, p.qty
                            else:
                                share = int(round(tgts[k][1] * p.filled))
                                p.exit_pending = min(p.qty, p.exit_pending + share)
                if m + 1 >= flat and not p.closing_all and (p.qty > 0 or p.entry_pending > 0):
                    if p.qty > 0:
                        p.reasons.append("flat")
                    p.closing_all, p.entry_pending, p.exit_pending = True, 0, p.qty
                if p.qty > 0 and np.isfinite(p.last_close):
                    unreal += p.qty * (p.last_close * (1 - p.cost) - p.avg_entry)
            pnl = realized + unreal
            if not stopped and pnl <= -cfg.daily_loss_stop * eq0:
                stopped = True
                for p in positions:
                    p.entry_pending = 0
            # 3) signals seen at the close of m become orders for m+1
            for s, stp, sb in by_idx.get(m, []):
                if stopped:
                    skipped["after_loss_stop"] += 1
                    continue
                if m + 1 >= flat:
                    skipped["too_late"] += 1
                    continue
                cost = cfg.cost.per_side(sb)
                ref = sb.close[m] * (1 + cost)
                if not np.isfinite(ref) or ref - stp <= 0:
                    skipped["stop_above_entry"] += 1
                    continue
                qty = int(math.floor(cfg.risk_frac * eq0 / (ref - stp)))
                room = cfg.gross_cap * eq0 - sum(p.committed() for p in positions)
                q2 = min(qty, int(math.floor(max(room, 0.0) / ref)))
                if q2 < 1:
                    skipped["gross_cap" if qty >= 1 else "too_small"] += 1
                    continue
                positions.append(_Pos(s, sb, m, stp, cost, q2, q2, ref))
            eq_rows.append((d, m, eq0 + pnl))
        # 4) session end: anything still held is force-sold at its last close
        for p in positions:
            if p.qty > 0:
                px = p.last_close * (1 - p.cost)
                p.exit_cash += p.qty * px
                realized += p.qty * (px - p.avg_entry)
                p.exit_qty += p.qty
                p.qty, p.forced, p.last_exit_idx = 0, True, n - 1
                if not p.reasons:
                    p.reasons.append("flat")
            if p.filled == 0:
                skipped["no_fill"] += 1
                continue
            avg_in, avg_out = p.avg_entry, p.exit_cash / p.exit_qty
            pnl_t = p.exit_cash - p.entry_cash
            trades.append({"symbol": p.symbol, "date": d, "signal_idx": p.signal_idx,
                           "entry_idx": p.first_fill_idx, "exit_idx": p.last_exit_idx,
                           "intended": p.intended, "filled": p.filled, "avg_entry": avg_in,
                           "avg_exit": avg_out, "stop": p.stop, "cost_per_side": p.cost,
                           "pnl": pnl_t, "ret": avg_out / avg_in - 1.0,
                           "r_mult": (avg_out - avg_in) / (avg_in - p.stop) if avg_in > p.stop else float("nan"),
                           "risk_frac": p.filled * max(avg_in - p.stop, 0.0) / eq0,
                           "exit_reason": "+".join(p.reasons), "forced": p.forced})
        equity = eq0 + realized
        daily.append({"date": d, "start_equity": eq0, "end_equity": equity, "pnl": realized,
                      "ret": realized / eq0, "n_trades": sum(1 for p in positions if p.filled),
                      "loss_stop_hit": stopped})
    tcols = ["symbol", "date", "signal_idx", "entry_idx", "exit_idx", "intended", "filled", "avg_entry",
             "avg_exit", "stop", "cost_per_side", "pnl", "ret", "r_mult", "risk_frac", "exit_reason", "forced"]
    return SimResult(trades=pd.DataFrame(trades, columns=tcols),
                     equity=pd.DataFrame(eq_rows, columns=["date", "minute", "equity"]),
                     daily=pd.DataFrame(daily, columns=["date", "start_equity", "end_equity", "pnl", "ret",
                                                        "n_trades", "loss_stop_hit"]),
                     skipped=skipped, config=cfg)
