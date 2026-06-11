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
