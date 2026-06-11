# WealthIncome — Vision Rethink: the honest path to a "99% winning" trader

**Written:** 2026-06-11, after the month-long churn-bug audit.
**Companion to:** ROADMAP.md (2026-05-11). This memo questions the destination; the roadmap sequences the journey.

---

## 1. The hard truth about "99% win rate"

No honest directional trader on Earth wins 99% of trades. The strategies that *appear* to are the ones that
sell catastrophe insurance — far-out-of-the-money option selling, martingale sizing. They win 99 small bets,
then the 100th erases years. That's not a trading system; it's a time bomb with a smooth equity curve.

What the best actually do:

- **Renaissance Medallion** — ~50.75% per-trade win rate. 66%/yr for three decades. The edge per bet is
  microscopic; the magic is *thousands of uncorrelated small bets*, sizing, and costs.
- **Citadel / Jane Street** — high *daily* win rates from market-making and arbitrage: thousands of tiny
  edges, massive diversification, ruthless risk caps. Per-position win rate is unremarkable.

**The honest reframe: stop optimizing winning *trades*; optimize winning *months*.**
Medallion's real signature wasn't per-trade accuracy — it was essentially never having a losing quarter.
"99% winning months" is achievable engineering: many small uncorrelated positive-expectancy bets + hard
loss caps. "99% winning trades" is either fraud, luck, or a steamroller waiting.

The metric that compounds: **expectancy × frequency × capital deployed**, with drawdown capped.

---

## 2. What we have today (honest scorecard, post-audit 2026-06-11)

| Dimension | State |
|---|---|
| Equity | $100k → ~$104.6k since Apr 14 (+4.6%, ~28%/yr pace) — but most of it from one April cohort's recovery |
| Realized expectancy | **Negative**: 8 wins / 31 closed (26%), avg win $289 vs avg loss $168 → ≈ −$49/trade |
| Fill rate | **~50% of entries never fill** (IOC limit cancels) — half the intended strategy doesn't exist |
| Deployment | 2 positions, ~84% cash — even perfect picks can't compound at this utilization |
| Concurrency | 2–6 positions max — monthly P&L is a coin flip, not a law-of-large-numbers curve |
| Decision engine | Claude reading indicators per cycle = discretionary trading with extra steps; reasoning is articulate but base rates are unmeasured |
| Slippage/attribution | Fields exist in DB, all NULL — we cannot say *why* we win or lose |
| Ops | Now solid: launchd-supervised, health-monitored, file-limit fixed, error monitors armed. One month (May 13–Jun 11) was lost to the churn bug and **nothing alerted us** |
| Cost | ~$7/day brain on subscription — genuinely clever and cheap |

What's genuinely good and worth keeping: the hard-guard philosophy (fail closed, code beats config), the
catalyst-tier risk framework, the enricher stack (options flow, EDGAR insiders, macro, news), the
validation agent, the SQLite everything-ledger, the self-calibration seed (performance_intelligence),
and — as of today — the supervision layer.

---

## 3. Gaps between today and even the *current* vision ($100k → $1M)

1. **No telling-Chris layer.** The system degraded for a month silently. Daily P&L digest + anomaly
   alerts to Slack/phone are prerequisite to trusting it with real money.
2. **No measurement substrate.** Win rate by signal, by regime, by hold time, by exit reason; live-vs-expected
   slippage — none of it is queryable. We can't improve what we can't attribute.
3. **Execution leaks.** 50% fill rate means the measured track record describes *half* the strategy.
   Marketable-limit retry ladder needed; fill-rate SLO ≥95%.
4. **Structural timidity.** 8%/25% caps + calendar blocks + 2 positions = the system mostly holds cash.
   Safe, but $100k→$1M needs deployed capital earning edge, not parked cash avoiding mistakes.
5. **Paper-fill optimism.** Alpaca paper fills are kinder than reality; any track record needs a live
   shadow account before belief.
6. **PDT guard** still unbuilt (required under $25k live).

---

## 4. The clean-sheet design (if we scrapped it): the Casino Model

You asked what I'd build for "99% winning." I'd build the casino, not the gambler — the house wins
99% of *months* without winning 99% of *hands*. Five layers:

**L1 — Edge library (replaces per-cycle stock-picking).**
A catalogue of event-driven edges, each with a *backtested base rate* before it trades a dollar:
post-earnings-announcement drift, insider cluster-buy follow-through (we already parse EDGAR),
gap-fill statistics, index-rebalance flows, options-flow follow-through, oversold-quality mean reversion.
Each edge ships with: historical win rate, expectancy after costs, capacity, decay half-life.
**Nothing trades without a measured base rate ≥ ~60% and positive EV after slippage.**

**L2 — Portfolio engine (replaces 2-position concentration).**
20–40 simultaneous positions, 0.5–2% risk each, quarter-Kelly sizing, correlation cap per cluster,
**daily loss circuit breaker** and per-edge exposure caps. This is where "99% winning months" is
actually manufactured: diversification across many small independent bets makes the monthly
aggregate nearly deterministic while any single trade stays a coin flip.

**L3 — Execution layer that actually fills.**
Marketable limit ladder with timed retries, fill-rate and slippage SLOs, every fill reconciled and
attributed. (The DB columns already exist — they're just never populated.)

**L4 — Claude's real job: research analyst + risk officer, not stock picker.**
The audit shows it: articulate per-cycle reasoning produced negative realized expectancy. LLMs are
mediocre at "is this chart going up" and *excellent* at: reading filings/news for disqualifying
catalysts, proposing new edge hypotheses for the backtester, sizing risk narratives, and writing
post-mortems. Stats pick entries; Claude vetoes, sizes, researches, and reports. The validation
agent grows into this risk-officer role.

**L5 — Measurement & decay layer.**
Live-vs-backtest attribution per edge, weekly; an edge whose live stats fall below its backtest
confidence band gets auto-benched. Edges die — the system must notice before the P&L does.

**What survives from today's build:** Alpaca client, SQLite ledger, guards, catalyst tiers, enricher
stack (becomes L1 inputs), validation agent (becomes L4), launchd/health ops, the $7/day brain.
**What gets retired:** Claude-decides-entries-from-indicators as the core loop.

---

## 5. Recommendation: don't scrap — transplant the heart

The chassis (ops, guards, ledger, data feeds) is exactly what the Casino Model needs; rewriting it
would burn a month to arrive where we already are. The migration:

- **Phase A (≈2 wks): Measurement first.** Populate slippage/attribution, build the per-edge/per-signal
  scorecard, daily Slack digest, decay alarms. Cost: small. Payoff: every later decision is evidence-based.
- **Phase B (≈4–6 wks): Edge library on paper, A/B.** Build 3–4 edges with backtests; run them as a
  second paper strategy beside the current brain. Same capital, same dashboard, two columns.
- **Phase C: Allocate by evidence.** Whichever engine shows better risk-adjusted expectancy gets the
  capital. My bet is the edge library wins and Claude moves to research/risk — but the A/B decides, not me.

This sequence also closes ROADMAP Phase 1's P0 items along the way (PDT guard, reconciliation,
alerting) so the live-money gate keeps getting closer while we rebuild the decision core.

---

*One-line version: stop trying to be a 99% gambler; build the house — many small measured edges,
brutal risk caps, and a brain that does research instead of guessing charts.*
