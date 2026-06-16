"""Self-heal bright-line guarantees (cross-provider debate verdict 2026-06-16).

The one invariant that must never regress: ambiguous state is NEVER given an
auto-fix; only reversible/unambiguous/risk-reducing repairs are.
"""

from core.self_heal import Finding


def test_only_safe_findings_carry_a_fix():
    # A 'propose' finding must never carry an executable fix.
    p = Finding("propose", "DB/broker disagree", "ambiguous")
    assert p.fix is None

    # An 'auto' finding is the only kind allowed to carry one.
    a = Finding("auto", "missing stop", "re-arm", fix=lambda: "placed stop")
    assert a.fix is not None and a.fix() == "placed stop"


def test_severity_vocabulary_is_closed():
    # Guards against a future edit introducing a silent third auto-acting tier.
    for sev in ("auto", "propose", "info"):
        assert Finding(sev, "t", "d").severity == sev


def test_diagnose_classifies_disagreements_as_propose(monkeypatch):
    """DB-vs-broker disagreement (either direction) must be propose, never auto."""
    import core.self_heal as sh

    class _Pos:
        def __init__(self, sym, qty):
            self.symbol, self.qty = sym, qty

    class _FakeAlpaca:
        def is_market_open(self):
            return True
        def get_positions(self):
            return [_Pos("HELD_ONLY", 10)]          # broker has it, DB won't
        def get_orders(self, status="open", limit=200):
            return []

    monkeypatch.setattr(sh, "_alpaca", lambda: _FakeAlpaca())

    import sqlite3
    import backend.db as db

    # DB knows about a different symbol the broker doesn't hold.
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE position_lifecycle (
        id INTEGER PRIMARY KEY, symbol TEXT, status TEXT, entry_qty REAL,
        entry_price REAL, trailing_stop_order_id TEXT)""")
    conn.execute("INSERT INTO position_lifecycle (symbol,status,entry_qty,entry_price)"
                 " VALUES ('DB_ONLY','open',5,100)")
    conn.commit(); conn.close()
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(sh, "DB_PATH", path, raising=False)

    findings = sh.diagnose()
    by_title = {f.title: f for f in findings}
    # Both disagreement directions present and BOTH propose-only (no fix).
    disagreements = [f for f in findings if "DB_ONLY" in f.title or "HELD_ONLY" in f.title]
    assert disagreements, "expected DB/broker disagreement findings"
    for f in disagreements:
        assert f.severity == "propose"
        assert f.fix is None
    os.unlink(path)
