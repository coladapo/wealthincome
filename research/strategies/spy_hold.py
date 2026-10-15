"""Reference strategy: SPY buy-and-hold, under the live chassis and with the caps lifted.

SpyHoldCapped     SPY in all 8 chassis slots at 8% each = 64% invested, the most the
                  live caps allow. Each slot ("SPY#1".."SPY#8") is its own position,
                  so the 12% trailing stop and the 5% daily loss limit apply exactly
                  as they would to 8 picks. This is what "buy the index" is worth
                  INSIDE the chassis -- the honest floor every challenger must beat.
SpyHoldUncapped   REFERENCE ONLY (never promotable): one 100% SPY position. Param
                  `chassis_rules` keeps (True) or drops (False) the trailing stop and
                  daily loss limit, so the cost of the 8% cap and the cost of the stop
                  can be separated.
"""

from __future__ import annotations

from research.portfolio_sim import Chassis
from research.strategy import Strategy


class SpyHoldCapped(Strategy):
    """SPY buy-and-hold in every chassis slot (8 x 8% = 64% invested)."""

    id = "spy_hold_capped"
    default_params = {"slots": 8, "weight": 0.08}
    rebalance = "drift"
    reenter_after_stop = True

    def target_weights(self, as_of, data):
        if data.close("SPY") is None:
            return {}
        return {f"SPY#{i + 1}": float(self.params["weight"]) for i in range(int(self.params["slots"]))}


class SpyHoldUncapped(Strategy):
    """SPY buy-and-hold at 100% with the 8% per-position cap lifted (reference only)."""

    id = "spy_hold_uncapped"
    default_params = {"weight": 1.0, "chassis_rules": False}
    rebalance = "drift"
    reenter_after_stop = True

    @property
    def chassis(self):
        keep = bool(self.params["chassis_rules"])
        return Chassis(max_positions=8, max_weight=1.0,
                       daily_loss_limit=0.05 if keep else None,
                       trailing_stop=0.12 if keep else None)

    def target_weights(self, as_of, data):
        if data.close("SPY") is None:
            return {}
        return {"SPY": float(self.params["weight"])}
