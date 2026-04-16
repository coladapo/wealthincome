"""
Signal Enricher — aggregates all enrichment signals into a single context dict.
Runs once per trading cycle before the Claude prompt is built.

Closes the Bloomberg gap using:
  - FRED API:          macro context (yield curve, VIX, HY spreads)        — free JSON, no scraping
  - SEC EDGAR API:     insider buying (Form 4)                              — free JSON, already in core/edgar_agent.py
  - yfinance:          earnings calendar (next date, days away)             — already available
  - Barchart:          unusual options flow (calls vs puts, net premium)    — scraped, 15-min delayed
  - EarningsWhispers:  whisper numbers vs consensus                         — scraped (optional)

All functions are fault-tolerant — if a source fails, the enricher returns
what it has and Claude proceeds without that signal. Never blocks a cycle.
"""

import logging
import time
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


def get_enriched_context(
    symbols: List[str],
    positions: Optional[List[dict]] = None,
    include_options: bool = True,
    include_earnings: bool = True,
    include_macro: bool = True,
    include_insider: bool = True,
    include_fedwatch: bool = True,
    include_edgar_extended: bool = True,
) -> Dict[str, Any]:
    """
    Run all signal enrichers and return a single context dict.
    Each key maps to the raw data + a pre-built Claude prompt block.

    Returns:
      {
        "macro": {...},
        "macro_block": "=== MACRO CONTEXT ===\n...",
        "earnings": {sym: {...}},
        "earnings_block": "=== EARNINGS CALENDAR ===\n...",
        "options_flow": {sym: {...}},
        "options_block": "=== UNUSUAL OPTIONS FLOW ===\n...",
        "insider": {sym: {...}},        # already handled in trader.py
        "combined_block": "...",        # all blocks joined — inject into Claude prompt
      }
    """
    context: Dict[str, Any] = {
        "macro": {},
        "macro_block": "",
        "earnings": {},
        "earnings_block": "",
        "options_flow": {},
        "options_block": "",
        "fedwatch": {},
        "fedwatch_block": "",
        "activist_signals": {},
        "eightk_signals": {},
        "edgar_extended_block": "",
        "performance_intelligence_block": "",
        "combined_block": "",
        "enricher_status": {},   # tracks ok/error per enricher for data quality scoring
    }

    blocks = []
    enricher_status = context["enricher_status"]

    # ── FRED Macro ──────────────────────────────────────────────────────────
    if include_macro:
        try:
            from core.fred_client import get_macro_context, build_macro_block_for_claude
            macro = get_macro_context()
            context["macro"] = macro
            block = build_macro_block_for_claude(macro)
            context["macro_block"] = block
            if block:
                blocks.append(block)
            logger.info(f"Macro enricher: VIX={macro.get('vix')} yield_curve={macro.get('yield_curve_2s10s')} hy={macro.get('hy_spread_bps')}")
            enricher_status["macro"] = {"ok": True, "fields": list(macro.keys())}
        except Exception as e:
            logger.warning(f"Macro enricher failed (non-fatal): {e}")
            enricher_status["macro"] = {"ok": False, "error": str(e)[:100]}

    # ── Earnings Calendar ───────────────────────────────────────────────────
    if include_earnings and symbols:
        try:
            from core.earnings_scraper import get_earnings_calendar, build_earnings_block_for_claude
            earnings = get_earnings_calendar(symbols[:15])
            context["earnings"] = earnings
            block = build_earnings_block_for_claude(earnings, positions=positions)
            context["earnings_block"] = block
            if block:
                blocks.append(block)
            imminent = [s for s, d in earnings.items() if d.get("earnings_risk") in ("today", "imminent")]
            if imminent:
                logger.info(f"Earnings enricher: IMMINENT earnings for {imminent}")
            enricher_status["earnings"] = {"ok": True, "symbols": len(earnings)}
        except Exception as e:
            logger.warning(f"Earnings enricher failed (non-fatal): {e}")
            enricher_status["earnings"] = {"ok": False, "error": str(e)[:100]}

    # ── Options Flow ────────────────────────────────────────────────────────
    if include_options and symbols:
        try:
            from core.options_scraper import get_options_flow, build_options_flow_block_for_claude
            flow = get_options_flow(symbols[:12])
            context["options_flow"] = flow
            block = build_options_flow_block_for_claude(flow, positions=positions)
            context["options_block"] = block
            if block:
                blocks.append(block)
            bullish = [s for s, d in flow.items() if d.get("flow_signal") == "bullish_flow"]
            if bullish:
                logger.info(f"Options enricher: bullish flow in {bullish}")
            enricher_status["options_flow"] = {"ok": True, "symbols": len(flow)}
        except Exception as e:
            logger.warning(f"Options enricher failed (non-fatal): {e}")
            enricher_status["options_flow"] = {"ok": False, "error": str(e)[:100]}

    # ── CME FedWatch ─────────────────────────────────────────────────────────
    if include_fedwatch:
        try:
            from core.fedwatch_client import get_fedwatch_probabilities, build_fedwatch_block_for_claude
            fw = get_fedwatch_probabilities()
            context["fedwatch"] = fw
            block = build_fedwatch_block_for_claude(fw)
            context["fedwatch_block"] = block
            if block:
                blocks.append(block)
            logger.info(f"FedWatch enricher: regime={fw.get('regime')} source={fw.get('source')}")
            enricher_status["fedwatch"] = {"ok": True, "regime": fw.get("regime"), "source": fw.get("source")}
        except Exception as e:
            logger.warning(f"FedWatch enricher failed (non-fatal): {e}")
            enricher_status["fedwatch"] = {"ok": False, "error": str(e)[:100]}

    # ── Extended EDGAR (13D activists + 8-K material events) ────────────────
    if include_edgar_extended and symbols:
        try:
            from core.edgar_signals import get_extended_edgar_signals
            edgar_ext = get_extended_edgar_signals(symbols[:12], positions=positions)
            context["activist_signals"] = edgar_ext.get("activist_signals", {})
            context["eightk_signals"] = edgar_ext.get("eightk_signals", {})
            block = edgar_ext.get("combined_block", "")
            context["edgar_extended_block"] = block
            if block:
                blocks.append(block)
            activists = [s for s, d in context["activist_signals"].items() if d.get("signal") in ("known_activist", "activist_accumulation")]
            if activists:
                logger.info(f"EDGAR activist enricher: signals for {activists}")
            enricher_status["edgar_extended"] = {"ok": True, "symbols": len(context["activist_signals"])}
        except Exception as e:
            logger.warning(f"Extended EDGAR enricher failed (non-fatal): {e}")
            enricher_status["edgar_extended"] = {"ok": False, "error": str(e)[:100]}

    # ── Performance Intelligence (self-calibrating feedback loop) ───────────
    try:
        from core.performance_intelligence import build_performance_intelligence_block
        perf_block = build_performance_intelligence_block(lookback_trades=30)
        context["performance_intelligence_block"] = perf_block
        if perf_block:
            blocks.append(perf_block)
            logger.info("Performance intelligence block injected into Claude context")
        enricher_status["performance_intelligence"] = {"ok": True, "has_data": bool(perf_block)}
    except Exception as e:
        logger.warning(f"Performance intelligence enricher failed (non-fatal): {e}")
        enricher_status["performance_intelligence"] = {"ok": False, "error": str(e)[:100]}

    context["combined_block"] = "\n\n".join(blocks)
    return context


def get_earnings_risk_symbols(symbols: List[str]) -> List[str]:
    """
    Quick check: return symbols with imminent earnings (within 3 days).
    Used by trader.py to skip entries on risky symbols.
    """
    try:
        from core.earnings_scraper import get_earnings_calendar
        earnings = get_earnings_calendar(symbols)
        return [s for s, d in earnings.items() if d.get("earnings_risk") in ("today", "imminent")]
    except Exception:
        return []


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")
    symbols = sys.argv[1:] if len(sys.argv) > 1 else ["AAPL", "NVDA", "MSFT", "AMZN", "CAT", "GS"]
    print(f"\nSignal Enricher Test — {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Symbols: {', '.join(symbols)}\n")
    ctx = get_enriched_context(symbols)
    print("=== COMBINED BLOCK FOR CLAUDE ===")
    print(ctx["combined_block"] or "(no enrichment signals)")
