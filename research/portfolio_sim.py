"""Daily portfolio simulator under the live chassis caps. Deterministic.

Timeline for each trading day t (calendar = SPY's sessions in the bar store):

  1. OPEN of t   execute the targets computed at the close of t-1 (next-open
                 execution: a signal never trades at the price it was computed from).
                 Sells first, then buys. Fractional shares. Costs per side on the
                 traded notional (cost_bps x cost_mult; 10 bp live, 2x stress = 20 bp).
  2. CLOSE of t  mark to close; update each position's high-water mark (closes since
                 entry, entry price included); TRAILING STOP: if close <= hwm x (1 - 12%)
                 the position is sold AT THAT CLOSE (the stop is treated as a stop on
                 close, not intraday).
  3. CLOSE of t  ask the strategy for target weights with AsOfData(t) (completed
                 sessions <= t only) and queue them for the open of t+1.

Chassis caps (live values; see Chassis): max 8 open positions, max 8% of equity
per position at entry/increase (targets are clipped), long only (negative targets
are dropped and counted), never more than 100% invested, no margin (buys are
scaled to available cash). DAILY LOSS LIMIT: if equity marked at t's open is down
>= 5% from t-1's close, no new positions or increases are opened that day; exits
and reductions still execute. (With daily bars the only intraday information is
the opening gap; an intraday breach after the open is not modelled.)

Rebalance modes (Strategy.rebalance):
  drift   a name trades only when its target CHANGES (entry / exit / resize) --
          the live chassis behaviour: positions are not trimmed as they drift above
          8%. An entry rejected by a cap or the loss limit is dropped, not retried
          (as a live order rejection would be). A name with a bar missing that day
          is retried at the next open.
  target  every day each name is moved back to its target when off by more than
          `rebalance_band` (absolute weight); entries/exits always trade.

After a trailing stop, a name is re-bought at the next open only if
Strategy.reenter_after_stop; otherwise it is blocked until its target goes to 0.

Outputs: SimResult.daily (equity, cash, invested, exposure, positions, traded
notional, turnover = traded notional / close equity, costs, daily return,
loss_limit_hit) and SimResult.trades. SimResult.state can seed the next call
(walk-forward stitching).
"""

from __future__ import annotations

import copy
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

import pandas as pd

from research.asof import AsOfData, MarketData


@dataclass(frozen=True)
class Chassis:
    max_positions: int = 8
    max_weight: float = 0.08
    daily_loss_limit: Optional[float] = 0.05
    trailing_stop: Optional[float] = 0.12
    cost_bps: float = 10.0

    def label(self) -> str:
        dl = "none" if self.daily_loss_limit is None else f"{self.daily_loss_limit:.0%}"
        ts = "none" if self.trailing_stop is None else f"{self.trailing_stop:.0%}"
        return (f"max {self.max_positions} pos, {self.max_weight:.0%}/pos, daily loss {dl}, "
                f"trailing stop {ts}, {self.cost_bps:g} bp/side")


LIVE_CHASSIS = Chassis()


@dataclass
class Position:
    shares: float
    hwm: float
    entry_date: str
    last_price: float


@dataclass
class SimState:
    cash: float
    last_equity: float
    positions: Dict[str, Position] = field(default_factory=dict)
    prev_targets: Dict[str, float] = field(default_factory=dict)
    pending: Optional[Dict[str, float]] = None
    stopped: Dict[str, str] = field(default_factory=dict)

    def copy(self) -> "SimState":
        return copy.deepcopy(self)

    def equity(self) -> float:
        return self.cash + sum(p.shares * p.last_price for p in self.positions.values())


@dataclass
class SimResult:
    daily: pd.DataFrame
    trades: pd.DataFrame
    state: SimState
    violations: Dict[str, int]
    chassis: Chassis
    cost_mult: float


TRADE_COLS = ["date", "symbol", "side", "shares", "price", "notional", "cost", "reason"]
EPS = 1e-9


def sanitize_targets(raw: Optional[Dict[str, float]], chassis: Chassis,
                     violations: Counter, held: Optional[set] = None) -> Dict[str, float]:
    """Apply the long-only / per-name cap / name-count / <=100% rules to a strategy's output.

    When more than max_positions names are requested, names already HELD keep their slots
    (the chassis never sells a position to make room); the rest go by weight, then symbol.
    """
    w: Dict[str, float] = {}
    for s, v in (raw or {}).items():
        try:
            v = float(v)
        except (TypeError, ValueError):
            violations["non_numeric_weight"] += 1
            continue
        if not math.isfinite(v):
            violations["non_numeric_weight"] += 1
            continue
        if v < 0:
            violations["short_requested"] += 1
            continue
        if v <= EPS:
            continue
        if v > chassis.max_weight + EPS:
            violations["over_cap_clipped"] += 1
            v = chassis.max_weight
        w[str(s)] = v
    if len(w) > chassis.max_positions:
        violations["too_many_names"] += 1
        held = held or set()
        keep = sorted(w.items(), key=lambda kv: (kv[0] not in held, -kv[1], kv[0]))[:chassis.max_positions]
        w = dict(keep)
    tot = sum(w.values())
    if tot > 1.0 + EPS:
        violations["over_invested_scaled"] += 1
        w = {s: v / tot for s, v in w.items()}
    return dict(sorted(w.items()))


def _px(market: MarketData, symbol: str, t: pd.Timestamp, col: str) -> Optional[float]:
    df = market.frame(symbol)
    try:
        v = df.at[t, col]
    except KeyError:
        return None
    v = float(v)
    return v if math.isfinite(v) and v > 0 else None


def simulate(strategy, start, end, market: MarketData, chassis: Optional[Chassis] = None,
             cost_mult: float = 1.0, initial_cash: float = 100_000.0,
             state: Optional[SimState] = None, rebalance_band: float = 0.01,
             now=None) -> SimResult:
    """Run `strategy` over the trading days in [start, end]. Pass `state` to continue a run."""
    chassis = chassis or getattr(strategy, "chassis", None) or LIVE_CHASSIS
    mode = getattr(strategy, "rebalance", "drift")
    reenter = getattr(strategy, "reenter_after_stop", True)
    c_rate = chassis.cost_bps * cost_mult / 1e4
    st = state.copy() if state is not None else SimState(cash=float(initial_cash),
                                                         last_equity=float(initial_cash))
    violations: Counter = Counter()
    tdays = market.trading_days()
    days = tdays[(tdays >= pd.Timestamp(start)) & (tdays <= pd.Timestamp(end))]
    trades: List[tuple] = []
    rows: List[dict] = []

    for t in days:
        tstr = t.strftime("%Y-%m-%d")
        traded = 0.0
        costs = 0.0
        loss_hit = False

        # ---------------------------------------------------------- 1. next-open execution
        if st.pending is not None:
            P = st.pending
            opens = {}
            for s in set(st.positions) | set(P):
                opens[s] = _px(market, s, t, "open")
            eq_open = st.cash + sum(p.shares * (opens.get(s) or p.last_price)
                                    for s, p in st.positions.items())
            if chassis.daily_loss_limit is not None and st.last_equity > 0:
                loss_hit = eq_open / st.last_equity - 1.0 <= -chassis.daily_loss_limit + EPS
            sells, buys = [], []
            for s in sorted(set(st.positions) | set(P)):
                tgt = P.get(s, 0.0)
                prev = st.prev_targets.get(s, 0.0)
                held = s in st.positions
                px = opens.get(s)
                cur_w = (st.positions[s].shares * px / eq_open) if (held and px and eq_open > 0) else 0.0
                act = None
                if held and tgt <= EPS:
                    act = ("exit", 0.0)
                elif held:
                    if mode == "drift":
                        if abs(tgt - prev) > EPS:
                            act = ("resize", tgt)
                    elif abs(tgt - cur_w) > rebalance_band:
                        act = ("resize", tgt)
                elif tgt > EPS:
                    was_stopped = s in st.stopped
                    if was_stopped and not reenter:
                        act = None
                    elif mode == "drift":
                        if abs(tgt - prev) > EPS or (was_stopped and reenter):
                            act = ("enter", tgt)
                    else:
                        act = ("enter", tgt)
                if act is None:
                    if tgt <= EPS or (held and mode == "drift"):
                        st.prev_targets[s] = tgt
                    continue
                if px is None:
                    continue  # no bar today: retry at the next open (prev target unchanged)
                kind, w = act
                if kind == "exit" or (kind == "resize" and w < cur_w - EPS):
                    sells.append((s, kind, w, px))
                elif w > cur_w + EPS:
                    buys.append((s, kind, w, px, cur_w))
                else:
                    st.prev_targets[s] = tgt

            for s, kind, w, px in sells:
                pos = st.positions[s]
                sh = pos.shares if kind == "exit" else pos.shares - w * eq_open / px
                sh = min(max(sh, 0.0), pos.shares)
                notional = sh * px
                cost = notional * c_rate
                st.cash += notional - cost
                pos.shares -= sh
                traded += notional
                costs += cost
                trades.append((tstr, s, "sell", sh, px, notional, cost,
                               "target_exit" if kind == "exit" else "target_reduce"))
                if pos.shares <= EPS:
                    del st.positions[s]
                st.prev_targets[s] = P.get(s, 0.0)

            buys.sort(key=lambda b: (-b[2], b[0]))
            for s, kind, w, px, cur_w in buys:
                st.prev_targets[s] = P.get(s, 0.0)   # rejections are final (drift mode)
                if loss_hit:
                    violations["loss_limit_blocked_buy"] += 1
                    continue
                if kind == "enter" and len(st.positions) >= chassis.max_positions:
                    violations["max_positions_blocked_entry"] += 1
                    continue
                w = min(w, chassis.max_weight)
                notional = (w - cur_w) * eq_open
                if notional * (1 + c_rate) > st.cash:
                    notional = max(st.cash / (1 + c_rate), 0.0)
                    violations["cash_limited_buy"] += 1
                if notional < 1.0:
                    continue
                sh = notional / px
                cost = notional * c_rate
                st.cash -= notional + cost
                if s in st.positions:
                    st.positions[s].shares += sh
                else:
                    st.positions[s] = Position(shares=sh, hwm=px, entry_date=tstr, last_price=px)
                    st.stopped.pop(s, None)
                traded += notional
                costs += cost
                trades.append((tstr, s, "buy", sh, px, notional, cost,
                               "target_enter" if kind == "enter" else "target_increase"))
            st.pending = None

        # ---------------------------------------------------------- 2. close: mark + trailing stop
        for s in sorted(st.positions):
            pos = st.positions[s]
            c = _px(market, s, t, "close")
            if c is None:
                continue
            pos.last_price = c
            pos.hwm = max(pos.hwm, c)
            if chassis.trailing_stop is not None and c <= pos.hwm * (1 - chassis.trailing_stop) + EPS:
                notional = pos.shares * c
                cost = notional * c_rate
                st.cash += notional - cost
                traded += notional
                costs += cost
                trades.append((tstr, s, "sell", pos.shares, c, notional, cost, "trailing_stop"))
                del st.positions[s]
                st.stopped[s] = tstr
        invested = sum(p.shares * p.last_price for p in st.positions.values())
        eq = st.cash + invested
        ret = eq / st.last_equity - 1.0 if st.last_equity > 0 else 0.0
        st.last_equity = eq
        rows.append({"date": t, "equity": eq, "cash": st.cash, "invested": invested,
                     "exposure": invested / eq if eq > 0 else 0.0, "n_positions": len(st.positions),
                     "traded_notional": traded, "turnover": traded / eq if eq > 0 else 0.0,
                     "costs": costs, "ret": ret, "loss_limit_hit": bool(loss_hit)})

        # ---------------------------------------------------------- 3. targets for the next open
        data = AsOfData(t.date(), market, after_close=True, now=now)
        P = sanitize_targets(strategy.target_weights(t.date(), data), chassis, violations,
                             held=set(st.positions))
        for s in list(st.stopped):
            if P.get(s, 0.0) <= EPS:
                del st.stopped[s]
        st.pending = P

    daily = pd.DataFrame(rows).set_index("date") if rows else pd.DataFrame(
        columns=["equity", "cash", "invested", "exposure", "n_positions", "traded_notional",
                 "turnover", "costs", "ret", "loss_limit_hit"])
    return SimResult(daily=daily, trades=pd.DataFrame(trades, columns=TRADE_COLS), state=st,
                     violations=dict(violations), chassis=chassis, cost_mult=cost_mult)


def summarize(daily: pd.DataFrame, trades: Optional[pd.DataFrame] = None) -> Dict:
    """Whole-account stats of a daily equity frame (needs columns equity, ret, exposure, turnover)."""
    if daily is None or len(daily) == 0:
        return {"days": 0}
    eq = daily["equity"].astype(float)
    r = daily["ret"].astype(float).iloc[1:] if len(daily) > 1 else daily["ret"].astype(float)
    total = float(eq.iloc[-1] / eq.iloc[0] - 1.0) if len(eq) > 1 else 0.0
    years = max((len(eq) - 1) / 252.0, 1e-9)
    sd = float(r.std(ddof=1)) if len(r) > 1 else float("nan")
    dd = float((eq / eq.cummax() - 1.0).min())
    out = {
        "days": int(len(eq)),
        "start": daily.index[0].strftime("%Y-%m-%d"),
        "end": daily.index[-1].strftime("%Y-%m-%d"),
        "total_return": total,
        "cagr": float((1 + total) ** (1 / years) - 1) if total > -1 else -1.0,
        "ann_vol": sd * math.sqrt(252) if sd == sd else float("nan"),
        "sharpe": float(r.mean() / sd * math.sqrt(252)) if sd and sd == sd and sd > 0 else float("nan"),
        "max_drawdown": dd,
    }
    if "exposure" in daily:
        out["avg_exposure"] = float(daily["exposure"].mean())
    if "n_positions" in daily:
        out["avg_positions"] = float(daily["n_positions"].astype(float).mean())
    if "turnover" in daily:
        out["annual_turnover"] = float(daily["turnover"].sum() / years)
    if "costs" in daily:
        out["total_costs"] = float(daily["costs"].sum())
    if trades is not None:
        out["n_trades"] = int(len(trades))
        out["n_stops"] = int((trades["reason"] == "trailing_stop").sum()) if len(trades) else 0
    return out
