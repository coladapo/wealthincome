"""
Idempotency keys (Alpaca client_order_id) shared by the trader and reconciler.

Kept dependency-free so the reconciler (API process) can use them without
importing the trader daemon module.
"""

import hashlib
from typing import Optional

KEY_PREFIX = "wi-"


def order_idempotency_key(cycle_id, symbol: str, side: str, qty, attempt: int = 0) -> str:
    """Deterministic client_order_id for one decision: hash(cycle_id+symbol+side+qty).

    The same decision replayed (restart mid-cycle, retried POST after a
    timeout) produces the same key, so Alpaca and the `orders` table both
    recognise it. `attempt` separates the deliberate IOC retry from the first try.
    """
    raw = f"{cycle_id}|{symbol.upper()}|{side.lower()}|{float(qty):g}"
    key = KEY_PREFIX + hashlib.sha256(raw.encode()).hexdigest()[:24]
    return f"{key}-r{attempt}" if attempt else key


def trailing_stop_key(position_id: int, qty, prev_stop_order_id: Optional[str] = None) -> str:
    """client_order_id for a position's trailing stop. Includes the previous stop
    id so a re-arm after a cancel gets a fresh key, but a replay of the same
    re-arm does not."""
    raw = f"ts|{position_id}|{float(qty):g}|{prev_stop_order_id or 'none'}"
    return KEY_PREFIX + "ts-" + hashlib.sha256(raw.encode()).hexdigest()[:24]
