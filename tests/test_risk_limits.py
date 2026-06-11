"""The G1 regression tests: one cap, one source of truth, sane values."""

from core import risk_limits


def test_single_position_cap_is_sane():
    assert 0 < risk_limits.MAX_SINGLE_POSITION_PCT <= 0.10


def test_deploy_cap_is_sane():
    assert risk_limits.MAX_SINGLE_POSITION_PCT < risk_limits.MAX_DEPLOY_PCT <= 0.80


def test_manual_path_shares_the_same_cap_object():
    """alpaca_client must import the cap from risk_limits, not define its own."""
    from core import alpaca_client

    assert alpaca_client.MAX_SINGLE_POSITION_PCT is risk_limits.MAX_SINGLE_POSITION_PCT


def test_trader_has_no_local_cap_constant():
    """The 25%-vs-8% contradiction must not come back as a literal in trader.py.

    Reads the source as text (importing backend.trader pulls the full heavy
    dependency stack, which CI doesn't install).
    """
    import os

    path = os.path.join(os.path.dirname(__file__), "..", "backend", "trader.py")
    with open(path) as f:
        src = f.read()
    assert "MAX_SINGLE_POSITION_PCT = 0.25" not in src
    assert "from core.risk_limits import" in src
