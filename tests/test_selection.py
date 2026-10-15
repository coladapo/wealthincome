"""Deflated Sharpe ratio and PBO (CSCV) against known inputs. Offline."""

import math

import numpy as np
import pytest

from research import selection as sel


def test_psr_is_half_when_sharpe_equals_benchmark():
    assert sel.probabilistic_sharpe(0.1, 0.1, 500) == pytest.approx(0.5)
    assert sel.probabilistic_sharpe(0.1, 0.0, 500) > 0.95


def test_expected_max_sharpe_grows_with_trials():
    assert sel.expected_max_sharpe(1, 0.01) == 0.0
    vals = [sel.expected_max_sharpe(n, 0.01) for n in (2, 10, 100, 1000)]
    assert all(a < b for a, b in zip(vals, vals[1:]))


def test_dsr_matches_bailey_lopez_de_prado_example():
    # Paper's numerical example: annualised SR 2.5 over T=1250 daily obs, N=100 trials,
    # V[SR] = 0.5 annualised, skew -3, kurtosis 10 -> SR0 ~= 0.1132 daily, DSR ~= 0.90.
    sr = 2.5 / math.sqrt(250)
    sr0 = sel.expected_max_sharpe(100, 0.5 / 250)
    assert sr0 == pytest.approx(0.1132, abs=5e-4)
    dsr = sel.probabilistic_sharpe(sr, sr0, 1250, skew=-3.0, kurt=10.0)
    assert dsr == pytest.approx(0.90, abs=0.01)


def test_deflated_sharpe_falls_as_trials_rise():
    rng = np.random.default_rng(3)
    r = rng.normal(0.0008, 0.01, 1000)
    one = sel.deflated_sharpe(r, n_trials=1)
    many = sel.deflated_sharpe(r, n_trials=50)
    assert one["sr0"] == 0.0 and one["dsr"] == pytest.approx(one["psr0"])
    assert many["dsr"] < one["dsr"]
    given = sel.deflated_sharpe(r, n_trials=10, trial_sharpes=[0.01, 0.05, 0.03])
    assert given["var_source"].startswith("sample variance")


def test_pbo_exact_two_block_examples():
    # 2 blocks x 2 obs, 4 trials. Overfit case: trial 0 is best in block 0 but worst in
    # block 1, trial 3 the reverse, so each split's in-sample winner is last out of sample.
    blk0 = np.array([[0.03, 0.01, 0.0, -0.02], [0.05, 0.02, 0.01, -0.01]])
    blk1 = np.array([[-0.02, 0.0, 0.01, 0.03], [-0.01, 0.01, 0.02, 0.05]])
    out = sel.pbo_cscv(np.vstack([blk0, blk1]), n_blocks=2)
    assert out["n_splits"] == 2 and out["pbo"] == 1.0
    # persistent case: trial 0 best in both blocks -> never overfit
    good = np.vstack([blk0, blk0 + np.array([0, 0.0001, 0.0002, 0.0003])])
    assert sel.pbo_cscv(good, n_blocks=2)["pbo"] == 0.0


def test_pbo_high_when_in_sample_winner_tends_to_lose_out_of_sample():
    # trial A earns in blocks 0-7 and loses in 8-15; B is the mirror image.
    block = 10
    a = np.concatenate([np.full(block, 0.01)] * 8 + [np.full(block, -0.01)] * 8)
    noise = np.random.default_rng(0).normal(0, 1e-4, (len(a), 4))
    M = np.column_stack([a, -a, a, -a]) + noise
    out = sel.pbo_cscv(M, n_blocks=16)
    assert out["n_splits"] == math.comb(16, 8)
    assert out["pbo"] > 0.7


def test_pbo_zero_with_a_genuinely_better_trial():
    rng = np.random.default_rng(1)
    M = rng.normal(0, 0.01, (800, 6))
    M[:, 2] += 0.01                                   # one trial with large true drift
    assert sel.pbo_cscv(M)["pbo"] == pytest.approx(0.0, abs=0.01)


def test_pbo_near_half_for_pure_noise():
    rng = np.random.default_rng(2)
    vals = [sel.pbo_cscv(rng.normal(0, 0.01, (480, 10)), n_blocks=8)["pbo"] for _ in range(6)]
    assert 0.25 < float(np.mean(vals)) < 0.75


def test_pbo_needs_four_trials():
    assert sel.pbo_cscv(np.zeros((100, 3)))["pbo"] is None
