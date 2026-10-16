"""Strategy contract (content-hashed versions) and registry lifecycle. Offline."""

import importlib.util
import sys

import pytest

from research import bars as rb
from research import strategy as reg
from research.strategies.spy_hold import SpyHoldCapped, SpyHoldUncapped

SRC = '''
from research.strategy import Strategy


class Tmp(Strategy):
    """Temporary strategy for hashing tests."""
    id = "tmp"
    horizon = "swing"
    default_params = {"k": 1}

    def target_weights(self, as_of, data):
        return {"AAA": 0.0%d}
'''


def _load(path, n):
    path.write_text(SRC % n)
    spec = importlib.util.spec_from_file_location(f"tmp_strat_{n}", str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod.Tmp


@pytest.fixture()
def conn(tmp_path):
    c = rb.connect(str(tmp_path / "research.db"))
    yield c
    c.close()


def test_version_hashes_source_and_params(tmp_path):
    p = tmp_path / "tmp_strat.py"
    A = _load(p, 5)
    v1 = A().version
    assert A().version == v1                       # stable
    assert A({"k": 2}).version != v1               # params change -> new version
    B = _load(p, 6)                                # source edit -> new version
    assert B().version != v1
    assert len(v1) == 64


def test_unknown_params_rejected_and_describe():
    with pytest.raises(ValueError):
        SpyHoldCapped({"slotz": 3})
    assert "spy_hold_capped" in SpyHoldCapped().describe()
    assert "REFERENCE ONLY" in SpyHoldUncapped().describe()
    assert SpyHoldUncapped().reference_only and not SpyHoldCapped().reference_only


def test_register_is_idempotent_and_lists(conn):
    s = SpyHoldCapped()
    a = reg.register(s, hypothesis_id="h1", conn=conn)
    b = reg.register(s, hypothesis_id="h1", conn=conn)
    assert a == b and a["status"] == "candidate" and a["params"] == s.params
    reg.register(SpyHoldCapped({"slots": 4}), conn=conn)
    assert len(reg.list_strategies(conn=conn)) == 2
    assert len(reg.list_strategies("candidate", conn=conn)) == 2


def test_champion_needs_chris_and_retires_previous(conn):
    s1, s2 = SpyHoldCapped(), SpyHoldCapped({"slots": 4})
    for s in (s1, s2):
        reg.register(s, conn=conn)
    with pytest.raises(ValueError):                         # candidate -> champion skips shadow
        reg.set_status(s1.id, s1.version, "champion", approved_by="chris", conn=conn)
    reg.set_status(s1.id, s1.version, "shadow", conn=conn)
    with pytest.raises(PermissionError):
        reg.set_status(s1.id, s1.version, "champion", conn=conn)
    with pytest.raises(PermissionError):
        reg.set_status(s1.id, s1.version, "champion", approved_by="the-pipeline", conn=conn)
    c1 = reg.set_status(s1.id, s1.version, "champion", approved_by="chris", conn=conn)
    assert c1["status"] == "champion" and c1["promoted_at"]
    reg.set_status(s2.id, s2.version, "shadow", conn=conn)
    reg.set_status(s2.id, s2.version, "champion", approved_by="chris", conn=conn)
    old = reg.get(s1.id, s1.version, conn=conn)
    assert old["status"] == "retired" and old["demoted_at"]
    assert [r["version"] for r in reg.list_strategies("champion", conn=conn)] == [s2.version]


def test_reference_only_strategy_cannot_be_champion(conn):
    s = SpyHoldUncapped()
    reg.register(s, conn=conn)
    reg.set_status(s.id, s.version, "shadow", conn=conn)
    with pytest.raises(PermissionError):
        reg.set_status(s.id, s.version, "champion", approved_by="chris", conn=conn)


def test_retired_candidate_cannot_skip_shadow_to_champion(conn):
    s = SpyHoldCapped({"slots": 6})
    reg.register(s, conn=conn)
    reg.set_status(s.id, s.version, "retired", conn=conn)       # parked, never a champion
    with pytest.raises(ValueError):
        reg.set_status(s.id, s.version, "champion", approved_by="chris", conn=conn)
    # the legitimate rollback path still works: shadow -> champion -> retired -> champion
    reg.set_status(s.id, s.version, "shadow", conn=conn)
    reg.set_status(s.id, s.version, "champion", approved_by="chris", conn=conn)
    reg.set_status(s.id, s.version, "retired", conn=conn)
    back = reg.set_status(s.id, s.version, "champion", approved_by="chris", conn=conn)
    assert back["status"] == "champion"
