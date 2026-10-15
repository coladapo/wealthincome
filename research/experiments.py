"""Pre-registration and the experiment budget (data/research.db).

Every evaluator run must name a hypothesis that was registered BEFORE the run:

    register_hypothesis(id, rationale, rule_text, max_trials=10)

A hypothesis is immutable once registered (re-registering the same id with the
same rationale/rule/budget is a no-op; anything different raises). Each run of a
strategy variant under the hypothesis is one TRIAL, logged as a row in
``variant_runs`` with ``hypothesis_id`` set. When ``trials_used >= max_trials``
the evaluator is refused (``TrialBudgetExhausted``): you cannot keep tweaking a
rule until something looks good -- the number of trials is exactly what the
selection-adjusted statistics (research/selection.py) deflate by.

``variant_runs`` predates this module (the edge event studies log there with no
hypothesis). ``ensure_schema`` adds the ``hypothesis_id`` column when it is
missing (ALTER TABLE ... ADD COLUMN leaves existing rows untouched, NULL), so the
migration is safe to run against the existing research store.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional

from research.bars import connect

HYPOTHESES_SCHEMA = """
CREATE TABLE IF NOT EXISTS hypotheses (
    id          TEXT PRIMARY KEY,
    rationale   TEXT NOT NULL,
    rule_text   TEXT NOT NULL,
    max_trials  INTEGER NOT NULL DEFAULT 10,
    created_at  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open'
);
"""


class TrialBudgetExhausted(RuntimeError):
    """The hypothesis has used all of its pre-registered trials."""


class UnknownHypothesis(KeyError):
    """The run names a hypothesis that was never registered."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(HYPOTHESES_SCHEMA)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(variant_runs)")}
    if "hypothesis_id" not in cols:
        conn.execute("ALTER TABLE variant_runs ADD COLUMN hypothesis_id TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_variant_runs_hyp ON variant_runs(hypothesis_id)")
    conn.commit()


def _open(conn: Optional[sqlite3.Connection], db_path: Optional[str]):
    if conn is not None:
        ensure_schema(conn)
        return conn, False
    c = connect(db_path)
    ensure_schema(c)
    return c, True


def register_hypothesis(id: str, rationale: str, rule_text: str, max_trials: int = 10,
                        conn: Optional[sqlite3.Connection] = None,
                        db_path: Optional[str] = None) -> Dict:
    if not id or not rationale or not rule_text:
        raise ValueError("id, rationale and rule_text are all required")
    if int(max_trials) < 1:
        raise ValueError("max_trials must be >= 1")
    c, own = _open(conn, db_path)
    try:
        row = c.execute("SELECT rationale, rule_text, max_trials FROM hypotheses WHERE id=?",
                        (id,)).fetchone()
        if row is not None:
            if tuple(row) != (rationale, rule_text, int(max_trials)):
                raise ValueError(f"hypothesis {id!r} is already registered with different "
                                 "content; pre-registrations are immutable -- register a new id")
        else:
            c.execute("INSERT INTO hypotheses(id, rationale, rule_text, max_trials, created_at) "
                      "VALUES (?,?,?,?,?)", (id, rationale, rule_text, int(max_trials), _now()))
            c.commit()
        return get_hypothesis(id, conn=c)
    finally:
        if own:
            c.close()


def get_hypothesis(id: str, conn: Optional[sqlite3.Connection] = None,
                   db_path: Optional[str] = None) -> Dict:
    c, own = _open(conn, db_path)
    try:
        row = c.execute("SELECT id, rationale, rule_text, max_trials, created_at, status "
                        "FROM hypotheses WHERE id=?", (id,)).fetchone()
        if row is None:
            raise UnknownHypothesis(id)
        out = dict(zip(["id", "rationale", "rule_text", "max_trials", "created_at", "status"], row))
        out["trials_used"] = trials_used(id, conn=c)
        return out
    finally:
        if own:
            c.close()


def trials_used(hypothesis_id: str, conn: Optional[sqlite3.Connection] = None,
                db_path: Optional[str] = None) -> int:
    c, own = _open(conn, db_path)
    try:
        return int(c.execute("SELECT COUNT(*) FROM variant_runs WHERE hypothesis_id=?",
                             (hypothesis_id,)).fetchone()[0])
    finally:
        if own:
            c.close()


def check_budget(hypothesis_id: str, conn: Optional[sqlite3.Connection] = None,
                 db_path: Optional[str] = None) -> int:
    """Raise unless another trial is allowed; return the trials remaining (>= 1)."""
    h = get_hypothesis(hypothesis_id, conn=conn, db_path=db_path)
    left = int(h["max_trials"]) - int(h["trials_used"])
    if left <= 0:
        raise TrialBudgetExhausted(
            f"hypothesis {hypothesis_id!r} has used {h['trials_used']}/{h['max_trials']} trials; "
            "register a new, separately motivated hypothesis instead of re-tuning this one")
    return left


def log_trial(hypothesis_id: str, edge_name: str, params: Dict, result: Dict,
              n_events: Optional[int] = None, conn: Optional[sqlite3.Connection] = None,
              db_path: Optional[str] = None) -> int:
    """Insert one trial into variant_runs (the budget is re-checked first). Returns the row id."""
    c, own = _open(conn, db_path)
    try:
        check_budget(hypothesis_id, conn=c)
        cur = c.execute(
            "INSERT INTO variant_runs(edge_name, run_at, params_json, n_events, result_json, "
            "hypothesis_id) VALUES (?,?,?,?,?,?)",
            (edge_name, _now(), json.dumps(params, sort_keys=True, default=str), n_events,
             json.dumps(result, sort_keys=True, default=str), hypothesis_id))
        c.commit()
        return int(cur.lastrowid)
    finally:
        if own:
            c.close()


def trial_results(hypothesis_id: str, conn: Optional[sqlite3.Connection] = None,
                  db_path: Optional[str] = None) -> List[Dict]:
    """All logged trials for a hypothesis, oldest first, with result_json decoded."""
    c, own = _open(conn, db_path)
    try:
        rows = c.execute("SELECT id, edge_name, run_at, params_json, result_json FROM variant_runs "
                         "WHERE hypothesis_id=? ORDER BY id", (hypothesis_id,)).fetchall()
    finally:
        if own:
            c.close()
    return [{"id": rid, "edge_name": edge, "run_at": run_at,
             "params": json.loads(pj) if pj else {},
             "result": json.loads(rj) if rj else {}}
            for rid, edge, run_at, pj, rj in rows]


def list_hypotheses(conn: Optional[sqlite3.Connection] = None,
                    db_path: Optional[str] = None) -> List[Dict]:
    c, own = _open(conn, db_path)
    try:
        ids = [r[0] for r in c.execute("SELECT id FROM hypotheses ORDER BY created_at, id")]
        return [get_hypothesis(i, conn=c) for i in ids]
    finally:
        if own:
            c.close()
