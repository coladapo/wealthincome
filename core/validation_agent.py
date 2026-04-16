"""
Validation Agent — second-pass LLM check before any trade executes.

Inspired by Bridgewater's three-layer validation (8% → 1.6% error rate).
Inspired by Balyasny's dual-LLM pattern (one generates, one checks).

For every BUY decision the main LLM proposes, this agent plays devil's
advocate: given the same market context + the proposed trade, it asks a
focused skeptical question and returns a PASS / BLOCK / WARN verdict.

Design principles:
  - Fast: short prompt, small token budget (~300 output tokens max)
  - Fault-tolerant: any failure → PASS (never block a trade due to validator crash)
  - Provider-agnostic: uses same llm_router dispatch as main trader
  - Auditable: every validation stored in DB (validation_results table)
  - HOLD decisions always pass — only BUY is validated pre-execution
    (SELL/exit decisions are time-sensitive and already guarded by stop-losses)
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Validation verdict constants
PASS  = "pass"
WARN  = "warn"    # proceed but log the concern
BLOCK = "block"   # do not execute this trade

# Risk factors that trigger an automatic BLOCK without LLM call (rule-based fast path)
_HARD_BLOCK_RULES = [
    # Confidence too low — below system threshold
    lambda d, _ctx: (
        BLOCK if float(d.get("confidence", 1.0)) < 0.65
        else None,
        f"Confidence {d.get('confidence')} below hard minimum 0.65"
    ),
    # Proposed position size > 12% of portfolio (way over 8% max)
    lambda d, ctx: (
        BLOCK if float(d.get("position_size_pct", 0)) > 0.12
        else None,
        f"position_size_pct {d.get('position_size_pct')} exceeds 12% hard cap"
    ),
    # No symbol
    lambda d, _ctx: (
        BLOCK if not d.get("symbol")
        else None,
        "Decision missing symbol"
    ),
]


VALIDATION_SYSTEM_PROMPT = """You are a risk management validator for an autonomous trading system.
Your only job is to identify critical flaws in proposed trades before they execute.

Be concise and decisive. Return ONLY valid JSON — no prose.

Required JSON format:
{
  "verdict": "pass" | "warn" | "block",
  "risk_score": 0-10,
  "top_risks": ["risk 1", "risk 2", "risk 3"],
  "block_reason": "one sentence if verdict=block, else null"
}

Verdict rules:
- "pass": trade looks reasonable given the context
- "warn": proceed but flag a concern (risk_score 4-6)
- "block": critical flaw detected — do NOT execute (risk_score 7-10)

Block conditions (any one is sufficient):
1. Proposed trade directly contradicts a strong bearish signal in the context
2. Earnings announcement within 24 hours and trade is speculative
3. Symbol already held and another BUY would dangerously over-concentrate
4. Macro context is severely bearish (VIX > 30, yield curve deeply inverted) AND trade is aggressive
5. Reasoning provided is incoherent or circular

Do NOT block for:
- Normal uncertainty or slightly mixed signals
- Moderate macro headwinds
- Your own stylistic disagreement with the strategy"""


def _build_validation_prompt(
    decision: Dict[str, Any],
    market_context: str,
    positions: Dict[str, Any],
    account: Dict[str, Any],
) -> str:
    sym = decision.get("symbol", "?")
    action = decision.get("action", "?").upper()
    confidence = decision.get("confidence", "?")
    reasoning = decision.get("reasoning", "no reasoning provided")
    size_pct = decision.get("position_size_pct", "?")

    held = list(positions.keys()) if positions else []
    already_held = sym in positions
    portfolio_val = account.get("portfolio_value", 0)
    cash = account.get("cash", 0)

    lines = [
        f"PROPOSED TRADE: {action} {sym}",
        f"  Confidence: {confidence}",
        f"  Position size: {size_pct} of portfolio",
        f"  Already held: {already_held}",
        f"  Reasoning: {reasoning[:300]}",
        "",
        f"CURRENT PORTFOLIO: {len(held)} positions — {', '.join(held) or 'none'}",
        f"  Portfolio value: ${float(portfolio_val):,.0f}",
        f"  Available cash: ${float(cash):,.0f}",
        "",
        "MARKET CONTEXT (truncated):",
        market_context[:1500] if market_context else "(none)",
        "",
        "Validate this trade. Return JSON only.",
    ]
    return "\n".join(lines)


def _run_hard_block_rules(
    decision: Dict[str, Any],
    context: str,
) -> Optional[Dict[str, Any]]:
    """Fast rule-based checks that don't need an LLM call."""
    for rule in _HARD_BLOCK_RULES:
        verdict, reason = rule(decision, context)
        if verdict == BLOCK:
            return {
                "verdict": BLOCK,
                "risk_score": 10,
                "top_risks": [reason],
                "block_reason": reason,
                "_source": "hard_rule",
            }
    return None


def validate_decision(
    decision: Dict[str, Any],
    market_context: str,
    positions: Dict[str, Any],
    account: Dict[str, Any],
    use_llm: bool = True,
) -> Dict[str, Any]:
    """
    Validate a single trade decision.

    Returns:
        {
            "verdict":      "pass" | "warn" | "block",
            "risk_score":   0-10,
            "top_risks":    [...],
            "block_reason": str or None,
            "_source":      "hard_rule" | "llm" | "fallback",
            "_duration_ms": int,
        }

    Never raises — any failure returns a PASS with _source="fallback".
    """
    t0 = time.time()
    action = decision.get("action", "hold").lower()

    # Only validate BUY decisions — hold/sell pass automatically
    if action != "buy":
        return {
            "verdict": PASS,
            "risk_score": 0,
            "top_risks": [],
            "block_reason": None,
            "_source": "not_buy",
            "_duration_ms": 0,
        }

    # ── Hard rule fast path ────────────────────────────────────────────────────
    hard_result = _run_hard_block_rules(decision, market_context)
    if hard_result:
        hard_result["_duration_ms"] = int((time.time() - t0) * 1000)
        logger.warning(
            f"Validation BLOCK (hard rule) {decision.get('symbol')}: "
            f"{hard_result['block_reason']}"
        )
        return hard_result

    # ── LLM second-pass ────────────────────────────────────────────────────────
    if not use_llm:
        return {
            "verdict": PASS,
            "risk_score": 0,
            "top_risks": [],
            "block_reason": None,
            "_source": "llm_disabled",
            "_duration_ms": int((time.time() - t0) * 1000),
        }

    try:
        from core.llm_router import _PROVIDERS, _parse_trading_json
        from backend.db import get_config

        cfg      = get_config()
        provider = cfg.get("llm_provider", "anthropic_cli")
        model    = cfg.get("llm_model", "claude-sonnet-4-6")

        user_prompt = _build_validation_prompt(decision, market_context, positions, account)
        fn = _PROVIDERS.get(provider)
        if not fn:
            raise ValueError(f"Unknown provider: {provider}")

        text_output, _raw_usage = fn(VALIDATION_SYSTEM_PROMPT, user_prompt, model, timeout=30)
        result = _parse_trading_json(text_output)

        verdict = result.get("verdict", PASS).lower()
        if verdict not in (PASS, WARN, BLOCK):
            verdict = PASS

        out = {
            "verdict":      verdict,
            "risk_score":   int(result.get("risk_score", 0)),
            "top_risks":    result.get("top_risks", []),
            "block_reason": result.get("block_reason") if verdict == BLOCK else None,
            "_source":      "llm",
            "_duration_ms": int((time.time() - t0) * 1000),
        }

        sym = decision.get("symbol", "?")
        if verdict == BLOCK:
            logger.warning(f"Validation BLOCK (LLM) {sym}: {out['block_reason']}")
        elif verdict == WARN:
            logger.info(f"Validation WARN {sym} (risk={out['risk_score']}): {out['top_risks']}")
        else:
            logger.info(f"Validation PASS {sym} (risk={out['risk_score']})")

        return out

    except Exception as e:
        logger.warning(f"Validation agent failed (non-fatal, defaulting to PASS): {e}")
        return {
            "verdict":      PASS,
            "risk_score":   0,
            "top_risks":    [],
            "block_reason": None,
            "_source":      "fallback",
            "_duration_ms": int((time.time() - t0) * 1000),
        }


def validate_decisions(
    decisions: List[Dict[str, Any]],
    market_context: str,
    positions: Dict[str, Any],
    account: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Validate a list of decisions. Returns only the ones that pass or warn.
    Blocked decisions are logged and excluded.

    Returns list of (decision, validation_result) tuples for passing decisions.
    """
    approved = []
    blocked_count = 0

    for d in decisions:
        v = validate_decision(d, market_context, positions, account)
        d["_validation"] = v  # attach validation result to decision for DB recording

        if v["verdict"] == BLOCK:
            blocked_count += 1
            logger.warning(
                f"BLOCKED: {d.get('action','?').upper()} {d.get('symbol','?')} — "
                f"{v['block_reason']}"
            )
        else:
            approved.append(d)

    if blocked_count:
        logger.info(
            f"Validation: {len(approved)} approved, {blocked_count} blocked "
            f"out of {len(decisions)} decisions"
        )

    return approved


# ── DB persistence ─────────────────────────────────────────────────────────────

def record_validation(
    cycle_id: int,
    ai_decision_id: int,
    symbol: str,
    action: str,
    verdict: str,
    risk_score: int,
    top_risks: List[str],
    block_reason: Optional[str],
    source: str,
    duration_ms: int,
):
    """Persist validation result to DB (non-fatal)."""
    try:
        from backend.db import db
        with db() as conn:
            conn.execute("""
                INSERT INTO validation_results(
                    cycle_id, ai_decision_id, validated_at,
                    symbol, action, verdict, risk_score,
                    top_risks_json, block_reason, source, duration_ms
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (
                cycle_id, ai_decision_id,
                __import__("datetime").datetime.now().isoformat(),
                symbol, action, verdict, risk_score,
                json.dumps(top_risks), block_reason, source, duration_ms,
            ))
    except Exception as e:
        logger.warning(f"Could not record validation result: {e}")


if __name__ == "__main__":
    # Quick smoke test
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")
    import sys
    sys.path.insert(0, ".")

    test_decision = {
        "action": "buy",
        "symbol": "NVDA",
        "confidence": 0.78,
        "position_size_pct": 0.08,
        "reasoning": "NVDA above SMA50, RSI 58, volume expanding, MACD positive crossover. AI cycle tailwind.",
    }
    test_account = {"portfolio_value": 100000, "cash": 15000}
    test_positions = {"AAPL": {}, "MSFT": {}}
    test_context = "VIX=18, yield curve -0.2%, HY spread 320bps. Market regime: CAUTION."

    result = validate_decision(test_decision, test_context, test_positions, test_account)
    print(json.dumps(result, indent=2))
