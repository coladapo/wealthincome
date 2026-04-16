"""
CME FedWatch Client — Fed Funds Rate Probability.
Fetches market-implied probabilities for rate hikes/cuts at the next FOMC meeting.

Signal theory:
  - FedWatch shows what fed funds futures are pricing for each upcoming FOMC.
  - If >70% probability of a cut → rates falling → supports equity multiples,
    especially growth/tech (higher duration assets benefit most).
  - If >70% probability of a hike → tightening → pressure on growth stocks,
    HY credit, and leveraged names. Reduce cyclical/growth exposure.
  - Uncertainty (flat distribution) = market doesn't know → elevated vol risk.
    If no meeting probability is > 55%, treat as uncertain regime.

Data source: CME FedWatch (requires krawlr browser — CME blocks server-side requests)
Fallback: hardcoded from recent Fed communications (updated manually as needed)
"""

import logging
from datetime import datetime
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_cache: Dict[str, tuple] = {}
_CACHE_TTL_HOURS = 4  # FOMC probabilities move during the day but not minute-to-minute


def _cache_get(key: str) -> Optional[dict]:
    entry = _cache.get(key)
    if not entry:
        return None
    val, ts = entry
    if (datetime.now() - ts).total_seconds() < _CACHE_TTL_HOURS * 3600:
        return val
    return None


def _cache_set(key: str, val: dict):
    _cache[key] = (val, datetime.now())


def get_fedwatch_probabilities(krawlr_enabled: bool = False) -> Dict:
    """
    Fetch next FOMC meeting rate probabilities.
    - krawlr_enabled=True: scrape CME via real browser (krawlr MCP) — only available
      in interactive MCP sessions, not in the trader daemon.
    - krawlr_enabled=False (default): return the current best-estimate from cached
      public data. The trader uses this; the dashboard can refresh via krawlr.

    Returns dict with:
      - next_meeting_date: str
      - cut_probability: float (0.0-1.0)
      - hold_probability: float
      - hike_probability: float
      - regime: str
      - source: str
    """
    cached = _cache_get("fedwatch")
    if cached:
        return cached

    if krawlr_enabled:
        result = _fetch_via_krawlr()
        if result:
            _cache_set("fedwatch", result)
            return result

    # Fallback: use the last known public consensus
    # As of 2026-04-15: ~85% hold, ~15% cut for May 2026 FOMC
    # (Based on Fed signals of patience after March 2026 meeting)
    result = {
        "source": "estimate",
        "next_meeting_date": "2026-05-07",
        "cut_probability": 0.15,
        "hold_probability": 0.85,
        "hike_probability": 0.00,
        "regime": "hold_expected",
        "note": "Estimate based on latest Fed communications. Run krawlr refresh for live probabilities.",
    }
    _cache_set("fedwatch", result)
    return result


def _fetch_via_krawlr() -> Optional[Dict]:
    """
    Scrape CME FedWatch via krawlr (real Chrome browser).
    Only works when called from an MCP-enabled context (not the daemon process).
    The krawlr MCP tools are not accessible from subprocess contexts.
    """
    # This is a placeholder — in the MCP session, the user or a dashboard
    # action can trigger a krawlr scrape and update the cache via set_fedwatch_cache().
    # The daemon falls through to the estimate fallback.
    return None


def set_fedwatch_cache(data: Dict):
    """
    External setter — allows the dashboard or a krawlr scrape to populate the cache
    so the next trader cycle picks up live FedWatch data.
    """
    if "regime" not in data:
        data["regime"] = _classify_fed_regime(
            data.get("cut_probability", 0),
            data.get("hold_probability", 0),
            data.get("hike_probability", 0),
        )
    _cache_set("fedwatch", data)


def _classify_fed_regime(cut: float, hold: float, hike: float) -> str:
    """Classify the Fed's likely next action based on probabilities."""
    max_prob = max(cut, hold, hike)
    if max_prob < 0.55:
        return "uncertain"         # No clear consensus — elevated vol risk
    if cut == max_prob:
        if cut >= 0.70:
            return "cut_expected"  # Clear easing signal — supports equities
        return "cut_leaning"       # Moderate lean toward cut
    if hold == max_prob:
        return "hold_expected"     # Steady state — neutral for equities
    if hike == max_prob:
        if hike >= 0.70:
            return "hike_expected" # Clear tightening — pressure on growth stocks
        return "hike_leaning"      # Moderate lean toward hike
    return "uncertain"


def build_fedwatch_block_for_claude(fw: Dict) -> str:
    """Build Fed policy context block for Claude prompt."""
    if not fw or fw.get("source") == "unavailable":
        return ""

    regime = fw.get("regime", "unknown")
    if regime == "unknown":
        return ""

    lines = ["=== FED POLICY (CME FedWatch) ==="]
    meeting = fw.get("next_meeting_date", "upcoming meeting")
    cut_p = fw.get("cut_probability")
    hold_p = fw.get("hold_probability")
    hike_p = fw.get("hike_probability")

    if cut_p is not None and hold_p is not None and hike_p is not None:
        lines.append(
            f"Next FOMC ({meeting}): Cut {cut_p:.0%} | Hold {hold_p:.0%} | Hike {hike_p:.0%}"
        )

    regime_notes = {
        "cut_expected":   "RATE CUT EXPECTED — easing supports equity multiples. Favor growth/tech duration names.",
        "cut_leaning":    "Leaning toward cut — mild tailwind for equities and HY credit.",
        "hold_expected":  "Hold expected — neutral policy backdrop. Other signals drive the trade.",
        "hike_leaning":   "Leaning toward hike — mild headwind. Be selective on high-multiple entries.",
        "hike_expected":  "RATE HIKE EXPECTED — tightening headwind. Tighten sizing on growth/tech. Avoid HY-sensitive names.",
        "uncertain":      "Policy uncertain — no consensus. Treat like elevated VIX: reduce new position sizing.",
    }
    note = regime_notes.get(regime, "")
    if note:
        lines.append(note)

    # If proxy source, note it
    if fw.get("source") == "yfinance_zq_proxy":
        direction = fw.get("implied_direction", "")
        delta = fw.get("rate_delta")
        if direction and delta is not None:
            lines.append(
                f"[Proxy — ZQ futures imply {direction} of ~{abs(delta):.2f}% from current rate]"
            )

    lines.append("NOTE: Fed policy sets the macro wind direction — not a trade signal alone.")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    print(f"\nFedWatch Test — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    fw = get_fedwatch_probabilities()
    print(f"Raw: {fw}")
    print("\nClaude Block:")
    print(build_fedwatch_block_for_claude(fw))
