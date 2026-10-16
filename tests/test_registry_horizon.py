"""Registry `horizon` (sleeve) field, safe migration, per-sleeve champion, chassis_override. Offline."""

import sqlite3

import pytest

from research import bars as rb
from research import strategy as reg
from research.portfolio_sim import Chassis
from research.strategies.champion_replay import ChampionReplay
from research.strategies.spy_hold import SpyHoldCapped, SpyHoldUncapped
from research.strategies.treasury_sweep import TreasurySweep
from research.strategies.trend_filtered_core import TrendFilteredCore
from research.strategy import Strategy

OLD_SCHEMA = """
CREATE TABLE strategies (
    id TEXT NOT NULL, version TEXT NOT NULL, params_json TEXT NOT NULL, source_path TEXT,
    status TEXT NOT NULL DEFAULT 'candidate', hypothesis_id TEXT, created_at TEXT NOT NULL,
    promoted_at TEXT, demoted_at TEXT, notes TEXT, reference_only INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (id, version));
"""


class NoHorizon(Strategy):
    """A strategy that forgot its horizon (test helper)."""
    id = "no_horizon"

    def target_weights(self, as_of, data):
        return {}


class SwingTmp(Strategy):
    """Swing test strategy."""
    id = "swing_tmp"
    horizon = "swing"
    default_params = {"k": 1}

    def target_weights(self, as_of, data):
        return {}


@pytest.fixture()
def conn(tmp_path):
    c = rb.connect(str(tmp_path / "research.db"))
    yield c
    c.close()


def test_migration_adds_horizon_with_legacy_sleeves(tmp_path):
    path = str(tmp_path / "old.db")
    c = sqlite3.connect(path)
    c.executescript(OLD_SCHEMA)
    for i in ("champion_replay", "spy_hold_capped", "spy_hold_uncapped", "something_else"):
        c.execute("INSERT INTO strategies(id, version, params_json, created_at) VALUES (?,?,?,?)",
                  (i, "v1", "{}", "2026-10-14T00:00:00+00:00"))
    c.commit()
    c.close()
    rows = {r["id"]: r for r in reg.list_strategies(db_path=path)}
    assert rows["champion_replay"]["horizon"] == "swing"
    assert rows["spy_hold_capped"]["horizon"] == "long"
    assert rows["spy_hold_uncapped"]["horizon"] == "long"
    assert rows["something_else"]["horizon"] == "swing"          # ADD COLUMN default
    assert all(r["params"] == {} for r in rows.values())        # rows otherwise untouched
    # idempotent: a second open neither fails nor re-applies the legacy mapping
    c = sqlite3.connect(path)
    c.execute("UPDATE strategies SET horizon='day' WHERE id='spy_hold_capped'")
    c.commit()
    c.close()
    assert reg.get("spy_hold_capped", db_path=path)["horizon"] == "day"


def test_legacy_classes_get_horizon_without_touching_source():
    assert ChampionReplay.horizon == "swing"
    assert SpyHoldCapped.horizon == "long" and SpyHoldUncapped.horizon == "long"
    assert TreasurySweep.horizon == "treasury" and TrendFilteredCore.horizon == "long"


def test_register_requires_horizon_and_records_it(conn):
    with pytest.raises(ValueError, match="horizon"):
        reg.register(NoHorizon(), conn=conn)
    assert reg.register(TreasurySweep(), conn=conn)["horizon"] == "treasury"
    assert reg.register(TrendFilteredCore(), conn=conn)["horizon"] == "long"
    assert reg.register(SwingTmp(), conn=conn)["horizon"] == "swing"
    assert [r["id"] for r in reg.list_strategies(horizon="long", conn=conn)] == ["trend_filtered_core"]
    assert len(reg.list_strategies(status="candidate", horizon="swing", conn=conn)) == 1
    with pytest.raises(ValueError):
        reg.list_strategies(horizon="hybrid", conn=conn)


def test_unknown_horizon_rejected():
    class Bad(Strategy):
        """bad"""
        id = "bad_h"
        horizon = "hybrid"

    with pytest.raises(ValueError):
        Bad()


def test_chassis_override_is_honoured_and_not_reference_only():
    t, c = TreasurySweep(), TrendFilteredCore()
    assert t.chassis is t.chassis_override and t.chassis.max_weight == 1.0
    assert c.chassis.max_positions == 1 and c.chassis.trailing_stop is None
    assert not t.reference_only and not c.reference_only
    assert "REFERENCE ONLY" not in c.describe()
    assert SpyHoldUncapped().reference_only and not SpyHoldCapped().reference_only
    assert SwingTmp().chassis is None                            # live chassis


def test_chassis_override_must_fit_the_sleeve_policy():
    class TooWide(Strategy):
        """two 100% positions in the core sleeve"""
        id = "too_wide"
        horizon = "long"
        chassis_override = Chassis(max_positions=2, max_weight=1.0)

    class SwingOverride(Strategy):
        """swing keeps the live chassis"""
        id = "swing_override"
        horizon = "swing"
        chassis_override = Chassis(max_positions=8, max_weight=0.5)

    with pytest.raises(ValueError, match="policy"):
        TooWide()
    with pytest.raises(ValueError, match="no chassis_override"):
        SwingOverride()


def test_champion_is_per_sleeve(conn):
    swing, core = SwingTmp(), TrendFilteredCore()
    for s in (swing, core):
        reg.register(s, conn=conn)
        reg.set_status(s.id, s.version, "shadow", conn=conn)
        reg.set_status(s.id, s.version, "champion", approved_by="chris", conn=conn)
    assert reg.get(swing.id, swing.version, conn=conn)["status"] == "champion"   # not retired by core
    assert reg.get(core.id, core.version, conn=conn)["status"] == "champion"
    core2 = TrendFilteredCore({"sma_days": 150})
    reg.register(core2, conn=conn)
    reg.set_status(core2.id, core2.version, "shadow", conn=conn)
    reg.set_status(core2.id, core2.version, "champion", approved_by="chris", conn=conn)
    assert reg.get(core.id, core.version, conn=conn)["status"] == "retired"     # same sleeve: superseded
    assert reg.get(swing.id, swing.version, conn=conn)["status"] == "champion"
