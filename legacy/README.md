# Legacy modules (archived 2026-06-11, G7 in AUDIT-2026-06-11.md)

Verified unreferenced before moving. Kept for history; do not import.

- ai_engine.py — v1 ML signal scaffolding, pre-LLM era. Zero references.
- autonomous_trader.py — v1 trading engine, superseded by backend/trader.py.

Candidates that turned out to be LIVE-COUPLED and stay in core/ for now:
trading_engine/auth/data_manager (imported by app.py dashboard),
options_scraper (imported by signal_enricher), wi_config (api.py + autonomous
page), backtester (roadmap G4 refresh target).
