"""Regression: signal_summary list-vs-dict crash (TGT, 2026-06-11 10:03).

indicators.compute_all() emits signal_summary as a list; the execution path
must coerce it to a dict before .update() — the crash left a filled position
with no lifecycle row and no trailing stop.
"""

import os


def _coerce(signal_summary):
    """Mirror of the coercion logic in backend/trader.py execute_decision."""
    if isinstance(signal_summary, list):
        return {"signals": signal_summary}
    if not isinstance(signal_summary, dict):
        return {}
    return signal_summary


def test_list_coerces_to_dict_and_updates():
    s = _coerce(["Price above all MAs (bullish structure)", "RSI in range"])
    s.update({"entry_rsi": 55})
    assert s["signals"] and s["entry_rsi"] == 55


def test_none_and_garbage_coerce_to_empty_dict():
    assert _coerce(None) == {}
    assert _coerce("weird") == {}
    d = {"already": "dict"}
    assert _coerce(d) is d


def test_trader_source_contains_the_coercion():
    path = os.path.join(os.path.dirname(__file__), "..", "backend", "trader.py")
    src = open(path).read()
    assert 'isinstance(signal_summary, list)' in src
    assert 'signal_summary = sym_data.get("signal_summary") or {}' not in src
