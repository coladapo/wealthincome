"""Selection-adjusted statistics: how much of a backtest is just the best of many tries.

Probabilistic Sharpe Ratio (Bailey & Lopez de Prado 2012)
    PSR(SR*) = Phi( (SR - SR*) * sqrt(T - 1) / sqrt(1 - g3*SR + (g4 - 1)/4 * SR^2) )
  SR = per-period (daily, not annualised) Sharpe of the observed returns, T = number
  of returns, g3 = skewness, g4 = kurtosis (NOT excess; normal = 3). PSR is the
  probability that the true Sharpe exceeds the benchmark SR*.

Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014, "The Deflated Sharpe Ratio")
    SR0 = sqrt(V[SR_n]) * ( (1 - gamma) * Z^-1(1 - 1/N) + gamma * Z^-1(1 - 1/(N e)) )
    DSR = PSR(SR0)
  N = number of independent trials run for the hypothesis, V[SR_n] = variance of the
  trials' (per-period) Sharpe ratios, gamma = Euler-Mascheroni (0.5772...), Z^-1 the
  standard-normal quantile. SR0 is the expected MAXIMUM Sharpe of N skill-less
  trials; the DSR is the probability the observed Sharpe beats that, i.e. that it
  is not just the luckiest of N. N = 1 gives SR0 = 0 (DSR = PSR(0)).
  V[SR_n]: the sample variance of the trials' Sharpes when >= 2 are known; otherwise
  the estimator's own asymptotic variance (1 - g3*SR + (g4 - 1)/4 * SR^2) / (T - 1)
  (stated in the output as var_source). Paper's example: SR 2.5 annualised (daily,
  T = 1250), N = 100, V = 0.5 annualised, skew -3, kurtosis 10 -> SR0 = 0.1132 daily,
  DSR = 0.90 (tested).

Probability of Backtest Overfitting via CSCV (Bailey, Borwein, Lopez de Prado &
Zhu 2015, "The Probability of Backtest Overfitting")
  M = T x N matrix of per-period returns of the N trials. Split the rows into S
  (default 16) contiguous blocks. For each of the C(S, S/2) ways of picking half the
  blocks as in-sample (IS; the rest out-of-sample, OOS):
     n* = the trial with the best IS Sharpe
     w  = rank of n*'s OOS Sharpe among all N OOS Sharpes / (N + 1)   (rank 1 = worst)
     lambda = ln(w / (1 - w))
  PBO = share of splits with lambda <= 0, i.e. the IS winner is at or below the OOS
  median. ~0 = selection carries over out of sample; ~0.5 = selection is noise;
  ~1 = what wins in-sample loses out-of-sample. Needs >= 4 trials here.
"""

from __future__ import annotations

import math
from itertools import combinations
from statistics import NormalDist
from typing import Dict, Optional, Sequence

import numpy as np
import pandas as pd

EULER_GAMMA = 0.5772156649015329
_N = NormalDist()
MIN_TRIALS_PBO = 4


def sharpe(r: np.ndarray) -> float:
    r = np.asarray(r, dtype=float)
    r = r[~np.isnan(r)]
    if len(r) < 2:
        return float("nan")
    sd = r.std(ddof=1)
    return float(r.mean() / sd) if sd > 0 else float("nan")


def moments(r: np.ndarray):
    """(skewness, kurtosis) -- population moments, kurtosis NOT excess (normal = 3)."""
    r = np.asarray(r, dtype=float)
    r = r[~np.isnan(r)]
    m = r.mean()
    s = r.std(ddof=0)
    if s == 0 or len(r) < 3:
        return 0.0, 3.0
    z = (r - m) / s
    return float((z ** 3).mean()), float((z ** 4).mean())


def probabilistic_sharpe(sr: float, sr_bench: float, T: int, skew: float = 0.0,
                         kurt: float = 3.0) -> float:
    denom = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    if T < 2 or not denom > 0 or not math.isfinite(sr):
        return float("nan")
    return float(_N.cdf((sr - sr_bench) * math.sqrt(T - 1) / math.sqrt(denom)))


def expected_max_sharpe(n_trials: int, sr_var: float) -> float:
    """SR0: expected maximum per-period Sharpe of n_trials skill-less trials."""
    if n_trials <= 1 or not sr_var > 0:
        return 0.0
    n = float(n_trials)
    return float(math.sqrt(sr_var) * ((1 - EULER_GAMMA) * _N.inv_cdf(1 - 1 / n)
                                      + EULER_GAMMA * _N.inv_cdf(1 - 1 / (n * math.e))))


def deflated_sharpe(returns: Sequence[float], n_trials: int,
                    trial_sharpes: Optional[Sequence[float]] = None,
                    sr_var: Optional[float] = None, periods_per_year: int = 252) -> Dict:
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    T = len(r)
    sr = sharpe(r)
    g3, g4 = moments(r) if T >= 3 else (0.0, 3.0)
    if sr_var is not None:
        var, src = float(sr_var), "given"
    else:
        ts = np.asarray([x for x in (trial_sharpes or []) if x is not None and math.isfinite(x)], float)
        if len(ts) >= 2:
            var, src = float(ts.var(ddof=1)), f"sample variance of {len(ts)} trial Sharpes"
        else:
            var = (1 - g3 * sr + (g4 - 1) / 4 * sr * sr) / max(T - 1, 1) if math.isfinite(sr) else float("nan")
            src = "estimator variance (fewer than 2 trial Sharpes known)"
    n = max(int(n_trials), 1)
    sr0 = expected_max_sharpe(n, var)
    k = math.sqrt(periods_per_year)
    return {"T": T, "n_trials": n, "sr": sr, "sr_annual": sr * k if math.isfinite(sr) else float("nan"),
            "skew": g3, "kurtosis": g4, "sr_var": var, "var_source": src,
            "sr0": sr0, "sr0_annual": sr0 * k,
            "psr0": probabilistic_sharpe(sr, 0.0, T, g3, g4),
            "dsr": probabilistic_sharpe(sr, sr0, T, g3, g4)}


def _rank_avg(x: np.ndarray, i: int) -> float:
    """1-based rank of x[i] among x (ascending), ties averaged."""
    less = np.sum(x < x[i])
    eq = np.sum(x == x[i])
    return float(less + (eq + 1) / 2.0)


def pbo_cscv(M, n_blocks: int = 16) -> Dict:
    """Probability of Backtest Overfitting by combinatorially symmetric cross-validation."""
    A = M.values if isinstance(M, pd.DataFrame) else np.asarray(M, dtype=float)
    A = np.nan_to_num(A.astype(float), nan=0.0)
    T, N = A.shape
    if N < MIN_TRIALS_PBO:
        return {"pbo": None, "n_trials": N, "reason": f"needs >= {MIN_TRIALS_PBO} trials"}
    if n_blocks % 2 or n_blocks < 2:
        raise ValueError("n_blocks must be an even number >= 2")
    T_use = (T // n_blocks) * n_blocks
    if T_use < 2 * n_blocks:
        return {"pbo": None, "n_trials": N, "reason": "too few periods for the blocks"}
    B = A[T - T_use:].reshape(n_blocks, T_use // n_blocks, N)
    s1 = B.sum(axis=1)            # blocks x N
    s2 = (B ** 2).sum(axis=1)
    cnt = B.shape[1]
    tot1, tot2 = s1.sum(axis=0), s2.sum(axis=0)
    logits = []
    for comb in combinations(range(n_blocks), n_blocks // 2):
        idx = list(comb)
        n_is = cnt * len(idx)
        a1, a2 = s1[idx].sum(axis=0), s2[idx].sum(axis=0)
        b1, b2 = tot1 - a1, tot2 - a2
        n_oos = T_use - n_is

        def _sr(x1, x2, n):
            mu = x1 / n
            var = (x2 - n * mu * mu) / (n - 1)
            with np.errstate(divide="ignore", invalid="ignore"):
                out = np.where(var > 1e-300, mu / np.sqrt(np.maximum(var, 1e-300)), 0.0)
            return out

        r_is, r_oos = _sr(a1, a2, n_is), _sr(b1, b2, n_oos)
        best = int(np.argmax(r_is))
        w = _rank_avg(r_oos, best) / (N + 1.0)
        logits.append(math.log(w / (1 - w)))
    lg = np.asarray(logits)
    return {"pbo": float(np.mean(lg <= 0)), "n_trials": N, "n_splits": int(len(lg)),
            "n_blocks": n_blocks, "median_logit": float(np.median(lg))}
