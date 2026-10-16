"""Volatility-managed SPY (15% vol target, weekly re-check) -- drafted by a model in weekly research session 2026-42 (claude-opus-5-5); frozen as written.

Hypothesis (pre-registered): research-2026-42-vol_managed_spy
Sleeve: long

Why mispriced: Stock-market volatility is persistent (high-vol weeks tend to follow high-vol weeks), but expected returns do not rise in proportion when volatility rises. So the reward per unit of risk is worst right after volatility spikes (Moreira & Muir 2017; Fleming, Kirby & Ostdiek 2001). Holding less SPY when realised volatility is high and more when it is calm should give a better Sharpe and a shallower drawdown than holding a constant amount, and that is exactly the long sleeve's floor (Sharpe >= SPY, drawdown shallower, 2022 not worse). It differs from the rejected trend filters in two ways. It looks at realised volatility, not at price relative to an average. And it sizes the position gradually in 10% steps instead of switching all-in/all-out, which is what caused the monthly 200-day rule's whipsaw drawdown. SPY is an ETF, so missing ex-members of the S&P 500 do not bias the result.

Who is on the other side: This is risk transfer, not mispricing someone loses money on. When volatility spikes, the risk we shed goes to buyers who hold a constant amount of stock or are slow to react: balanced funds and target-date funds that rebalance back into stocks after a drop, buy-and-hold investors who ignore volatility, and capital that only arrives slowly after leveraged and risk-limited players are forced to cut. They earn less return per unit of risk in those weeks, and accept that because their mandates fix their equity share, or because they do not watch volatility week to week. The known weakness: after a sudden crash followed by a quick V-shaped rebound (for example April 2025) the 21-day volatility reading lags, so we are underweight for part of the rebound. That is the real risk to the Sharpe test.

Rule: Re-check day = the first completed session of each ISO week (Mon..Sun). On the latest re-check day d <= as_of: take SPY's last 22 closes up to and including d, compute the 21 daily log returns, and realised vol rv = sample stdev (ddof=1) * sqrt(252). Target weight w = min(1.0, 0.15 / rv). Round w to the nearest 0.1 (floor(w/0.1 + 0.5) * 0.1) and clip it to [0, 1.0]. Hold SPY at w of the sleeve, with the rest in uninvested cash: the sleeve's one-position policy rules out BIL alongside SPY. If w rounds to 0, there are fewer than 22 closes, or SPY has no latest bar, hold nothing. The target changes only on re-check days and only by steps of 0.1. Trades happen at the next open in drift mode. Chassis: 1 position, at most 100%, 5% daily loss limit, no trailing stop, 10 bp/side (stress 20 bp). Params: symbol=SPY, vol_days=21, target_vol=0.15, max_weight=1.0, step=0.1.

Pre-registered bar: At both 1x and 2x cost, over the full out-of-sample walk-forward, all four must hold. (1) Sharpe at least SPY buy-and-hold's over the same dates. (2) Max drawdown shallower than SPY's. (3) Calendar-2022 return no worse than SPY's 2022. (4) Max drawdown no worse than -20%. Judged on this primary version only; any other variants are sensitivity checks and do not count as a pass. I expect (2)-(4) to pass. (1) is roughly a coin flip because of the 2025 V-shaped rebound.
Bar criteria (merged with the sleeve floor): {"sharpe_at_least_spy": true, "max_drawdown_shallower_than_spy": true, "not_worse_than_spy_2022": true, "max_drawdown_floor": -0.2}
Trial budget: 2

"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from research.portfolio_sim import Chassis
from research.strategy import Strategy


class VolManagedSpy(Strategy):
    """SPY sized to a 15% annualised volatility target (21-day realised vol, re-checked weekly); the rest stays in cash."""

    id = "vol_managed_spy"
    horizon = "long"
    default_params = {"symbol": "SPY", "vol_days": 21, "target_vol": 0.15, "max_weight": 1.0, "step": 0.1}
    rebalance = "drift"
    reenter_after_stop = True
    chassis_override = Chassis(max_positions=1, max_weight=1.0, daily_loss_limit=0.05,
                               trailing_stop=None)

    def __init__(self, params=None):
        super().__init__(params)
        if int(self.params["vol_days"]) < 5:
            raise ValueError("vol_days must be >= 5")
        if not (0.0 < float(self.params["step"]) <= 1.0):
            raise ValueError("step must be in (0, 1]")
        if not (0.0 < float(self.params["max_weight"]) <= 1.0):
            raise ValueError("max_weight must be in (0, 1]")
        self._cache_day = None
        self._cache_w = {}

    def _check_day(self, data):
        """First completed session of the current ISO week (Mon..Sun), or None."""
        td = data.trading_days(lookback=10)
        if not len(td):
            return None
        periods = td.to_period("W")
        return td[periods == periods[-1]][0]

    def _sized(self, data, d):
        sym = self.params["symbol"]
        n = int(self.params["vol_days"])
        closes = data.bars(sym, lookback=n + 1, until=d)["close"]
        if len(closes) < n + 1:
            return {}
        c = np.asarray(closes, dtype=float)
        if not np.all(np.isfinite(c)) or np.any(c <= 0):
            return {}
        r = np.diff(np.log(c))
        rv = float(np.std(r, ddof=1)) * math.sqrt(252.0)
        if not math.isfinite(rv) or rv <= 0:
            return {}
        cap = float(self.params["max_weight"])
        step = float(self.params["step"])
        w = min(cap, float(self.params["target_vol"]) / rv)
        w = math.floor(w / step + 0.5) * step
        w = max(0.0, min(cap, w))
        if w < step / 2.0:
            return {}
        return {sym: round(w, 6)}

    def target_weights(self, as_of, data):
        d = self._check_day(data)
        if d is None:
            return {}
        if self._cache_day != d:
            self._cache_w = self._sized(data, d)
            self._cache_day = d
        if not self._cache_w:
            return {}
        if data.close(self.params["symbol"]) is None:
            return {}
        return dict(self._cache_w)
