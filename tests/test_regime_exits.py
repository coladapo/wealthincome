"""Regime-conditional exit gate — conservative by construction."""

from core.risk_limits import preemptive_exits_active, STRONG_BULL_MIN_SCORE


def test_strong_bull_disarms():
    assert preemptive_exits_active("BULL", STRONG_BULL_MIN_SCORE) is False
    assert preemptive_exits_active("BULL", 95) is False


def test_weak_bull_keeps_insurance():
    assert preemptive_exits_active("BULL", STRONG_BULL_MIN_SCORE - 1) is True


def test_caution_and_bear_keep_insurance():
    assert preemptive_exits_active("CAUTION", 99) is True
    assert preemptive_exits_active("BEAR", 99) is True


def test_unknown_or_garbage_defaults_to_armed():
    assert preemptive_exits_active(None, None) is True
    assert preemptive_exits_active("", 80) is True
    assert preemptive_exits_active("BULL", None) is True
    assert preemptive_exits_active("BULL", "not-a-number") is True


def test_case_insensitive_and_string_scores():
    assert preemptive_exits_active("bull", "85") is False
