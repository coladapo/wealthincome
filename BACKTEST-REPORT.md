# Backtest grid — rule variants vs live baseline

**Run:** 2026-06-11 · 3yr daily bars · 53-symbol universe · per-symbol replay, entries at signal close (uniform across variants — relative numbers are the signal, absolute numbers are optimistic vs real fills).

| Variant | Trades | Win % | Expectancy %/trade | PF | Avg win | Avg loss | Hold (d) | Worst |
|---|---|---|---|---|---|---|---|---|
| no_breach_no_collapse | 377 | 44.8 | 6.463 | 2.59 | 23.48 | -7.37 | 76.4 | -18.24 |
| tight_rsi_no_breach | 613 | 42.7 | 3.494 | 2.11 | 15.55 | -5.51 | 40.5 | -18.24 |
| no_breach_exit | 619 | 42.3 | 3.422 | 2.06 | 15.74 | -5.62 | 40.7 | -18.24 |
| breach_3bars | 799 | 34.8 | 2.57 | 1.98 | 14.91 | -4.02 | 29.9 | -18.24 |
| trail_tight_1.5x | 874 | 33.2 | 2.477 | 2.04 | 14.65 | -3.57 | 26.6 | -12.0 |
| rsi_45_65 | 865 | 33.3 | 2.387 | 2.0 | 14.31 | -3.56 | 26.5 | -18.24 |
| live_baseline | 870 | 33.1 | 2.361 | 1.97 | 14.48 | -3.63 | 26.8 | -18.24 |
| no_grace_window | 870 | 33.1 | 2.361 | 1.97 | 14.48 | -3.63 | 26.8 | -18.24 |
| rsi_50_70 | 870 | 33.2 | 2.349 | 1.97 | 14.38 | -3.63 | 26.7 | -18.24 |
| no_momentum_collapse | 861 | 32.5 | 2.347 | 1.96 | 14.73 | -3.62 | 27.2 | -18.24 |
| trail_wide_3.5x | 855 | 32.6 | 2.217 | 1.91 | 14.29 | -3.63 | 27.3 | -19.55 |
| rsi_40_55 | 577 | 31.0 | 1.84 | 1.79 | 13.48 | -3.39 | 22.7 | -19.24 |

## Live baseline exit-reason breakdown

- **momentum_collapse**: 147 trades, 56% wins, 517.31% total
- **sma50_breach**: 590 trades, 24% wins, 479.5% total
- **trailing_stop**: 131 trades, 47% wins, 940.38% total
- **catastrophic_dd**: 2 trades, 100% wins, 116.61% total

## Bear-window split (2021-06 → 2023-06, includes the 2022 bear)

| Variant | Trades | Win % | Expectancy %/trade | PF | Worst |
|---|---|---|---|---|---|
| live_baseline | 600 | 24.0 | -0.786 | 0.74 | -14.77 |
| no_breach_exit | 433 | 29.1 | -1.230 | 0.71 | -16.36 |
| no_breach_no_collapse | 302 | 29.8 | -2.103 | 0.62 | -16.97 |

**The decisive finding:** dropping the preemptive exits raises win rate in ALL regimes but
flips expectancy hard negative in bears — they are crash insurance, not dead weight.
Win rate alone is a vanity metric; expectancy splits the truth.

## Recommendation — regime-conditional exits

- **BULL regime:** disable sma50_breach + momentum_collapse exits; trailing stop + 15%
  catastrophic stop do the exiting. (3yr bull-leaning window: 44.8% wins, +6.46%/trade, PF 2.59
  vs baseline 33.1%, +2.36%, PF 1.97.)
- **CAUTION/BEAR regime:** keep both preemptive exits (and the regime gate already throttles
  entries). Every variant loses money in bear windows — long-only momentum shouldn't trade them.
- Implementation: the reconciler's SMA50 monitor and the prompt's exit rules consult the
  current regime (already computed every cycle).

*Caveats: per-symbol replay, optimistic same-close fills, no portfolio caps — relative
comparisons are the signal, absolute numbers flatter reality.*
