"""The strategy contract and the strategy registry (data/research.db).

Contract
--------
A strategy is a frozen, deterministic rule that maps "what was knowable at the
close of `as_of`" to the portfolio it wants to hold:

    class MyStrategy(Strategy):
        id = "my_strategy"
        default_params = {"lookback": 20}
        def target_weights(self, as_of, data):      # data: research.asof.AsOfData
            return {"AAPL": 0.08, "MSFT": 0.05}       # fractions of equity

Weights are desired fractions of account equity, each in [0, 0.08], at most 8
names, long only. The simulator (research/portfolio_sim.py) enforces the live
chassis caps regardless of what a strategy returns, and counts violations.

``version`` is sha256(source file bytes + canonical params JSON): editing the
strategy's source file or changing a parameter produces a new version, so a
registered version is a frozen rule. (Only the defining source file is hashed;
helpers imported from elsewhere are not -- keep a strategy's logic in its file.)

Optional hooks / switches:
  fit(train_data) -> params dict or None   walk-forward parameter selection on the
                                           training window (default: no-op)
  rebalance = "drift" | "target"           drift: trade a name only when its target
                                           changes (live-chassis behaviour, winners run);
                                           target: rebalance back to target weights
  reenter_after_stop                       may a name stopped out by the trailing stop
                                           be re-bought while its target is unchanged
  chassis                                  None = the live chassis. Anything else makes
                                           the strategy REFERENCE ONLY: it can be
                                           evaluated but never promoted.

Registry
--------
Table ``strategies`` keyed by (id, version). Status lifecycle:
    candidate -> shadow -> champion -> retired
Promotion to champion requires ``approved_by="chris"`` -- the code never promotes
on its own -- and retires the previous champion (demoted_at set) in one transaction.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import sqlite3
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from research.bars import connect

STATUSES = ("candidate", "shadow", "champion", "retired")
ALLOWED_TRANSITIONS = {
    "candidate": {"shadow", "retired"},
    "shadow": {"candidate", "champion", "retired"},
    "champion": {"retired"},
    "retired": {"shadow", "champion"},   # retired -> champion is a (Chris-approved) rollback
}
APPROVER = "chris"
MAX_WEIGHT = 0.08
MAX_NAMES = 8

SCHEMA = """
CREATE TABLE IF NOT EXISTS strategies (
    id             TEXT NOT NULL,
    version        TEXT NOT NULL,
    params_json    TEXT NOT NULL,
    source_path    TEXT,
    status         TEXT NOT NULL DEFAULT 'candidate',
    hypothesis_id  TEXT,
    created_at     TEXT NOT NULL,
    promoted_at    TEXT,
    demoted_at     TEXT,
    notes          TEXT,
    reference_only INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (id, version)
);
"""


def canonical_params(params: Dict) -> str:
    return json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)


class Strategy:
    """Base class. Subclasses set `id`, `default_params` and implement target_weights."""

    id: str = ""
    default_params: Dict[str, Any] = {}
    rebalance: str = "drift"
    reenter_after_stop: bool = True
    chassis: Any = None

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        if not self.id:
            raise ValueError(f"{type(self).__name__} must set a class attribute `id`")
        params = dict(params or {})
        unknown = set(params) - set(self.default_params)
        if unknown:
            raise ValueError(f"{self.id}: unknown params {sorted(unknown)}; "
                             f"known: {sorted(self.default_params)}")
        self.params: Dict[str, Any] = {**self.default_params, **params}
        if self.rebalance not in ("drift", "target"):
            raise ValueError("rebalance must be 'drift' or 'target'")

    # ----------------------------------------------------------------- identity
    @property
    def source_path(self) -> str:
        return inspect.getsourcefile(type(self)) or ""

    @property
    def version(self) -> str:
        h = hashlib.sha256()
        with open(self.source_path, "rb") as f:
            h.update(f.read())
        h.update(b"\n")
        h.update(canonical_params(self.params).encode())
        return h.hexdigest()

    @property
    def reference_only(self) -> bool:
        return self.chassis is not None

    def describe(self) -> str:
        doc = (inspect.getdoc(type(self)) or "").strip().splitlines()
        head = doc[0] if doc else type(self).__name__
        tag = " [REFERENCE ONLY: non-live chassis]" if self.reference_only else ""
        return f"{self.id} v{self.version[:12]} {canonical_params(self.params)} -- {head}{tag}"

    # ----------------------------------------------------------------- behaviour
    def target_weights(self, as_of: date, data) -> Dict[str, float]:
        raise NotImplementedError

    def fit(self, train) -> Optional[Dict[str, Any]]:
        """Select params on the training window; return a params dict or None (no change)."""
        return None

    def with_params(self, params: Optional[Dict[str, Any]]) -> "Strategy":
        if not params:
            return self
        return type(self)({**self.params, **params})


# --------------------------------------------------------------------------- registry

def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def _open(conn: Optional[sqlite3.Connection], db_path: Optional[str]):
    if conn is not None:
        ensure_schema(conn)
        return conn, False
    c = connect(db_path)
    ensure_schema(c)
    return c, True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_COLS = ["id", "version", "params_json", "source_path", "status", "hypothesis_id", "created_at",
         "promoted_at", "demoted_at", "notes", "reference_only"]


def _row(r) -> Dict:
    d = dict(zip(_COLS, r))
    d["params"] = json.loads(d.pop("params_json"))
    d["reference_only"] = bool(d["reference_only"])
    return d


def register(strategy: Strategy, hypothesis_id: Optional[str] = None, notes: Optional[str] = None,
             conn: Optional[sqlite3.Connection] = None, db_path: Optional[str] = None) -> Dict:
    """Register (id, version) as a candidate. Idempotent: an existing row is returned unchanged."""
    c, own = _open(conn, db_path)
    try:
        v = strategy.version
        if c.execute("SELECT 1 FROM strategies WHERE id=? AND version=?",
                     (strategy.id, v)).fetchone() is None:
            c.execute(f"INSERT INTO strategies({','.join(_COLS)}) VALUES ({','.join('?' * len(_COLS))})",
                      (strategy.id, v, canonical_params(strategy.params), strategy.source_path,
                       "candidate", hypothesis_id, _now(), None, None, notes,
                       int(strategy.reference_only)))
            c.commit()
        return get(strategy.id, v, conn=c)
    finally:
        if own:
            c.close()


def get(id: str, version: Optional[str] = None, conn: Optional[sqlite3.Connection] = None,
        db_path: Optional[str] = None) -> Optional[Dict]:
    """One registry row; without `version` the most recently created version of `id`."""
    c, own = _open(conn, db_path)
    try:
        q = f"SELECT {','.join(_COLS)} FROM strategies WHERE id=?"
        p: list = [id]
        if version:
            q += " AND version=?"
            p.append(version)
        r = c.execute(q + " ORDER BY created_at DESC, rowid DESC LIMIT 1", p).fetchone()
        return _row(r) if r else None
    finally:
        if own:
            c.close()


def list_strategies(status: Optional[str] = None, conn: Optional[sqlite3.Connection] = None,
                    db_path: Optional[str] = None) -> List[Dict]:
    c, own = _open(conn, db_path)
    try:
        q = f"SELECT {','.join(_COLS)} FROM strategies"
        p: list = []
        if status:
            if status not in STATUSES:
                raise ValueError(f"unknown status {status!r}")
            q += " WHERE status=?"
            p.append(status)
        return [_row(r) for r in c.execute(q + " ORDER BY created_at, id", p)]
    finally:
        if own:
            c.close()


def set_status(id: str, version: str, status: str, approved_by: Optional[str] = None,
               notes: Optional[str] = None, conn: Optional[sqlite3.Connection] = None,
               db_path: Optional[str] = None) -> Dict:
    """Move a registered version along the lifecycle.

    champion requires approved_by="chris" (and a live-chassis strategy); promoting
    retires the current champion. Leaving champion records demoted_at.
    """
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    c, own = _open(conn, db_path)
    try:
        cur = get(id, version, conn=c)
        if cur is None:
            raise KeyError(f"strategy {id} {version[:12]} is not registered")
        if status not in ALLOWED_TRANSITIONS[cur["status"]]:
            raise ValueError(f"{id}: {cur['status']} -> {status} is not an allowed transition")
        if status == "champion" and cur["status"] == "retired" and not cur["promoted_at"]:
            # retired -> champion is a ROLLBACK to a former champion only; a candidate
            # parked as retired has never been through shadow and may not skip it.
            raise ValueError(f"{id}: retired -> champion is only allowed for a former champion "
                             "(promoted_at is empty); go through shadow first")
        now = _now()
        if status == "champion":
            if approved_by != APPROVER:
                raise PermissionError("promotion to champion needs approved_by='chris'; "
                                      "the pipeline never promotes on its own")
            if cur["reference_only"]:
                raise PermissionError(f"{id} runs on a non-live chassis (reference only)")
            c.execute("UPDATE strategies SET status='retired', demoted_at=?, "
                      "notes=COALESCE(notes || ' | ', '') || ? WHERE status='champion'",
                      (now, f"superseded by {id} {version[:12]}"))
            c.execute("UPDATE strategies SET status='champion', promoted_at=?, "
                      "notes=COALESCE(notes || ' | ', '') || ? WHERE id=? AND version=?",
                      (now, f"approved_by={approved_by}" + (f": {notes}" if notes else ""),
                       id, version))
        else:
            demoted = now if cur["status"] == "champion" else cur["demoted_at"]
            c.execute("UPDATE strategies SET status=?, demoted_at=?, "
                      "notes=CASE WHEN ? IS NULL THEN notes ELSE COALESCE(notes || ' | ', '') || ? END "
                      "WHERE id=? AND version=?", (status, demoted, notes, notes, id, version))
        c.commit()
        return get(id, version, conn=c)
    finally:
        if own:
            c.close()
