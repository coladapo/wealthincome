"""Hard risk limits — single source of truth for position caps.

These are CODE-LEVEL CEILINGS, deliberately not config (a config flag can be
toggled mid-flight; a code guard cannot — see bugs_and_guards memory).
DB config `max_position_pct` may size positions LOWER than the ceiling,
never higher. Every order path (autonomous trader, manual dashboard form,
/order endpoint) must import from here.

History: until 2026-06-11 the autonomous trader hardcoded 25% while manual
paths hardcoded 8% — the AI could take 3x the risk a human could (G1 in
AUDIT-2026-06-11.md). Unified at 8%.
"""

# Maximum fraction of portfolio value in a single symbol. Applies to BUYS
# only — sells/exits are never capped.
MAX_SINGLE_POSITION_PCT = 0.08

# Maximum fraction of portfolio value deployed across all positions.
MAX_DEPLOY_PCT = 0.80

# Regime-conditional exits (BACKTEST-REPORT.md, 2026-06-11): the preemptive
# exits (SMA50-breach monitor, momentum-collapse) cost ~12 points of win rate
# and ~2/3 of expectancy in bull markets but are essential crash insurance in
# bears. They switch OFF only in a STRONG bull; any ambiguity keeps them on.
STRONG_BULL_MIN_SCORE = 70


def preemptive_exits_active(regime: str | None, score: float | None) -> bool:
    """True = keep the SMA50-breach / momentum-collapse exits armed.

    Conservative by construction: only an unambiguous strong BULL reading
    disarms them. Unknown/missing regime data keeps the insurance on.
    """
    if not regime:
        return True
    try:
        return not (regime.upper() == "BULL" and float(score or 0) >= STRONG_BULL_MIN_SCORE)
    except (TypeError, ValueError):
        return True
