## WealthIncome v1.0.0 — 2026-05-08

First tagged release. WealthIncome is an autonomous AI trading platform with multi-agent intelligence, hard risk guards, and a self-improving performance loop.

### What's New

**Multi-Agent Intelligence**
- Validation agent + trade history retrieval — every trade is checked against historical outcomes before execution (Bridgewater + Balyasny-inspired patterns)
- EDGAR agent — SEC Form 4 insider buying as a conviction signal
- Options Flow agent — unusual options activity as a leading indicator
- Tick agent — VWAP intraday signal applied to every trade cycle
- 6-layer intelligence stack with live-tested signal enrichers

**Provider Flexibility**
- Provider-agnostic LLM router — swap Claude, GPT-4o, Gemini, Grok, or Ollama via config without code changes

**Risk & Safety**
- Hard guards: no margin trading, 80% portfolio deployment cap
- Self-improving performance intelligence feedback loop — the system learns from its own trade outcomes

**Operations**
- Launchd auto-start/stop tied to LA market hours
- Token cost tracking on every cycle
- Enricher status tracking for observability

### Bug Fixes
- Fixed ETF short interest 404 errors and added SSL retry logic
- Fixed duplicate-buy bug in execution pipeline
- Fixed duplicate import and undefined variable found in audit
- Hardened execution pipeline against edge cases

### Infrastructure
- Streamlit Cloud deployment configured with minimal requirements
