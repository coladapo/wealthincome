"""Catalyst tier classification — sizing/stop behavior around binary events.

Asserts the behavioral contract (block / suspend / size flags) rather than
internal tier numbers.
"""

from core.catalyst_risk import assess_catalyst_risk


def _assess(days):
    return assess_catalyst_risk("TEST", days_until_earnings=days,
                                fomc_dates=[], cpi_dates=[], nfp_dates=[],
                                check_ex_dividend=False)


def test_clear_when_no_events():
    risk = _assess(None)
    assert risk.tier == 0
    assert not risk.block_new_entry
    assert risk.position_size_multiplier == 1.0


def test_earnings_today_blocks_entry():
    risk = _assess(0)
    assert risk.block_new_entry
    assert risk.suspend_trailing_stop


def test_earnings_within_week_suspends_stop_and_reduces_size():
    risk = _assess(5)
    assert not risk.block_new_entry
    assert risk.suspend_trailing_stop
    assert risk.position_size_multiplier < 1.0


def test_earnings_in_two_weeks_reduces_size_only():
    risk = _assess(10)
    assert not risk.block_new_entry
    assert not risk.suspend_trailing_stop
    assert risk.position_size_multiplier < 1.0


def test_far_earnings_is_clear():
    risk = _assess(30)
    assert risk.tier == 0
    assert risk.position_size_multiplier == 1.0
