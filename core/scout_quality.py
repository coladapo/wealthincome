"""Scout quality scoring — measures how predictive each pre-LLM scout is.

For each scout (vwap, options_flow, insider_buy, macro, earnings, rag),
we look at closed trades where the scout fired with a clear signal vs not,
and compute the win-rate delta. A scout that helps wins consistently
ranks high; a scout that fires on noise ranks low.

Run via: venv/bin/python -m core.scout_quality
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from backend.db import db


# Each scout maps to a JSON path inside trades.entry_signals_json that holds its
# fired/not-fired flag. Add a new line here to track a new scout.
SCOUT_SIGNAL_FLAGS: dict[str, str] = {
    "vwap":         "$.vwap_above",            # bullish if true
    "options_flow": "$.unusual_call_volume",   # bullish if true
    "insider_buy":  "$.insider_cluster_buy",   # bullish if true
    "earnings":     "$.earnings_within_7d",    # negative — risk if true
    "rag":          "$.similar_trades_winrate_high",  # bullish if true
    "macro":        "$.macro_supportive",      # bullish if true
}


@dataclass
class ScoutScore:
    scout: str
    fired_count: int
    not_fired_count: int
    fired_winrate: float
    not_fired_winrate: float
    delta: float           # fired_winrate − not_fired_winrate (higher = scout helps)
    fired_avg_pnl_pct: float
    not_fired_avg_pnl_pct: float
    sample_size_ok: bool

    def as_dict(self) -> dict:
        return {
            "scout": self.scout,
            "fired_count": self.fired_count,
            "not_fired_count": self.not_fired_count,
            "fired_winrate": round(self.fired_winrate, 3),
            "not_fired_winrate": round(self.not_fired_winrate, 3),
            "delta": round(self.delta, 3),
            "fired_avg_pnl_pct": round(self.fired_avg_pnl_pct, 3),
            "not_fired_avg_pnl_pct": round(self.not_fired_avg_pnl_pct, 3),
            "sample_size_ok": self.sample_size_ok,
        }


def _closed_trades() -> list[sqlite3.Row]:
    # entry_signals_json + realized_pnl_pct both live on position_lifecycle.
    with db() as conn:
        return conn.execute(
            """
            SELECT id, symbol, entry_signals_json, realized_pnl_pct AS pnl_pct, closed_at
            FROM position_lifecycle
            WHERE closed_at IS NOT NULL AND entry_signals_json IS NOT NULL
            """
        ).fetchall()


def _score_one(scout: str, json_path: str, trades: list[sqlite3.Row]) -> ScoutScore:
    fired_pnl: list[float] = []
    not_fired_pnl: list[float] = []
    for t in trades:
        try:
            sigs = json.loads(t["entry_signals_json"] or "{}")
        except (TypeError, ValueError):
            continue
        key = json_path.lstrip("$.")
        flag = bool(sigs.get(key))
        pnl = t["pnl_pct"]
        if pnl is None:
            continue
        (fired_pnl if flag else not_fired_pnl).append(pnl)

    def winrate(arr: list[float]) -> float:
        if not arr:
            return 0.0
        return sum(1 for v in arr if v > 0) / len(arr)

    def avg(arr: list[float]) -> float:
        return sum(arr) / len(arr) if arr else 0.0

    fr_wr = winrate(fired_pnl)
    nf_wr = winrate(not_fired_pnl)
    return ScoutScore(
        scout=scout,
        fired_count=len(fired_pnl),
        not_fired_count=len(not_fired_pnl),
        fired_winrate=fr_wr,
        not_fired_winrate=nf_wr,
        delta=fr_wr - nf_wr,
        fired_avg_pnl_pct=avg(fired_pnl),
        not_fired_avg_pnl_pct=avg(not_fired_pnl),
        sample_size_ok=len(fired_pnl) >= 10 and len(not_fired_pnl) >= 10,
    )


def score_all() -> list[ScoutScore]:
    trades = _closed_trades()
    return [_score_one(s, p, trades) for s, p in SCOUT_SIGNAL_FLAGS.items()]


def persist_scores(scores: Iterable[ScoutScore]) -> None:
    now = datetime.utcnow().isoformat()
    with db() as conn:
        for s in scores:
            conn.execute(
                """
                INSERT INTO signal_calibration
                    (computed_at, lookback_trades, signal_type,
                     total_trades_with_signal, winning_trades,
                     win_rate, avg_pnl_pct, avg_hold_days,
                     valid_from, valid_through)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    now,
                    s.fired_count + s.not_fired_count,
                    f"scout:{s.scout}",
                    s.fired_count,
                    int(s.fired_count * s.fired_winrate),
                    s.fired_winrate,
                    s.fired_avg_pnl_pct,
                    now,
                    "9999-12-31T00:00:00",
                ),
            )


def report() -> str:
    scores = score_all()
    persist_scores(scores)
    if not any(s.sample_size_ok for s in scores):
        return (
            "Scout quality report: not enough closed trades yet. "
            "Need at least 10 fired + 10 not-fired per scout to draw conclusions. "
            "Reading from current data anyway:\n\n" + _fmt(scores)
        )
    return "Scout quality report:\n\n" + _fmt(scores)


def _fmt(scores: list[ScoutScore]) -> str:
    lines = [
        f"{'scout':<14} {'n_fired':>8} {'n_quiet':>8} {'fired_wr':>10} "
        f"{'quiet_wr':>10} {'delta':>8} {'sample_ok':>10}"
    ]
    for s in scores:
        lines.append(
            f"{s.scout:<14} {s.fired_count:>8} {s.not_fired_count:>8} "
            f"{s.fired_winrate:>10.0%} {s.not_fired_winrate:>10.0%} "
            f"{s.delta:>+8.0%} {('yes' if s.sample_size_ok else 'no'):>10}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    print(report())
