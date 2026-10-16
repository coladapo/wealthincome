"""Opening-range breakout: range, breakout trigger, stop, filters; plus an end-to-end run of the
intraday study on synthetic sessions (baseline, SPY hold, variant log). Offline."""

import numpy as np
import pandas as pd
import pytest

from research.edges import opening_range_breakout as orb
from research.intraday_study import (HORIZONS, intraday_bar, log_variant, run_study,
                                     variants_tried)
from tests.intraday_synth import session


def ranged(**over):
    """Range 09:30-09:44: highs up to 101, lows down to 99, volume 1000; afterwards flat 100."""
    base = {"high": {0: 101.0, 7: 100.8}, "low": {3: 99.0}, "close": {}, "volume": {}}
    sb = session(vol=1000)
    for k, mv in base.items():
        for m, v in mv.items():
            getattr(sb, k)[m] = v
    for k, mv in over.items():
        for m, v in mv.items():
            getattr(sb, k)[m] = v
    return sb


def test_range_and_breakout_with_volume_confirmation():
    sb = ranged(close={30: 101.2, 40: 101.5}, high={30: 101.3, 40: 101.6}, volume={30: 1400, 40: 2000})
    s = orb.signal(sb)
    assert s["range_hi"] == 101.0 and s["range_lo"] == 99.0 and s["range_vol"] == pytest.approx(1000)
    assert s["signal_idx"] == 40                    # bar 30 closed above but on 1.4x volume
    assert s["stop"] == pytest.approx(101.5 * 0.99)  # tighter than the range low


def test_stop_is_range_low_when_tighter():
    sb = ranged(low={m: 100.9 for m in range(15)}, close={20: 101.2}, volume={20: 1600})
    s = orb.signal(sb)
    assert s["signal_idx"] == 20 and s["stop"] == pytest.approx(100.9)


def test_no_signal_without_close_above_range_or_after_flat():
    assert orb.signal(ranged(high={50: 102.0}, volume={50: 5000})) is None      # wick only, no close
    assert orb.signal(ranged(close={384: 102.0}, volume={384: 5000})) is None   # too late to enter
    assert orb.signal(ranged(close={10: 102.0}, high={10: 102.0}, volume={10: 5000})) is None  # inside range


def test_thirty_minute_range():
    sb = ranged(high={20: 101.4}, close={25: 101.2, 45: 101.6}, volume={25: 2000, 45: 2000})
    assert orb.signal(sb)["signal_idx"] == 25
    s30 = orb.signal(sb, range_minutes=30)
    assert s30["range_hi"] == 101.4 and s30["signal_idx"] == 45
    assert s30["range_vol"] == pytest.approx((29 * 1000 + 2000) / 30)


def test_min_range_filter():
    sb = ranged(close={30: 101.2}, volume={30: 2000})              # range 2/99 = 2.0%
    assert orb.signal(sb, min_range_pct=0.005) is not None
    assert orb.signal(sb, min_range_pct=0.03) is None


def test_spy_vwap_filter_has_no_look_ahead():
    sb = ranged(close={30: 101.2, 40: 101.3}, volume={30: 2000, 40: 2000})
    spy = session("SPY")
    spy.close[:35] = 99.0          # below its VWAP (100) until minute 35
    spy.close[35:] = 101.0
    ok = orb.spy_above_vwap(spy)
    assert not ok[30] and ok[40]
    assert orb.signal(sb, ok, spy_vwap_filter=True)["signal_idx"] == 40
    assert orb.signal(sb, None, spy_vwap_filter=True) is None


def _synthetic_market(n_days=12, drift=True):
    """Every symbol breaks out at minute 30 on 2x volume; with drift, it then trends up."""
    paths, spy, rows, days = {}, {}, [], pd.bdate_range("2025-02-03", periods=n_days).strftime("%Y-%m-%d")
    for d in days:
        spy[d] = session("SPY", d, vol=1e6)
        for s in ("AAA", "BBB", "CCC"):
            sb = session(s, d, vol=1e5)
            sb.high[0], sb.low[3] = 101.0, 99.0
            sb.close[30], sb.high[30], sb.volume[30] = 101.2, 101.2, 3e5
            if drift:
                path = 101.2 + np.arange(390 - 31) * 0.01
                sb.open[31:] = sb.close[31:] = sb.low[31:] = path
                sb.high[31:] = path + 0.005
            sg = orb.signal(sb)
            rows.append({"symbol": s, "date": d, **sg})
            paths[(s, d)] = sb
    return pd.DataFrame(rows), paths, spy, list(days)


def test_study_end_to_end_baseline_spy_and_bar(tmp_path):
    ev, paths, spy, days = _synthetic_market()
    res = run_study(ev, paths, spy, days, baseline_min_idx=15, n_boot=200)
    assert {r["horizon"] for r in res.rows} == {str(h) for h in HORIZONS}
    al = res.get("all", "strategy")
    assert al["n"] == len(res.sim.trades) == 36
    assert al["mean"] > 0 and al["diff_spy"] > 0             # SPY is flat, the names trend
    assert al["diff_base"] > 0                               # the breakout entry is earlier than most random draws
    assert res.get("stress x2", "60")["mean"] < res.get("all", "60")["mean"]
    assert len(res.sim_stress.trades) == 36
    assert res.params["holdout_start"] == days[int(len(days) * 0.6)]
    ho = res.get("holdout", "60")
    assert ho["n"] == 3 * (len(days) - int(len(days) * 0.6))
    bar = intraday_bar(res)
    assert bar["strategy"]["n>=300"] is False and bar["strategy"]["CLEARS BAR"] is False
    db = str(tmp_path / "intraday.db")
    assert log_variant("orb_test", {"a": 1}, res, db) == 1
    assert log_variant("orb_test", {"a": 1}, res, db) == 1    # same params: not a new variant
    assert log_variant("orb_test", {"a": 2}, res, db) == 2
    assert variants_tried("orb_test", db_path=db) == 2


def test_study_fixed_horizon_returns_pay_costs_both_sides():
    ev, paths, spy, days = _synthetic_market(n_days=2, drift=False)
    res = run_study(ev, paths, spy, days, n_boot=50)
    r5 = res.events["ret_5"].values
    c = res.events["cost_per_side"].values
    np.testing.assert_allclose(r5, 100.0 * (1 - c) / (100.0 * (1 + c)) - 1)   # flat price: pure cost
    np.testing.assert_allclose(res.events["spy_5"].values, 0.0)


def test_baseline_never_draws_before_the_signal_bar():
    """A random entry before the signal would 'know' the run-up that produced the breakout."""
    from research.intraday_study import ZERO_COST, event_returns

    sb = session("AAA", "2025-02-03")
    ramp = np.r_[np.linspace(100, 110, 81), np.full(390 - 101, 110.0)]
    sb.open[20:] = sb.close[20:] = sb.high[20:] = sb.low[20:] = ramp
    ev = pd.DataFrame([{"symbol": "AAA", "date": "2025-02-03", "signal_idx": 200, "stop": 105.0}])
    out = event_returns(ev, {("AAA", "2025-02-03"): sb}, {"2025-02-03": session("SPY", "2025-02-03")},
                        ZERO_COST, baseline_min_idx=15, n_base=50)
    assert out["base_close"].iloc[0] == pytest.approx(0.0)    # flat after the signal: no run-up leaks in
    assert out["ret_close"].iloc[0] == pytest.approx(0.0)
