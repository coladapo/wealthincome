"""Sleeve allocation table: Chris-only writes, treasury is the remainder, caps. Offline."""

import pytest

from research import bars as rb
from research import strategy as reg


@pytest.fixture()
def conn(tmp_path):
    c = rb.connect(str(tmp_path / "research.db"))
    yield c
    c.close()


def test_default_is_all_treasury(conn):
    a = reg.current_allocation(conn=conn)
    assert a["date"] is None and a["approved_by"] is None
    assert a["weights"] == {"day": 0.0, "swing": 0.0, "long": 0.0, "treasury": 1.0}


def test_only_chris_may_allocate(conn):
    for who in (None, "", "cosmo", "Chris "):
        with pytest.raises(PermissionError):
            reg.set_allocation({"long": 0.1}, approved_by=who, conn=conn)
    assert reg.current_allocation(conn=conn)["date"] is None


def test_treasury_is_the_remainder(conn):
    a = reg.set_allocation({"long": 0.3, "swing": 0.2}, approved_by="chris", notes="t",
                           as_of="2026-10-15", conn=conn)
    assert a["weights"] == pytest.approx({"day": 0.0, "swing": 0.2, "long": 0.3, "treasury": 0.5})
    assert a["date"] == "2026-10-15" and a["approved_by"] == "chris" and a["notes"] == "t"
    assert sum(a["weights"].values()) == pytest.approx(1.0)
    rows = conn.execute("SELECT COUNT(*) FROM sleeve_allocation WHERE date='2026-10-15'").fetchone()[0]
    assert rows == 4


def test_weights_must_sum_to_one_and_respect_caps(conn):
    with pytest.raises(ValueError, match="sum to 1"):
        reg.set_allocation({"long": 0.3, "treasury": 0.6}, approved_by="chris", conn=conn)
    with pytest.raises(ValueError):
        reg.set_allocation({"long": 0.6, "swing": 0.4, "day": 0.1}, approved_by="chris", conn=conn)
    with pytest.raises(ValueError, match="cap"):
        reg.set_allocation({"day": 0.2}, approved_by="chris", conn=conn)
    with pytest.raises(ValueError, match="cap"):
        reg.set_allocation({"long": 0.7}, approved_by="chris", conn=conn)
    with pytest.raises(ValueError):
        reg.set_allocation({"swing": -0.1}, approved_by="chris", conn=conn)
    with pytest.raises(ValueError, match="unknown"):
        reg.set_allocation({"hybrid": 0.1}, approved_by="chris", conn=conn)
    ok = reg.set_allocation({"long": 0.6, "swing": 0.4, "treasury": 0.0}, approved_by="chris", conn=conn)
    assert ok["weights"]["treasury"] == pytest.approx(0.0)


def test_latest_date_wins_and_same_date_replaces(conn):
    reg.set_allocation({}, approved_by="chris", notes="seed", as_of="2026-10-15", conn=conn)
    reg.set_allocation({"long": 0.2}, approved_by="chris", as_of="2026-12-31", conn=conn)
    assert reg.current_allocation(conn=conn)["weights"]["long"] == pytest.approx(0.2)
    reg.set_allocation({"long": 0.1}, approved_by="chris", as_of="2026-12-31", conn=conn)
    a = reg.current_allocation(conn=conn)
    assert a["weights"]["long"] == pytest.approx(0.1) and a["weights"]["treasury"] == pytest.approx(0.9)
    assert conn.execute("SELECT COUNT(*) FROM sleeve_allocation").fetchone()[0] == 8
