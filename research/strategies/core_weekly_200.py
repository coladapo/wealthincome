"""Long / core sleeve, frozen: SPY while above its 200-day SMA (re-checked weekly), else T-bills.

Hypothesis `core_weekly_200`. The rule is fixed -- there is nothing to tune:
  * Re-check day = the FIRST completed session of each ISO week (Mon..Sun). Only
    completed sessions are visible (research.asof), so the first session of the week
    is knowable at its close; nothing needs tomorrow's calendar.
  * On the most recent re-check day d <= as_of: risk-on iff SPY's close at d is above
    the simple average of its last 200 closes up to and including d (never later).
  * risk-on  -> 100% of the sleeve in SPY;
    risk-off -> 100% in BIL (SGOV only if BIL has no bar on the latest session).
  * Between re-check days the target is frozen; the simulator trades only when the
    target changes, at the next session's open.
  * Fewer than 200 SPY closes up to d -> risk-off (no signal yet).

The only parameter is `extra_slippage_bps`, added to the chassis's 10 bp/side for the
pre-registered cost sensitivity (primary = 0, sensitivity = 10). It is not a rule knob.

Provenance: this rule was first seen as a sensitivity variant of hypothesis
`sleeve-trend-filtered-core` (runs 43-46), i.e. it was chosen after looking at results.
Its backtest is therefore not independent evidence; its shadow record is.

Logic is self-contained here on purpose: the frozen version hashes only this file.
"""

from __future__ import annotations

from research.portfolio_sim import Chassis
from research.strategy import Strategy

SMA_DAYS = 200
RISK_ON = "SPY"
RISK_OFF = "BIL"
RISK_OFF_FALLBACK = "SGOV"
BASE_COST_BPS = 10.0


class CoreWeekly200(Strategy):
    """SPY above its 200-day SMA (re-checked on the first session of each ISO week), else BIL; 100% of the sleeve."""

    id = "core_weekly_200"
    horizon = "long"
    default_params = {"extra_slippage_bps": 0.0}
    rebalance = "drift"
    reenter_after_stop = True
    chassis_override = Chassis(max_positions=1, max_weight=1.0, daily_loss_limit=0.05,
                               trailing_stop=None, cost_bps=BASE_COST_BPS)

    def __init__(self, params=None):
        super().__init__(params)
        extra = float(self.params["extra_slippage_bps"])
        if extra < 0:
            raise ValueError("extra_slippage_bps must be >= 0")
        if extra:
            # instance-level override: same sleeve policy, higher cost (still the live sleeve chassis)
            self.chassis_override = Chassis(max_positions=1, max_weight=1.0, daily_loss_limit=0.05,
                                            trailing_stop=None, cost_bps=BASE_COST_BPS + extra)

    @staticmethod
    def last_check_day(data):
        """The first completed session of the current ISO week, or None."""
        td = data.trading_days()
        if not len(td):
            return None
        weeks = td.to_period("W-SUN")
        return td[weeks == weeks[-1]][0]

    def risk_on(self, data) -> bool:
        d = self.last_check_day(data)
        if d is None:
            return False
        closes = data.bars(RISK_ON, lookback=SMA_DAYS, until=d)["close"]
        if len(closes) < SMA_DAYS:
            return False
        return float(closes.iloc[-1]) > float(closes.mean())

    def target_weights(self, as_of, data):
        if self.risk_on(data):
            return {RISK_ON: 1.0} if data.close(RISK_ON) is not None else {}
        last = data.trading_days(lookback=1)
        for sym in (RISK_OFF, RISK_OFF_FALLBACK):
            if len(last):
                b = data.bars(sym, lookback=1)
                if len(b) and b.index[-1] == last[-1]:
                    return {sym: 1.0}
        return {}
