"""Treasury sleeve: idle cash swept into a T-bill ETF (the risk-free baseline).

Holds one T-bill ETF with 100% of the sleeve's capital: BIL (SPDR 1-3 Month T-Bill,
history since 2007) by default, SGOV (iShares 0-3 Month Treasury, since 2020-05) when
BIL has no completed bar as of the decision date. Param `primary` swaps the order.

This is not an edge. It is what unallocated cash is worth when it earns the T-bill
rate instead of 0, and the floor every other sleeve's capital is measured against.
Returns come from dividend-adjusted daily bars (yfinance auto_adjust), so the
monthly T-bill distributions are in the price series; the ETF's expense ratio is
already netted out (BIL ~0.14%/yr, SGOV ~0.09%/yr).

Broker reality: Alpaca PAPER accounts pay no interest on cash and there is no
sweep; a REAL Alpaca account can hold the ETF (or use a cash sweep). So the paper
account's idle cash earning 0 understates what the same allocation earns live.
"""

from __future__ import annotations

from research.portfolio_sim import Chassis
from research.strategy import Strategy


class TreasurySweep(Strategy):
    """Hold a T-bill ETF (BIL, fallback SGOV) with 100% of the treasury sleeve."""

    id = "treasury_sweep"
    horizon = "treasury"
    default_params = {"primary": "BIL", "fallback": "SGOV", "weight": 1.0}
    rebalance = "drift"
    reenter_after_stop = True
    # one position at 100% of the sleeve; no trailing stop or daily loss limit (a T-bill
    # fund has no drawdown worth stopping on; a stop would only churn costs)
    chassis_override = Chassis(max_positions=1, max_weight=1.0, daily_loss_limit=None,
                               trailing_stop=None)

    def target_weights(self, as_of, data):
        sym = tbill_symbol(data, self.params["primary"], self.params["fallback"])
        return {sym: float(self.params["weight"])} if sym else {}


def tbill_symbol(data, primary: str, fallback: str):
    """The first of (primary, fallback) with a bar on the latest completed session, else None."""
    last = data.trading_days(lookback=1)
    if not len(last):
        return None
    for sym in (primary, fallback):
        if sym:
            b = data.bars(sym, lookback=1)
            if len(b) and b.index[-1] == last[-1]:
                return sym
    return None
