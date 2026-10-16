"""Long / core sleeve: SPY while it is above its long moving average, else T-bills.

Rule (as-of, no look-ahead):
  * Re-check days are the FIRST completed session of each calendar month
    (recheck="monthly") or of each ISO week (recheck="weekly"). Only completed
    sessions are visible (research.asof), so "first session of the period" is
    knowable at its close; "last session of the month" would need tomorrow's
    calendar and is deliberately not used.
  * On the most recent re-check day d <= as_of: risk-on iff SPY's close at d is
    above the simple average of its last `sma_days` closes up to and including d.
  * risk-on  -> 100% of the sleeve in SPY;
    risk-off -> 100% in BIL (SGOV if BIL has no bar on the latest session).
  * Between re-check days the target does not change (limits whipsaw): the
    simulator only trades when the target changes, at the next session's open.
  * Fewer than `sma_days` SPY closes -> risk-off (no signal yet).

Why: a long trend filter is a drawdown control, not a return enhancer. In strong
bull periods it usually LOWERS total return (late re-entry after dips, T-bill
yield instead of equity return while out); its case is cutting the deep, slow
bear-market drawdowns (2008, 2022). It is judged on that trade-off.

Chassis: the core sleeve's policy is one index position at 100% of the sleeve
(chassis_override). No trailing stop -- the trend filter is the exit; the 5% daily
loss limit is kept (it only blocks buys after a >= 5% opening gap down).
"""

from __future__ import annotations

from research.portfolio_sim import Chassis
from research.strategy import Strategy


class TrendFilteredCore(Strategy):
    """SPY above its N-day SMA (re-checked monthly/weekly), else T-bills; 100% of the sleeve."""

    id = "trend_filtered_core"
    horizon = "long"
    default_params = {"sma_days": 200, "recheck": "monthly", "risk_on": "SPY",
                      "risk_off": "BIL", "risk_off_fallback": "SGOV", "weight": 1.0}
    rebalance = "drift"
    reenter_after_stop = True
    chassis_override = Chassis(max_positions=1, max_weight=1.0, daily_loss_limit=0.05,
                               trailing_stop=None)

    def __init__(self, params=None):
        super().__init__(params)
        if self.params["recheck"] not in ("monthly", "weekly"):
            raise ValueError("recheck must be 'monthly' or 'weekly'")
        if int(self.params["sma_days"]) < 2:
            raise ValueError("sma_days must be >= 2")

    def last_check_day(self, data):
        """The first completed session of the current month / ISO week, or None."""
        td = data.trading_days()
        if not len(td):
            return None
        freq = "M" if self.params["recheck"] == "monthly" else "W"
        periods = td.to_period(freq)
        return td[periods == periods[-1]][0]

    def risk_on(self, data) -> bool:
        d = self.last_check_day(data)
        if d is None:
            return False
        n = int(self.params["sma_days"])
        closes = data.bars(self.params["risk_on"], lookback=n, until=d)["close"]
        if len(closes) < n:
            return False
        return float(closes.iloc[-1]) > float(closes.mean())

    def target_weights(self, as_of, data):
        w = float(self.params["weight"])
        if self.risk_on(data):
            return {self.params["risk_on"]: w} if data.close(self.params["risk_on"]) is not None else {}
        last = data.trading_days(lookback=1)
        for sym in (self.params["risk_off"], self.params["risk_off_fallback"]):
            if sym and len(last):
                b = data.bars(sym, lookback=1)
                if len(b) and b.index[-1] == last[-1]:
                    return {sym: w}
        return {}
