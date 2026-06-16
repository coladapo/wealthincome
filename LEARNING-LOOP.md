# WealthIncome — How the trader "learns" (and where it's weak)

**Written:** 2026-06-16. Grounded in a live trace of backend/trader.py, core/performance_intelligence.py,
core/trade_analyzer.py, core/trade_rag.py, and the signal_calibration table (63 rows live).

---

## The honest headline

The trader **does** get better at trading *your* book over time — but NOT by training a model.
The brain is Claude, and Claude is **frozen**: no fine-tuning, no weight updates, nothing learned
into the model itself. What improves is the **context** the model reads before each decision. This
is **memory-based / in-context learning**, not recursive self-training. It's real and it compounds,
but it has a hard ceiling that model-retraining wouldn't.

Think of it as a trader who never gets smarter innately, but every morning re-reads a sharper,
more honest file on how *their own* past trades actually worked out.

---

## The three feedback channels that ARE wired into the live prompt

1. **Performance feedback (trade_analyzer → build_feedback_block_for_claude).**
   Before each cycle the prompt is fed real closed-trade stats from your own DB — win rate,
   average win/loss, profit factor. So the model sees "your actual hit rate is X," not a guess.

2. **Trade-history RAG (trade_rag → build_portfolio_rag_block).**
   For each watchlist symbol it retrieves the most *similar* past trades (matched on symbol,
   regime, RSI band, SMA50 posture, volume) and injects how those resolved. "Last 3 times you
   bought this kind of setup, here's what happened." Most-relevant-first, capped at 6k chars.

3. **Signal calibration (performance_intelligence → signal_calibration table, 63 rows).**
   After every position closes, the reconciler recomputes a win rate per *signal scout* (VWAP,
   options flow, insider, earnings, RAG, macro) and a recommended weight (scaled by win rate vs a
   50% benchmark). These become calibration warnings in the prompt — e.g. "you've been
   overconfident on this scout; it's 30% historically." Regime-split (bull/bear win rates) too.

Net effect: the system's *advice to itself* sharpens daily as real outcomes accrue. That's
genuine learning at the decision layer.

## What it is NOT (and should never be claimed as)

- **No model fine-tuning.** Claude's weights never change. "Training on its own data" in the
  literal ML sense is not happening and isn't the design.
- **No automatic rule rewriting.** When calibration says a setup is weak, it *warns* the model;
  it does not silently edit the strategy. Rule changes (like the regime-exit experiment) still
  go through backtest → human review. By design — autonomous rule-rewriting is exactly the
  one-way-door the cross-provider debate said to gate.
- **No reinforcement-learning policy.** There's no reward-maximizing optimizer in the loop.

---

## Where it's weak (the real ceilings)

1. **Data starvation — the dominant limit.** All three channels are statistics over *closed
   trades*, and there are ~35 ever, ~4 since the regime-exit change. Win rates over single-digit
   samples are noise, not signal. The loop can't teach what it hasn't seen enough of. This is THE
   gap — it resolves only with trade volume (and the casino-model's many-small-bets design is
   partly a fix for it).
2. **Survivorship / attribution blur.** It learns from trades it *took*. It has no feedback on
   the good trades it *skipped* (no counterfactual), so it can't learn "you're too cautious."
3. **In-context recency, not permanence.** A lesson only influences a decision if it fits in that
   cycle's prompt. Once the history outgrows the 6k RAG cap, older lessons fall out of view —
   the model doesn't carry them forward the way a fine-tuned weight would.
4. **Calibration lag.** Scout weights update only on position close, so during a long hold the
   model trades on stale calibration.
5. **No cross-validation of its own learning.** It trusts its computed win rates literally; a
   regime shift can make yesterday's calibration actively misleading with no guard.

---

## So: is it "getting better each day"?

Yes — at *applying your specific history*, and that genuinely compounds. No — it is not growing
more intelligent, and it can't learn faster than closed-trade volume allows. The highest-leverage
upgrade is **more trades feeding the loop** (the edge-library / portfolio-of-many-small-bets
direction), not a fancier learning algorithm. A distant option is periodic fine-tuning of a local
model on the trade ledger — but that's a different, heavier architecture and premature until the
strategy itself is proven and trade volume is real.

*One line: it learns like a disciplined journal-keeper, not like a model in training — and it's
starved for entries, not for cleverness.*
