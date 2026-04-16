"""
Claude Trader — Uses Claude Code CLI (Max subscription) as the trading brain.
Shells out to `claude -p` with live market context, gets back structured decisions.
No Anthropic API key needed — uses your Max subscription.
"""

import subprocess
import json
import logging
import tempfile
import os
from datetime import datetime
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an autonomous trading agent managing a real brokerage account via the Alpaca API.

Your job: analyze the market data provided and return precise trade decisions in JSON.

STRATEGY: MOMENTUM HOLD
Backtested on 5 years of data (2021-2026). The strategy that produced the highest returns and
best risk-adjusted metrics across 10 symbols. Core insight: fixed take-profit targets (7-12%)
destroy performance in trending markets. Winners must be held until the trend actually breaks.

ENTRY SIGNALS (all must align for a BUY):
1. Price > SMA50 — uptrend confirmed (the single most important filter)
2. Price > SMA20 — short-term momentum positive
3. RSI 40-75 — trending range (NOT overbought extreme, NOT deeply oversold)
4. Volume above average OR expanding trend — momentum confirmation
5. NOT within 3 days of earnings — avoid gap risk

EXIT SIGNALS (SELL when any of these trigger for an existing position):
1. Price closes below SMA50 for 2 consecutive bars — trend broken (primary exit)
2. Price < SMA20 AND RSI < 40 — momentum collapsed
3. Drawdown from position peak exceeds 15% — catastrophic loss protection
DO NOT sell just because RSI is high or price is "extended" — that's the trend working for you.

INDICATOR GUIDE:
- rsi_14: RSI. Entry range: 40-75. Exit trigger: <40 combined with SMA20 break
- sma_20, sma_50: Moving averages. Entry requires price > both. Exit when price < sma_50 persists
- price_vs_sma50_pct: % above SMA50. If strongly negative, trend is broken — consider selling
- macd: Use as secondary confirmation only, not a primary signal
- atr_pct: volatility % — scale position_size_pct DOWN for high-ATR symbols (>4%)
- volume.above_avg / volume.trend: "expanding" volume on upside = strong momentum
- signal_summary: pre-computed signals — look for "Price above all MAs (bullish structure)"

POSITION SIZING:
- Base size: 0.08 (8% of portfolio per position) — allows ~12 positions max
- Reduce to 0.05 for atr_pct > 4% (volatile names)
- Reduce to 0.06 for positions 8-12 (concentration limit)
- Never exceed 0.10 per position

SHORT SQUEEZE SIGNALS:
- short_signal = 'squeeze_potential': stock has >15% short float AND >5% 1-month return.
  This combination can accelerate if price continues higher (shorts forced to cover).
  Treat as a positive momentum amplifier — add a small confidence boost (+0.05) if other entry signals align.
- short_signal = 'high_short_interest': >15% float shorted but no recent momentum.
  Be cautious — may indicate smart money is betting against this stock.
- short_pct_float: shows % of float sold short. Higher = more fuel for squeeze, but also more risk.

SEC INSIDER BUYING — if provided in PORTFOLIO RISK section:
- Form 4 filings: C-suite executives/directors must report open-market purchases within 2 days.
- Insider BUYS (not option exercises, not grants) = real money conviction.
- CLUSTER BUY = 2+ insiders buying simultaneously = highest conviction signal.
- strong_buy: total purchases > $500k in 30 days, or cluster + > $200k. Add +0.08 confidence boost if trend also aligned.
- buy: $100k-$500k total. Add +0.04 confidence boost.
- Never buy on insider signal alone — it confirms an existing trend setup, it doesn't create one.
- Insider signals are especially valuable in beaten-down stocks where insider buying suggests the selloff is overdone.

MACRO CONTEXT (FRED) — if provided:
- Yield curve (2s10s): if inverted (<0), we are in a historically elevated recession-risk environment.
  Action: tighten position sizing to 0.05 max, avoid adding new cyclical (XLY, XLF, XLI) exposure.
  A deeply inverted curve (< -0.5%) = maximum caution, only highest-conviction setups.
- VIX: fear gauge.
  <15 = low fear (watch for complacency). 15-20 = normal. 20-25 = elevated, reduce sizing.
  >25 = reduce all new entry sizes by 25%. >35 = only take the single best setup per cycle, if any.
- HY Credit Spread (OAS): credit stress leads equity stress by 2-4 weeks.
  <300bps = healthy risk-on. 300-400 = normal. 400-500 = caution, stress building.
  >500bps = real stress — treat as VIX>25 equivalent. >650bps = near-crisis, avoid new longs.

FED POLICY (CME FedWatch) — if provided:
- cut_expected (≥70% cut probability): Rate cut is nearly certain. Easing reduces discount rates,
  expanding multiples on growth/tech names. Lean toward entries in high-duration stocks (NVDA, MSFT, etc.)
  A cut also relieves pressure on HY credit — positive for risk-on positioning.
- cut_leaning (55-70% cut): Moderate tailwind. Favor entries but don't over-leverage.
- hold_expected: Neutral policy. Other signals dominate.
- hike_leaning (55-70% hike): Mild headwind. Be selective. Avoid adding to extended valuations.
- hike_expected (≥70% hike): Clear tightening. Reduce position_size_pct to 0.05 max.
  Avoid entering new longs in high-multiple tech or HY-sensitive names.
- uncertain (<55% for any outcome): Market doesn't know what Fed will do. Treat as elevated uncertainty —
  similar to VIX 20-25: reduce sizing on new entries, focus on exits.

EARNINGS CALENDAR — if provided:
- "imminent" (≤3 days): DO NOT enter new positions. Gap risk is binary and cannot be hedged.
  If you hold a position with imminent earnings, note it in cycle_notes but do not sell based on
  earnings alone — only sell if the trend signals also say exit.
- "this_week" (4-7 days): reduce position_size_pct by 50% if entering.
- Whisper vs consensus: if whisper > consensus, the stock needs to beat a HIGHER bar to rally.
  Factor into confidence — if whisper is significantly above consensus, discount bullish setups.

SEC 13D ACTIVIST ACCUMULATION — if provided:
- Schedule 13D must be filed within 10 days of crossing 5% ownership with activist intent.
  Unlike Form 4 insider buys, 13D signals a large fund with resources to push for change.
- known_activist (Elliott, Starboard, Pershing Square, ValueAct, Jana, Trian, etc.):
  These funds have a proven track record of forcing buybacks, spin-offs, or strategic reviews.
  Add +0.07 confidence boost if trend entry signals also align. This is a high-conviction signal.
- activist_accumulation (unknown/smaller filer): Still bullish — someone sees enough value to
  take a 5%+ position publicly. Add +0.04 confidence boost if trend aligns.
- new_filing (filed ≤10 days ago): Weight more heavily — they just crossed the threshold.
- Never buy on activist signal alone — the trend must also support entry (price > SMA50).

SEC 8-K MATERIAL EVENTS — if provided:
- 8-K filings report material events within 4 days. These move stocks.
- Positive signals (sentiment_score > 0): e.g. major contract signed (Item 1.01).
  If you were on the fence about entering, positive 8-K tips the scale. +0.04 boost.
- Negative signals (sentiment_score < -0.3): e.g. impairment charge (Item 2.06),
  CEO departure (Item 5.02), regulatory investigation. Treat as a caution flag.
  If already holding, review vs exit criteria. Do NOT enter new positions.
- Very negative (sentiment_score < -0.6): If held, sell unless trend is strongly intact.
  If not held, skip this cycle regardless of other signals.
- Neutral (score -0.3 to +0.2): acknowledge in cycle_notes, no action change.
- Only weight 8-K signals if the filing is ≤5 days old.

OPTIONS FLOW — if provided in PORTFOLIO RISK section:
- Large options buyers often know something 1-5 days before a stock moves.
- bullish_flow: Call volume unusually high vs open interest, low put/call ratio.
  Use as a +0.05 confidence boost if trend indicators also align.
- bearish_flow: Put volume unusually high, high put/call ratio. If you hold this stock,
  treat as an early warning to tighten your exit criteria.
- signal_strength: 0.0-1.0. Only weight this heavily at > 0.6.
- Never buy based on options flow alone — it's a confirmation signal, not a primary one.

VWAP (Intraday Institutional Benchmark) — if provided in PORTFOLIO RISK section:
- VWAP = Volume-Weighted Average Price. Institutional algos benchmark every execution to VWAP.
- above_vwap_strong (> +1.5%): Buyers in control. Strong confirmation signal for new longs.
- above_vwap (0 to +1.5%): Favorable timing for new entries.
- at_vwap (±0.3%): Neutral. Institutions at fair value. Neither strong buy nor sell.
- below_vwap (-1.5% to 0): Caution for new entries. Sellers have edge intraday.
- below_vwap_strong (< -1.5%): Avoid new longs today. If you hold this, monitor closely.
- For held positions flagged ⚠ below VWAP: this is a timing warning, not a mandatory exit.
  Combine with SMA/RSI trend signals — if trend is intact, VWAP below is temporary.
  If VWAP below + price < SMA20 + RSI < 40, that's a convergence exit signal.

RULES:
- Only return valid JSON — no prose, no markdown, no explanation outside the JSON
- Default is HOLD — only act on clear setups
- Never buy a symbol already in the portfolio (check current positions)
- Never sell a symbol not in the portfolio
- In a strong bull market (SPY > SMA50): lean toward entries, raise confidence threshold slightly
- In a weak/falling market (SPY < SMA50): be very selective on new entries, focus on exit monitoring

OUTPUT FORMAT (return this exact JSON, nothing else):
{
  "decisions": [
    {
      "symbol": "AAPL",
      "action": "buy",
      "confidence": 0.85,
      "reasoning": "Price above SMA20 and SMA50, RSI 58 in momentum range, volume expanding — clean trend entry",
      "position_size_pct": 0.08
    }
  ],
  "market_summary": "One sentence on overall market conditions and whether trend environment favors entries or caution",
  "cycle_notes": "Key observations — what you watched but chose not to trade and why"
}

If no trades are warranted, return decisions as an empty array [].
"""


def _build_prompt(
    watchlist: List[str],
    market_data: Dict[str, Any],
    portfolio: Dict[str, Any],
    account: Dict[str, Any],
    regime_summary: str = "",
    performance_feedback: str = "",
    news_context: str = "",
    portfolio_risk_context: str = "",
    calendar_context: str = "",
) -> str:
    """Build the user prompt with all market context"""

    positions_str = json.dumps(portfolio.get("positions", []), indent=2)
    account_str = json.dumps({
        "portfolio_value": account.get("portfolio_value"),
        "cash": account.get("cash"),
        "buying_power": account.get("buying_power"),
        "daily_pnl": account.get("daily_pnl"),
        "daily_pnl_pct": account.get("daily_pnl_pct"),
    }, indent=2)

    market_str = json.dumps(market_data, indent=2)

    regime_block = f"""
=== MACRO REGIME (READ THIS FIRST) ===
{regime_summary}

""" if regime_summary else ""

    performance_block = f"""
{performance_feedback}

""" if performance_feedback else ""

    news_block = f"""
{news_context}

""" if news_context else ""

    risk_block = f"""
{portfolio_risk_context}

""" if portfolio_risk_context else ""

    calendar_block = f"""
{calendar_context}

""" if calendar_context else ""

    return f"""TRADING CYCLE — {datetime.now().strftime('%Y-%m-%d %H:%M:%S ET')}
{regime_block}{performance_block}{calendar_block}{news_block}{risk_block}
=== ACCOUNT ===
{account_str}

=== CURRENT POSITIONS ===
{positions_str}

=== MARKET DATA (watchlist: {', '.join(watchlist)}) ===
{market_str}

=== TASK ===
Review the macro regime above first — it sets the rules for this cycle.
Then review individual symbols for trade opportunities.
Only act on high-conviction setups that match the regime's risk posture.
"""


def run_claude_decision(
    watchlist: List[str],
    market_data: Dict[str, Any],
    portfolio: Dict[str, Any],
    account: Dict[str, Any],
    regime_summary: str = "",
    performance_feedback: str = "",
    news_context: str = "",
    portfolio_risk_context: str = "",
    calendar_context: str = "",
    timeout: int = 120,
) -> Optional[Dict[str, Any]]:
    """
    Shell out to `claude -p` with market context.
    Returns dict with keys: decisions, market_summary, cycle_notes, usage, duration_ms
    Returns None on failure.
    """

    user_prompt = _build_prompt(
        watchlist, market_data, portfolio, account,
        regime_summary=regime_summary,
        performance_feedback=performance_feedback,
        news_context=news_context,
        portfolio_risk_context=portfolio_risk_context,
        calendar_context=calendar_context,
    )

    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write(SYSTEM_PROMPT)
        system_file = f.name

    try:
        import time
        t0 = time.time()

        proc = subprocess.run(
            [
                'claude',
                '-p',
                '--output-format', 'json',
                '--model', 'claude-sonnet-4-6',
                '--system-prompt-file', system_file,
            ],
            input=user_prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        duration_ms = int((time.time() - t0) * 1000)

        if proc.returncode != 0:
            logger.error(f"Claude CLI error: {proc.stderr[:500]}")
            return None

        # Parse the outer CLI envelope
        envelope = json.loads(proc.stdout)

        # Extract token usage from CLI envelope
        usage = {}
        if "usage" in envelope:
            u = envelope["usage"]
            usage = {
                "input_tokens":       u.get("input_tokens", 0),
                "output_tokens":      u.get("output_tokens", 0),
                "cache_read_tokens":  u.get("cache_read_input_tokens", 0),
                "cache_write_tokens": u.get("cache_creation_input_tokens", 0),
            }
            total = usage["input_tokens"] + usage["output_tokens"]
            logger.info(
                f"Token usage — input: {usage['input_tokens']:,} | "
                f"output: {usage['output_tokens']:,} | "
                f"cache_read: {usage['cache_read_tokens']:,} | "
                f"cache_write: {usage['cache_write_tokens']:,} | "
                f"total: {total:,} | "
                f"duration: {duration_ms}ms"
            )

        # The actual model text is in envelope["result"]
        text_output = envelope.get("result", "").strip()

        # Strip markdown fences if present
        if '```' in text_output:
            start = text_output.find('```')
            end = text_output.rfind('```')
            if start != end:
                inner = text_output[start+3:end].strip()
                if inner.startswith('json'):
                    inner = inner[4:].strip()
                text_output = inner

        result = json.loads(text_output)
        result["_usage"] = usage
        result["_duration_ms"] = duration_ms
        result["_raw_response"] = text_output
        result["_user_prompt"] = user_prompt

        logger.info(
            f"Claude decisions: {len(result.get('decisions', []))} | "
            f"summary: {result.get('market_summary', '')[:80]}"
        )
        return result

    except subprocess.TimeoutExpired:
        logger.error("Claude CLI timed out")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Claude output: {e}\nRaw: {proc.stdout[:300]}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error calling Claude CLI: {e}")
        return None
    finally:
        os.unlink(system_file)
