"""Strict intraday simulator: next-bar fills, spread + slippage, 10%-of-volume partial fills,
flat by 15:55, per-trade risk cap, daily loss stop. Offline, synthetic bars."""

import numpy as np
import pandas as pd
import pytest

from research.intraday_sim import CostModel, SimConfig, estimate_spread, simulate
from tests.intraday_synth import session

D = "2025-01-02"


def sig(*rows):
    return pd.DataFrame([{"symbol": s, "date": D, "signal_idx": i, "stop": st} for s, i, st in rows])


def run(sbs, signals, **cfg):
    return simulate(signals, {(sb.symbol, sb.date): sb for sb in sbs}, SimConfig(**cfg))


def test_spread_estimate_and_cost_model():
    sb = session(range_bp=10)
    assert estimate_spread(sb) == pytest.approx(10e-4)
    assert CostModel().per_side(sb) == pytest.approx(5e-4 + 1e-4)          # half spread + 1 bp
    assert CostModel().stressed(2).per_side(sb) == pytest.approx(2 * 6e-4)  # 2x spread and 2x slippage
    assert CostModel().per_side(session(range_bp=0)) == pytest.approx(1e-4 + 1e-4)  # 2 bp floor
    later_wide = session(range_bp=0, high={100: 200.0})                    # only the first 5 minutes count
    assert estimate_spread(later_wide) == pytest.approx(2e-4)


def test_entry_at_next_bar_open_with_costs_and_flat_by_1555():
    sb = session(range_bp=10, open={21: 100.5})
    r = run([sb], sig(("AAA", 20, 99.0)))
    t = r.trades.iloc[0]
    assert t.entry_idx == 21 and t.exit_idx == 385                      # 15:55 is minute 385
    assert t.avg_entry == pytest.approx(100.5 * 1.0006)                 # never the signal bar's close
    assert t.avg_exit == pytest.approx(100.0 * 0.9994)
    assert t.exit_reason == "flat" and not t.forced
    assert t.ret == pytest.approx(100 * 0.9994 / (100.5 * 1.0006) - 1)


def test_stress_variant_doubles_costs():
    sb = session(range_bp=10)
    base = run([sb], sig(("AAA", 20, 99.0))).trades.iloc[0]
    st = simulate(sig(("AAA", 20, 99.0)), {("AAA", D): sb},
                  SimConfig(cost=CostModel().stressed(2.0))).trades.iloc[0]
    assert st.cost_per_side == pytest.approx(2 * base.cost_per_side)
    assert st.ret < base.ret


def test_missing_next_bar_waits_for_the_next_print():
    sb = session(open={21: np.nan}, volume={21: 0})
    t = run([sb], sig(("AAA", 20, 99.0))).trades.iloc[0]
    assert t.entry_idx == 22


def test_partial_fills_cap_each_bar_at_10pct_of_volume_and_roll():
    sb = session(vol=1000)                                  # 100 shares per bar
    t = run([sb], sig(("AAA", 20, 99.02))).trades.iloc[0]   # risk 250 / (100.02-99.02) = 250 shares
    assert t.intended == 250 and t.filled == 250
    assert t.entry_idx == 21                                # 100 @21, 100 @22, 50 @23
    assert t.exit_idx == 387                                # exits roll too: 385, 386, 387


def test_unfilled_entry_cancelled_when_exit_triggers():
    sb = session(vol=1000, low={21: 99.0})                  # stop touched on the first fill bar
    t = run([sb], sig(("AAA", 20, 99.02))).trades.iloc[0]
    assert t.filled == 100 and t.intended == 250
    assert t.exit_reason == "stop" and t.exit_idx == 22


def test_unfilled_entry_cancelled_at_flat_time():
    sb = session(vol=10)                                    # 1 share per bar
    t = run([sb], sig(("AAA", 380, 99.02))).trades.iloc[0]
    assert t.filled == 4 and t.intended == 250              # minutes 381..384, nothing from 15:55 on


def test_exit_that_cannot_fill_is_forced_at_last_close():
    sb = session(vol=10, volume={m: 0 for m in range(385, 390)})
    t = run([sb], sig(("AAA", 380, 99.02))).trades.iloc[0]
    assert t.forced


def test_per_trade_risk_cap():
    sb = session()
    t = run([sb], sig(("AAA", 20, 99.02))).trades.iloc[0]
    assert t.filled == 250
    assert t.risk_frac == pytest.approx(0.0025, rel=1e-3)


def test_gross_cap_limits_tight_stops():
    sb = session()
    t = run([sb], sig(("AAA", 20, 99.99))).trades.iloc[0]   # risk sizing alone would buy ~8000 shares
    assert t.intended == int(100_000 // (100 * 1.0002))


def test_stop_executes_at_next_open_not_at_stop_price():
    sb = session(low={30: 98.9}, open={31: 98.5})
    t = run([sb], sig(("AAA", 20, 99.02))).trades.iloc[0]
    assert t.exit_reason == "stop" and t.exit_idx == 31
    assert t.avg_exit == pytest.approx(98.5 * 0.9998)
    assert t.r_mult < -1                                    # gapped through the stop


def test_targets_scale_out_half_at_1r_rest_at_2r():
    sb = session(high={40: 101.5, 50: 102.5}, open={41: 101.4, 51: 102.4})
    t = run([sb], sig(("AAA", 20, 99.02))).trades.iloc[0]   # R = 1.00 from the 100.02 fill
    assert t.exit_reason == "target1R+target2R" and t.exit_idx == 51
    exp = (125 * 101.4 + 125 * 102.4) * 0.9998 / 250
    assert t.avg_exit == pytest.approx(exp)


def test_stop_wins_when_stop_and_target_touch_same_bar():
    sb = session(high={40: 103.0}, low={40: 98.0})
    t = run([sb], sig(("AAA", 20, 99.02))).trades.iloc[0]
    assert t.exit_reason == "stop"


def test_daily_loss_stop_blocks_new_entries():
    a = session("AAA", low={30: 94.0}, close={30: 95.0}, open={31: 95.0})   # ~ -1.26% of the account
    b = session("BBB")
    r = run([a, b], sig(("AAA", 20, 99.02), ("BBB", 40, 99.02)))
    assert list(r.trades["symbol"]) == ["AAA"]
    assert r.skipped["after_loss_stop"] == 1
    assert bool(r.daily.iloc[0]["loss_stop_hit"])
    assert r.daily.iloc[0]["ret"] < -0.01


def test_no_signal_late_enough_to_miss_the_flat_time():
    r = run([session()], sig(("AAA", 384, 99.02)))
    assert r.trades.empty and r.skipped["too_late"] == 1


def test_equity_series_and_compounding():
    s1 = session(d="2025-01-02", high={40: 101.5, 50: 102.5})
    s2 = session(d="2025-01-03")
    sigs = pd.DataFrame([{"symbol": "AAA", "date": "2025-01-02", "signal_idx": 20, "stop": 99.02},
                         {"symbol": "AAA", "date": "2025-01-03", "signal_idx": 20, "stop": 99.02}])
    r = simulate(sigs, {("AAA", s1.date): s1, ("AAA", s2.date): s2}, SimConfig(),
                 sessions=["2025-01-02", "2025-01-03", "2025-01-06"])
    assert len(r.daily) == 3 and r.daily.iloc[2]["n_trades"] == 0
    d1 = r.equity[r.equity["date"] == "2025-01-02"]
    assert len(d1) == 390 and d1["equity"].iloc[-1] == pytest.approx(r.daily.iloc[0]["end_equity"])
    assert r.daily.iloc[1]["start_equity"] == pytest.approx(r.daily.iloc[0]["end_equity"])
    assert r.summary()["n_trades"] == 2
