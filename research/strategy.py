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
  chassis_override                         a Chassis inside its SLEEVE's risk policy
                                           (SLEEVE_LIMITS, e.g. the core sleeve's one
                                           index position at 100%). The simulator and
                                           walk-forward run it (it is what `chassis`
                                           returns) and it is NOT reference-only.

Horizon (sleeve)
----------------
Every strategy declares ``horizon`` in HORIZONS = day | swing | long | treasury and
is judged inside that sleeve; ``register`` refuses a strategy without one. Strategies
registered before the field existed get theirs from LEGACY_HORIZONS (set on the class
at definition time, so their source files -- and therefore their frozen versions --
are untouched).

Registry
--------
Table ``strategies`` keyed by (id, version). Status lifecycle:
    candidate -> shadow -> champion -> retired
Promotion to champion requires ``approved_by="chris"`` -- the code never promotes
on its own -- and retires the previous champion OF THE SAME HORIZON (demoted_at set)
in one transaction: promotion is per sleeve.

Sleeve allocation
-----------------
Table ``sleeve_allocation(date, sleeve, weight, approved_by, notes)``: the share of
account capital each sleeve gets, one row per sleeve per allocation date. Only
``set_allocation(..., approved_by="chris")`` writes it; treasury is always the
remainder (1 - the others), each sleeve is capped at SLEEVE_CAPS until proven.
``current_allocation()`` reads the latest date (all-treasury when nothing is
recorded). Weights only: nothing in the live trader reads this table yet.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import sqlite3
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from research.bars import connect
from research.portfolio_sim import Chassis  # noqa: F401  (the type of chassis_override)

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

HORIZONS = ("day", "swing", "long", "treasury")
SLEEVES = HORIZONS
# strategies registered before `horizon` existed (their files must not change: frozen versions)
LEGACY_HORIZONS = {"champion_replay": "swing", "spy_hold_capped": "long", "spy_hold_uncapped": "long"}
# per-sleeve risk policy a chassis_override must stay inside: (max positions, max weight per
# position). swing keeps the live chassis (8 x 8%); the day sleeve's policy (per-trade risk,
# flat by 15:55 ET, 1% daily stop) is not a daily-bar chassis and has no override yet.
SLEEVE_LIMITS = {"long": (1, 1.0), "treasury": (1, 1.0)}
# capital share each sleeve may get until proven (sleeve-design.md); treasury = the remainder
SLEEVE_CAPS = {"long": 0.60, "swing": 0.40, "day": 0.10, "treasury": 1.0}

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
    horizon        TEXT NOT NULL DEFAULT 'swing',
    PRIMARY KEY (id, version)
);
CREATE TABLE IF NOT EXISTS sleeve_allocation (
    date        TEXT NOT NULL,
    sleeve      TEXT NOT NULL,
    weight      REAL NOT NULL,
    approved_by TEXT NOT NULL,
    notes       TEXT,
    PRIMARY KEY (date, sleeve)
);
"""


def canonical_params(params: Dict) -> str:
    return json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)


class Strategy:
    """Base class. Subclasses set `id`, `default_params` and implement target_weights."""

    id: str = ""
    horizon: str = ""
    default_params: Dict[str, Any] = {}
    rebalance: str = "drift"
    reenter_after_stop: bool = True
    chassis_override: Any = None

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        if not cls.__dict__.get("horizon") and cls.__dict__.get("id") in LEGACY_HORIZONS:
            cls.horizon = LEGACY_HORIZONS[cls.__dict__["id"]]

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        if not self.id:
            raise ValueError(f"{type(self).__name__} must set a class attribute `id`")
        if self.horizon and self.horizon not in HORIZONS:
            raise ValueError(f"{self.id}: horizon {self.horizon!r} not in {HORIZONS}")
        ov = self.chassis_override
        if ov is not None:
            lim = SLEEVE_LIMITS.get(self.horizon)
            if lim is None:
                raise ValueError(f"{self.id}: sleeve {self.horizon!r} allows no chassis_override")
            if ov.max_positions > lim[0] or ov.max_weight > lim[1] + 1e-12:
                raise ValueError(f"{self.id}: chassis_override exceeds the {self.horizon} sleeve "
                                 f"policy (max {lim[0]} positions, {lim[1]:.0%} each)")
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
    def chassis(self):
        """The chassis the simulator runs: None (live chassis) or the sleeve's override."""
        return self.chassis_override

    @property
    def reference_only(self) -> bool:
        return self.chassis is not None and self.chassis is not self.chassis_override

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
    cols = {r[1] for r in conn.execute("PRAGMA table_info(strategies)")}
    if cols and "horizon" not in cols:
        # existing rows get 'swing' (the ADD COLUMN default), then the known legacy ids their sleeve
        conn.execute("ALTER TABLE strategies ADD COLUMN horizon TEXT NOT NULL DEFAULT 'swing'")
        conn.executemany("UPDATE strategies SET horizon=? WHERE id=?",
                         [(h, i) for i, h in LEGACY_HORIZONS.items()])
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
         "promoted_at", "demoted_at", "notes", "reference_only", "horizon"]


def _row(r) -> Dict:
    d = dict(zip(_COLS, r))
    d["params"] = json.loads(d.pop("params_json"))
    d["reference_only"] = bool(d["reference_only"])
    return d


def register(strategy: Strategy, hypothesis_id: Optional[str] = None, notes: Optional[str] = None,
             conn: Optional[sqlite3.Connection] = None, db_path: Optional[str] = None) -> Dict:
    """Register (id, version) as a candidate. Idempotent: an existing row is returned unchanged."""
    if strategy.horizon not in HORIZONS:
        raise ValueError(f"{strategy.id}: register needs a horizon in {HORIZONS} "
                         f"(got {strategy.horizon!r})")
    c, own = _open(conn, db_path)
    try:
        v = strategy.version
        if c.execute("SELECT 1 FROM strategies WHERE id=? AND version=?",
                     (strategy.id, v)).fetchone() is None:
            c.execute(f"INSERT INTO strategies({','.join(_COLS)}) VALUES ({','.join('?' * len(_COLS))})",
                      (strategy.id, v, canonical_params(strategy.params), strategy.source_path,
                       "candidate", hypothesis_id, _now(), None, None, notes,
                       int(strategy.reference_only), strategy.horizon))
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
                    db_path: Optional[str] = None, horizon: Optional[str] = None) -> List[Dict]:
    c, own = _open(conn, db_path)
    try:
        q = f"SELECT {','.join(_COLS)} FROM strategies"
        where, p = [], []
        if status:
            if status not in STATUSES:
                raise ValueError(f"unknown status {status!r}")
            where.append("status=?")
            p.append(status)
        if horizon:
            if horizon not in HORIZONS:
                raise ValueError(f"unknown horizon {horizon!r}")
            where.append("horizon=?")
            p.append(horizon)
        if where:
            q += " WHERE " + " AND ".join(where)
        return [_row(r) for r in c.execute(q + " ORDER BY created_at, id", p)]
    finally:
        if own:
            c.close()


def set_status(id: str, version: str, status: str, approved_by: Optional[str] = None,
               notes: Optional[str] = None, conn: Optional[sqlite3.Connection] = None,
               db_path: Optional[str] = None) -> Dict:
    """Move a registered version along the lifecycle.

    champion requires approved_by="chris" (and a non-reference strategy); promoting
    retires the current champion of the same horizon (sleeve). Leaving champion
    records demoted_at.
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
                      "notes=COALESCE(notes || ' | ', '') || ? WHERE status='champion' AND horizon=?",
                      (now, f"superseded by {id} {version[:12]}", cur["horizon"]))
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


# --------------------------------------------------------------------------- sleeve allocation

def set_allocation(weights: Dict[str, float], approved_by: Optional[str], notes: Optional[str] = None,
                   as_of: Optional[str] = None, conn: Optional[sqlite3.Connection] = None,
                   db_path: Optional[str] = None) -> Dict:
    """Record a sleeve allocation dated `as_of` (default today). Chris only.

    Weights are fractions of account capital per sleeve; treasury is the remainder
    (1 - the others) and, if given, must equal it (weights sum to 1). Each sleeve is
    capped at SLEEVE_CAPS until proven. Re-allocating on the same date replaces it.
    """
    if approved_by != APPROVER:
        raise PermissionError("sleeve allocation needs approved_by='chris'")
    unknown = set(weights) - set(SLEEVES)
    if unknown:
        raise ValueError(f"unknown sleeves {sorted(unknown)}; known: {SLEEVES}")
    w = {s: float(weights.get(s, 0.0)) for s in SLEEVES if s != "treasury"}
    for s, v in w.items():
        if not 0.0 <= v <= SLEEVE_CAPS[s] + 1e-12:
            raise ValueError(f"{s} weight {v:.4f} outside [0, {SLEEVE_CAPS[s]:.0%}] (cap until proven)")
    rest = 1.0 - sum(w.values())
    if rest < -1e-9:
        raise ValueError(f"sleeve weights sum to {1 - rest:.4f} > 1")
    if "treasury" in weights and abs(float(weights["treasury"]) - rest) > 1e-9:
        raise ValueError(f"weights must sum to 1: treasury must be {rest:.4f} (1 - the others)")
    w["treasury"] = max(rest, 0.0)
    d = as_of or date.today().isoformat()
    c, own = _open(conn, db_path)
    try:
        with c:
            c.execute("DELETE FROM sleeve_allocation WHERE date=?", (d,))
            c.executemany("INSERT INTO sleeve_allocation(date, sleeve, weight, approved_by, notes) "
                          "VALUES (?,?,?,?,?)", [(d, s, w[s], approved_by, notes) for s in SLEEVES])
        return current_allocation(conn=c)
    finally:
        if own:
            c.close()


def current_allocation(conn: Optional[sqlite3.Connection] = None,
                       db_path: Optional[str] = None) -> Dict:
    """Latest allocation: {date, weights{sleeve: w}, approved_by, notes}; all treasury if none."""
    c, own = _open(conn, db_path)
    try:
        d = c.execute("SELECT MAX(date) FROM sleeve_allocation").fetchone()[0]
        if d is None:
            return {"date": None, "weights": {s: (1.0 if s == "treasury" else 0.0) for s in SLEEVES},
                    "approved_by": None, "notes": "default: no allocation recorded (all treasury)"}
        rows = c.execute("SELECT sleeve, weight, approved_by, notes FROM sleeve_allocation "
                         "WHERE date=?", (d,)).fetchall()
        weights = {s: 0.0 for s in SLEEVES}
        weights.update({s: float(v) for s, v, _, _ in rows})
        return {"date": d, "weights": weights, "approved_by": rows[0][2], "notes": rows[0][3]}
    finally:
        if own:
            c.close()
