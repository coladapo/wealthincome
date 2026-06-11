# WealthIncome — Comprehensive Roadmap

**Last synthesized:** 2026-05-11
**Status:** v1.0.0 shipped. Paper trading. Goal: compound a $100k Alpaca paper account toward $1M, then switch to live capital.

---

## Where we are

- **v1.0.0 tagged and released** (2026-05-08). Production-grade hard guards, provider router, validation agent.
- **Catalyst-risk framework live** — Tier 0–3 sizing/stops, 25% concentration cap, partial-sell `reduce_pct` shipped.
- **Three runtime processes:** FastAPI backend (always-on via launchd, port 8000), Streamlit dashboard (port 8501, started by launch script), trader daemon (5-min cycle loop).
- **Brain:** Claude (claude-sonnet-4-6) via local Claude Code CLI subprocess — piggybacks on subscription, no API billing. ~99.98% prompt-cache hit rate, ~$7/day.
- **Morning/evening routines** scheduled weekdays. Single blessed launcher (`scripts/launch.sh`) handles wedged process cleanup and STATUS reporting.
- **Quality scoring framework** shipped for scout/regime/watchlist.

## Open positions (paper, as of last memory snapshot)

SBUX (concentration risk, near earnings), AMAT (+7.37%), GS (+0.36%), TGT (new). Verify live before acting.

---

## Roadmap

### Phase 1 — Make paper trading bulletproof (now → next 2 weeks)

The trader can't graduate to live capital until these are closed.

**P0 — Safety rails (do before any live money)**
- **PDT guard.** Day-trade counter in DB; hard block at 3 round-trips per 5-day window. Required under $25k.
- **Trailing-stop reconciliation.** Reconciler polls Alpaca to confirm `trailing_stop_order_id` acceptance and fills. Right now we write the ID but never verify.
- **Reconciler silent-exception fix.** Errors are getting swallowed. Add structured logging and an alert path before any real-money flip.

**P1 — Verify what's already built**
- **Restart trader and let it run a full week.** Cycle 35, partial-sell path, catalyst-risk integration all need a live trading cycle to confirm.
- **Performance feedback loop end-to-end test.** Activates at 5+ closed trades — we have none yet. Need to either wait or force the threshold lower for a smoke test.
- **Backtester refresh.** Current backtest predates the enricher stack. Re-run with regime-gated entries, correlation filter, and full enricher signals to get a realistic Sharpe.

**P2 — Operational polish**
- **`pmset repeat wakeorpoweron MTWRF 06:20:00`** — Chris still needs to run this once with sudo, or the Mac won't wake before 6:25am trader start.
- **FedWatch live scrape.** Estimate fallback is in use; krawlr browser scrape into `set_fedwatch_cache()` not automated yet.
- **Desktop app signing.** Workaround (`xattr -cr` + right-click Open) works; long-term fix is a signed app bundle.
- **Config.py cleanup.** Still references only `OPENAI_API_KEY` — tech debt from the pre-Claude era.
- **trader.plist `StartCalendarInterval`.** Script comments claim auto-start at 6:25am PT but it's never wired up; currently the local routine covers it.

### Phase 2 — Edge expansion (next 4–6 weeks)

Once the system is stable, widen the signal stack.

- **Renaissance-style signal stacking** — dark-pool prints (FINRA ATS T+1), Reuters/AP wire scraping, Zacks revisions, Unusual Whales MCP integration. All planned, none built.
- **Cross-asset hedging.** Today's MACRO HEDGE SIGNAL is a partial-sell trigger; expand to actual hedge legs (SH, VIX calls) when regime turns risk-off.
- **Trade history RAG depth.** Closed-trade feedback into the prompt is live but shallow — extend with vector retrieval of similar setups before each decision.
- **Multi-account paper-to-live shadow.** Run live-money account in parallel with paper for 30 days at small size before flipping fully.

### Phase 3 — Capital deployment (8–12 weeks out, gated on Phase 1+2)

Don't open this phase until Phase 1 P0 items are closed and Phase 2 has shown lift in backtest + shadow.

- **Live account bring-up.** Small size ($5–10k) for 30 days minimum.
- **Funding mechanics.** Decide ACH vs wire schedule for compounding deposits.
- **Tax structure.** Talk to CPA about LLC vs personal account, mark-to-market election (Section 475), expected tax treatment of swing-trade gains.
- **Real-money kill switches.** Separate hard caps for live vs paper (smaller initial position size cap, tighter concentration limit, lower daily drawdown circuit breaker).

### Phase 4 — Productize? (open question, 3+ months out)

Optional and not committed. Worth thinking about only after Phase 3 is profitable.

- **Multi-tenant?** Today this is a single-user personal tool. Going multi-tenant means real auth, isolation, compliance (RIA registration?), and a different cost model.
- **Signals-as-a-service?** Sell the enricher stack output without managing capital — sidesteps the RIA question.
- **Open source the framework?** Builds reputation and recruiting funnel; doesn't directly monetize.

---

## Cross-project dependencies and urgent items

These came in via Cosmo todo notifications and are NOT wealthincome-specific but block Chris's broader operating system:

- **[URGENT] Break Render→CF POP affinity in krawlr-media-worker retries** (futureshift) — flagged from iOS, separate project.
- Run chain-of-command formalization loop (cosmo).
- Run fleet-architecture pressure-test loop (cosmo).
- Bump purmemo-mcp to v15.7.20, test, ship.
- Auto-trigger re-OAuth on bad-decrypt (purmemo-mcp).
- Rewrite npm README for vibe-coder onboarding (purmemo-mcp).

---

## Guiding principles (locked in from prior sessions)

1. **Real data only.** No mocks, no random values in production paths. Dashboard pulls from API; API pulls from DB and Alpaca. Verified 2026-05-08.
2. **PID truth.** `/status` derives `trader_running` from PID file + `os.kill(pid, 0)`, never the DB flag.
3. **Boring choice first.** Match patterns already in the repo. Innovate only when explicitly asked.
4. **Single blessed launcher.** `scripts/launch.sh` is the only way to start any service. Routine, dashboard, terminal — everything funnels through it.
5. **Brain stays on local CLI.** Never move to API/cloud routine — preserves subscription pricing.
6. **Plain English first.** Chris is a founder, not an engineer. Lead with what it does in everyday words.

---

## Next concrete action

Start the trader and let it run a full cycle to verify the catalyst-risk integration end-to-end. If that's clean, knock out the PDT guard next.
