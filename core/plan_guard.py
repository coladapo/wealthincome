"""Plan guard — keep the trader from eating the family's shared Claude plan.

The plan reading comes from Cosmo's hourly probe (~/.claude/state/claude-usage.json:
`windows.five_hour.pct`, `windows.seven_day.pct`, `resets_at` epoch seconds,
`checked_at` epoch seconds). This module only READS that file — it never runs the
probe. A missing, unreadable or > 2 h old reading means "unknown" and the guard
stays off (logged once per process), so a dead probe can never stall the trader.

When the reading is at/above config `plan_guard_week_pct` (default 95) or
`plan_guard_5h_pct` (default 90) the trader skips the brain call for that cycle
(no new entries); exits, stops and reconcile run as usual.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

USAGE_PATH = os.environ.get("WEALTHINCOME_PLAN_USAGE_FILE",
                            os.path.expanduser("~/.claude/state/claude-usage.json"))
MAX_AGE_S = 2 * 3600
DEFAULT_WEEK_PCT = 95.0
DEFAULT_5H_PCT = 90.0

_stale_logged = False


def plan_state(path: Optional[str] = None, now: Optional[float] = None) -> Dict[str, Any]:
    """Latest plan reading. `fresh` is False when missing/unreadable/older than 2 h."""
    p = path or USAGE_PATH
    now = time.time() if now is None else now
    out: Dict[str, Any] = {"fresh": False, "five_hour_pct": None, "week_pct": None,
                           "five_hour_resets_at": None, "week_resets_at": None,
                           "checked_at": None, "age_s": None, "reason": None}
    try:
        with open(p) as f:
            data = json.load(f)
        checked = data.get("checked_at") or os.path.getmtime(p)
        w = data.get("windows") or {}
        five, week = w.get("five_hour") or {}, w.get("seven_day") or {}
        out.update(five_hour_pct=_num(five.get("pct")), week_pct=_num(week.get("pct")),
                   five_hour_resets_at=five.get("resets_at"), week_resets_at=week.get("resets_at"),
                   checked_at=float(checked), age_s=round(now - float(checked)))
    except FileNotFoundError:
        out["reason"] = "no plan reading file"
        return out
    except Exception as e:
        out["reason"] = f"unreadable plan reading ({type(e).__name__})"
        return out
    if out["age_s"] > MAX_AGE_S:
        out["reason"] = f"plan reading is {out['age_s'] // 60} min old"
    elif out["five_hour_pct"] is None and out["week_pct"] is None:
        out["reason"] = "plan reading has no percentages"
    else:
        out["fresh"] = True
    return out


def _num(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _threshold(cfg: Dict[str, str], key: str, default: float) -> float:
    try:
        return float(cfg.get(key, default))
    except (TypeError, ValueError):
        return default


def should_hold(cfg: Dict[str, str], state: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """(hold, reason). hold=True → skip the brain call for new entries this cycle."""
    global _stale_logged
    s = state if state is not None else plan_state()
    if not s["fresh"]:
        if not _stale_logged:
            logger.info(f"Plan guard off: {s['reason']}")
            _stale_logged = True
        return False, s["reason"] or "no reading"
    _stale_logged = False
    week_max = _threshold(cfg, "plan_guard_week_pct", DEFAULT_WEEK_PCT)
    five_max = _threshold(cfg, "plan_guard_5h_pct", DEFAULT_5H_PCT)
    if s["week_pct"] is not None and s["week_pct"] >= week_max:
        return True, f"shared plan week at {s['week_pct']:.0f}% (guard {week_max:.0f}%)"
    if s["five_hour_pct"] is not None and s["five_hour_pct"] >= five_max:
        return True, f"shared plan 5h window at {s['five_hour_pct']:.0f}% (guard {five_max:.0f}%)"
    return False, "under thresholds"


def _default_post(text: str) -> bool:
    from scripts.daily_digest import _post_via_webhook
    return _post_via_webhook(text, attempts=1)


def note_hold(reason: str, cycle_id: Optional[int] = None,
              post: Optional[Callable[[str], bool]] = None, now: Optional[datetime] = None) -> None:
    """Side effects of a held cycle: one WARNING per cycle, an errors row
    'plan_guard_hold' at most once per hour, one #wealthincome line per day."""
    from backend.db import db, get_config, record_error, set_config
    now = now or datetime.now()
    logger.warning(f"Plan guard HOLD — brain not asked for new entries this cycle: {reason}")
    try:
        with db() as conn:
            recent = conn.execute(
                "SELECT 1 FROM errors WHERE error_type = 'plan_guard_hold' AND occurred_at >= ? LIMIT 1",
                ((now - timedelta(hours=1)).isoformat(),)).fetchone()
        if not recent:
            record_error("plan_guard_hold", reason, cycle_id)
    except Exception as e:
        logger.warning(f"plan_guard_hold error row failed: {e}")
    try:
        today = (now.date() if isinstance(now, datetime) else date.today()).isoformat()
        if get_config().get("plan_guard_last_post") != today:
            set_config("plan_guard_last_post", today)
            (post or _default_post)(
                f"⏸️ Trader paused new entries: {reason}. Exits and stops still run; "
                f"entries resume when the plan reading drops.")
    except Exception as e:
        logger.warning(f"plan guard daily post failed: {e}")
