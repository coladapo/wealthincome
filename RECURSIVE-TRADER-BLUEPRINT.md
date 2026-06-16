# Building the Most Powerful Recursive Trader — Research Blueprint

**Date:** 2026-06-16. **Method:** widest-net multi-model research (Codex/GPT-5.5 authored,
Gemini/2.5-pro independently cross-verified → AGREE; Claude synthesized). Frames the path beyond
the lessons-memory scaffold shipped today.

---

## The one finding everything rests on

**The bottleneck is not the agent's intelligence — it's statistical power.** With ~35 closed
trades, the most dangerous thing we can build is a self-improvement loop that overfits faster than
we can detect it. Every recommendation below is shaped by that single fact.

And the uncomfortable open question both models raised: **we cannot yet prove Claude's decision
layer adds durable alpha** beyond the structured scouts and rules. 35 trades can't separate skill
from luck. The roadmap is designed to answer that, not assume it.

---

## The headline move: candidate-event logging + a meta-labeling admission gate

This is the highest-leverage idea, and it solves the data problem and the win-rate problem at once.

**1. Log every candidate, not just every trade.** Today we learn from ~35 executed trades. But
every cycle, Claude and the scouts *consider* dozens of names and skip most. If we log every
candidate — its features (regime, RSI, VWAP, options flow, insider, news), and its forward outcome
(did it hit target / stop / time-out, max favorable & adverse excursion) — then **35 trades becomes
thousands of labeled events**. This is the single fastest way out of data starvation. It also
finally captures the *counterfactual* — what the trades we skipped would have done.

**2. Meta-labeling (López de Prado).** Keep Claude + scouts generating trade *candidates* (the
direction engine). Then a SEPARATE, simple model (start with calibrated gradient-boosting / XGBoost,
not a neural net) learns one thing: *given a candidate that looks like this, did similar ones
historically work?* It outputs take / skip / size-down. Only candidates above a calibrated
threshold trade. This directly raises win rate **without** wrecking expectancy — because the
threshold is chosen on out-of-sample expected value, not raw accuracy. Both models named this the
#1 win-rate lever for a small swing book.

The frozen Claude brain stays exactly where it's strong — proposing and explaining. The *learning*
happens in the meta-model and the memory, outside the frozen weights.

---

## Win rate vs expectancy (the trap, stated rigorously)

Expectancy = P(win)·avg_win − P(loss)·avg_loss. An 80%-win / small-winner system loses to a
45%-win / big-winner system. For *compounding*, the real objective is geometric growth → Kelly-style
sizing (maximize expected log wealth), not win frequency. **Trade abstention is the most underrated
win-rate lever** — and meta-labeling is abstention done with evidence. Optimize for expectancy,
Sharpe/Sortino, max drawdown, calibration, and geometric growth; report win rate but never steer by
it alone.

## Recursion mechanisms for a FROZEN brain (ranked, with failure modes)

| Mechanism | Buys | Failure mode | When |
|---|---|---|---|
| Lessons memory / RAG (SHIPPED today) | avoids repeating mistakes | becomes superstition unless tied to measured outcomes | now |
| Candidate logging + meta-label gate | escapes low-N; raises win rate honestly | label leakage looks magical in backtest | **next** |
| Hypothesis→backtest→gate→deploy (human-gated) | ideas become measured candidates | data snooping / overfit | after volume |
| Bandit/RL weighting of scouts | learns which signals deserve weight | learns selection bias from partial feedback | after candidate logs exist |
| Ensemble / committee of models | reduces brittleness, measures disagreement | correlated models = fake diversification | later |
| Small local specialist head (fine-tuned) | cheap fast meta-model | overfits on sparse/leaky labels | only once labels are plentiful |
| Fine-tune the frontier brain | — | loses frontier reasoning; not worth it | not recommended |

Design patterns to borrow (not trading systems, but the right shape for a frozen-LLM agent):
Reflexion (verbal memory), Voyager (reusable skills), ReAct (reason+act), Self-Refine (iterative
self-feedback). The hard rule from financial ML: **every self-improvement proposal must pass
purged/embargoed walk-forward validation with deflated-Sharpe / multiple-testing correction** —
or it's curve-fitting, not learning.

## The bright line (unchanged, reinforced by the research)

SAFE autonomous: add cautionary memories, propose strategy diffs, run shadow backtests, rank
candidates in shadow mode, and **reduce risk** when calibration worsens.
HUMAN-GATED always: changing position/deploy caps, stops, loss limits; adding leverage/shorting/
options/intraday; changing the objective or the validation gate; promoting shadow→paper→live;
weakening exits; materially expanding the universe; editing its own guardrails or CI gates.

---

## Sequenced roadmap — 3 highest-leverage moves

1. **Candidate-event logging (build now, passive).** Log every considered name + features +
   forward triple-barrier outcome. *Proof metric:* labeled-event count climbing into the hundreds/
   thousands within weeks; zero impact on live trading (pure observation).
2. **Meta-labeling admission gate in SHADOW.** Train the take/skip/size model on the candidate log;
   run it alongside the live trader scoring every trade but NOT acting. *Proof metric:* on
   out-of-sample/forward data, would-have-taken trades beat actually-taken trades on expectancy and
   win rate. Only after that proves out does a human promote it to actually gate trades.
3. **Human-gated hypothesis loop.** The agent proposes rule/parameter variants; the backtest engine
   scores them with purged validation + deflated Sharpe; Chris promotes winners. *Proof metric:*
   promoted changes hold their backtested edge in forward paper.

Everything here is prove-in-shadow-then-promote — exactly the founder's prove-then-scale bias, and
it never crosses the bright line without a human.

*One line: stop trying to make the brain smarter; make the system keep score on everything it
considers, then let evidence — gated by a human — decide what it's allowed to do more of.*
