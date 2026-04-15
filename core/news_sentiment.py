"""
Feature 4: News & Sentiment

Fetches recent news from yfinance and scores headlines using a rule-based
lexicon approach (no external API needed). Feeds into Claude's trading prompt.

Functions:
  score_headline(headline) -> float          (-1.0 to 1.0)
  get_yfinance_news(symbol, max_age_hours)   -> List[Dict]
  get_news_summary(symbols)                  -> Dict[str, Dict]
  build_news_block_for_claude(summary, positions) -> str
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ─── Sentiment lexicon ────────────────────────────────────────────────────────
# Word-level scores. Compound phrases handled by phrase matching first.

_PHRASE_SCORES: Dict[str, float] = {
    # Strongly positive
    "beats earnings":          0.8,
    "beat earnings":           0.8,
    "exceeds expectations":    0.7,
    "raises guidance":         0.7,
    "raised guidance":         0.7,
    "record revenue":          0.65,
    "record profit":           0.65,
    "strong earnings":         0.6,
    "dividend increase":       0.55,
    "stock buyback":           0.45,
    "share repurchase":        0.45,
    "upgraded":                0.5,
    "price target raised":     0.55,
    "fda approval":            0.7,
    "partnership":             0.3,
    "acquisition":             0.25,
    "strategic acquisition":   0.35,
    # Strongly negative
    "misses earnings":         -0.8,
    "missed earnings":         -0.8,
    "below expectations":      -0.7,
    "lowers guidance":         -0.75,
    "lowered guidance":        -0.75,
    "cuts guidance":           -0.75,
    "ceo fraud":               -0.95,
    "sec investigation":       -0.85,
    "fraud investigation":     -0.9,
    "accounting irregularities": -0.85,
    "class action":            -0.8,
    "regulatory fine":         -0.6,
    "data breach":             -0.65,
    "massive layoffs":         -0.6,
    "bankruptcy":              -0.95,
    "chapter 11":              -0.95,
    "going concern":           -0.8,
    "recall":                  -0.5,
    "product recall":          -0.6,
    "downgraded":              -0.5,
    "price target cut":        -0.55,
    "revenue miss":            -0.7,
    "earnings miss":           -0.75,
    "profit warning":          -0.7,
}

_WORD_SCORES: Dict[str, float] = {
    # Positive words
    "surges":       0.5,
    "surge":        0.5,
    "rallies":      0.4,
    "rally":        0.4,
    "jumps":        0.4,
    "jump":         0.4,
    "soars":        0.55,
    "soar":         0.55,
    "gains":        0.3,
    "growth":       0.3,
    "profit":       0.25,
    "profits":      0.25,
    "revenue":      0.1,
    "record":       0.2,
    "strong":       0.25,
    "beat":         0.4,
    "beats":        0.4,
    "approval":     0.35,
    "approved":     0.35,
    "bullish":      0.45,
    "upgrade":      0.4,
    "upgrades":     0.4,
    "outperform":   0.35,
    "innovation":   0.2,
    "breakthrough": 0.4,
    "expands":      0.25,
    "expansion":    0.2,
    # Negative words
    "plunges":     -0.55,
    "plunge":      -0.55,
    "drops":       -0.3,
    "drop":        -0.3,
    "falls":       -0.25,
    "fall":        -0.25,
    "tumbles":     -0.45,
    "tumble":      -0.45,
    "crash":       -0.6,
    "crashes":     -0.6,
    "layoffs":     -0.5,
    "layoff":      -0.5,
    "fired":       -0.3,
    "losses":      -0.4,
    "loss":        -0.3,
    "missed":      -0.5,
    "misses":      -0.5,
    "miss":        -0.4,
    "warning":     -0.35,
    "investigation": -0.45,
    "fraud":       -0.75,
    "lawsuit":     -0.4,
    "downgrade":   -0.45,
    "downgrades":  -0.45,
    "bearish":     -0.45,
    "risks":       -0.2,
    "concerns":    -0.2,
    "disappointing": -0.45,
    "disappoints": -0.45,
    "slumps":      -0.4,
    "slump":       -0.4,
    "weakens":     -0.3,
    "weaker":      -0.25,
    "cuts":        -0.3,
}

_RED_FLAG_PHRASES = [
    "fraud", "investigation", "sec investigation", "class action",
    "accounting irregularities", "going concern", "bankruptcy",
    "chapter 11", "data breach", "ceo resigned", "cfo resigned",
    "earnings restatement", "restatement", "doj",
]


def score_headline(headline: str) -> float:
    """
    Score a news headline on a -1.0 to 1.0 scale.
    Uses phrase matching first, then word-level scoring.
    Returns 0.0 for truly neutral/unknown text.
    """
    text = headline.lower()

    total = 0.0
    hits  = 0

    # Phase 1: Phrase matching (higher weight)
    for phrase, score in _PHRASE_SCORES.items():
        if phrase in text:
            total += score * 1.5   # phrases carry more weight
            hits  += 1

    # Phase 2: Word-level scoring
    words = text.split()
    for word in words:
        # Strip punctuation
        word = word.strip(".,!?;:()")
        if word in _WORD_SCORES:
            total += _WORD_SCORES[word]
            hits  += 1

    if hits == 0:
        return 0.0

    # Average, then clamp to [-1, 1]
    raw = total / max(hits, 1)
    return max(-1.0, min(1.0, raw))


# ─── yfinance news fetcher ────────────────────────────────────────────────────

def get_yfinance_news(symbol: str, max_age_hours: int = 24) -> List[Dict]:
    """
    Fetch recent news for a symbol from yfinance.
    Each item has: title, publisher, link, published_at, sentiment_score, age_hours

    Returns empty list on error. Filters items older than max_age_hours.
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        raw_news = ticker.news or []
    except Exception as e:
        logger.warning(f"yfinance news fetch failed for {symbol}: {e}")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    results = []

    for item in raw_news:
        try:
            # yfinance returns a 'content' dict inside each news item (v2 API)
            # Handle both old dict format and new object format
            if hasattr(item, 'get'):
                content = item.get('content', {}) or {}
                title = (content.get('title') or item.get('title') or '').strip()
                provider = ''
                if content.get('provider'):
                    p = content['provider']
                    provider = p.get('displayName', '') if isinstance(p, dict) else str(p)
                if not provider:
                    provider = item.get('publisher', '')
                link = ''
                if content.get('canonicalUrl'):
                    cu = content['canonicalUrl']
                    link = cu.get('url', '') if isinstance(cu, dict) else str(cu)
                if not link:
                    link = item.get('link', '')
                # Pub date: try content.pubDate, then item.providerPublishTime
                pub_time = None
                pub_date = content.get('pubDate') or content.get('publishedAt')
                if pub_date:
                    try:
                        pub_time = datetime.fromisoformat(str(pub_date).replace('Z', '+00:00'))
                    except Exception:
                        pass
                if not pub_time:
                    ts = item.get('providerPublishTime')
                    if ts:
                        try:
                            pub_time = datetime.fromtimestamp(int(ts), tz=timezone.utc)
                        except Exception:
                            pass
            else:
                # Object-style item
                title = getattr(item, 'title', '') or ''
                provider = ''
                link = ''
                pub_time = None

            if not title:
                continue

            # Age check
            if pub_time:
                if pub_time < cutoff:
                    continue
                age_hours = (datetime.now(timezone.utc) - pub_time).total_seconds() / 3600
            else:
                age_hours = None  # unknown age — include anyway

            sentiment = score_headline(title)

            results.append({
                "title":           title,
                "publisher":       provider,
                "link":            link,
                "published_at":    pub_time.isoformat() if pub_time else None,
                "age_hours":       round(age_hours, 1) if age_hours is not None else None,
                "sentiment_score": round(sentiment, 4),
            })
        except Exception as e:
            logger.debug(f"Error parsing news item for {symbol}: {e}")
            continue

    return results


# ─── Summary builder ──────────────────────────────────────────────────────────

def get_news_summary(symbols: List[str], max_age_hours: int = 48) -> Dict[str, Dict]:
    """
    Fetch and summarize news for a list of symbols.

    Returns dict keyed by symbol:
      sentiment_score: float   (avg of all headlines)
      article_count:   int
      red_flags:       List[str]
      top_headlines:   List[str]
      has_news:        bool
    """
    summary = {}
    for symbol in symbols:
        news_items = get_yfinance_news(symbol, max_age_hours=max_age_hours)

        if not news_items:
            summary[symbol] = {
                "sentiment_score": 0.0,
                "article_count":   0,
                "red_flags":       [],
                "top_headlines":   [],
                "has_news":        False,
            }
            continue

        scores = [item["sentiment_score"] for item in news_items]
        avg_score = sum(scores) / len(scores) if scores else 0.0

        # Red flag detection
        red_flags = []
        for item in news_items:
            title_lower = item["title"].lower()
            for flag in _RED_FLAG_PHRASES:
                if flag in title_lower and flag not in red_flags:
                    red_flags.append(flag)

        # Top headlines sorted by absolute sentiment (most impactful)
        sorted_items = sorted(news_items, key=lambda x: abs(x["sentiment_score"]), reverse=True)
        top_headlines = [item["title"] for item in sorted_items[:5]]

        summary[symbol] = {
            "sentiment_score": round(avg_score, 4),
            "article_count":   len(news_items),
            "red_flags":       red_flags,
            "top_headlines":   top_headlines,
            "has_news":        True,
        }

    return summary


# ─── Claude prompt block ──────────────────────────────────────────────────────

def build_news_block_for_claude(
    news_summary: Dict[str, Dict],
    positions: List[str],
) -> str:
    """
    Build a news block for Claude's prompt.

    Shows:
      - All symbols in `positions` (held stocks need monitoring regardless)
      - Watchlist symbols with negative sentiment (< -0.15) or red flags
      - Skips neutral symbols not in portfolio (reduces noise)

    Returns empty string if nothing noteworthy.
    """
    lines = []

    for symbol, data in news_summary.items():
        if not data.get("has_news"):
            continue

        score     = data["sentiment_score"]
        red_flags = data["red_flags"]
        headlines = data["top_headlines"]
        count     = data["article_count"]

        # Include: held positions always, or any symbol with notable sentiment/flags
        in_portfolio   = symbol in positions
        is_notable     = score < -0.15 or score > 0.3 or len(red_flags) > 0

        if not in_portfolio and not is_notable:
            continue

        # Format score display
        if score > 0.15:
            score_label = f"+{score:.2f} (positive)"
        elif score < -0.15:
            score_label = f"{score:.2f} (NEGATIVE)"
        else:
            score_label = f"{score:.2f} (neutral)"

        flag_str = f" | RED FLAGS: {', '.join(red_flags)}" if red_flags else ""
        portfolio_tag = " [HELD]" if in_portfolio else ""

        lines.append(f"  {symbol}{portfolio_tag}: sentiment={score_label} ({count} articles){flag_str}")
        for h in headlines[:3]:
            lines.append(f"    - {h[:120]}")

    if not lines:
        return ""

    header = "=== NEWS & SENTIMENT ==="
    footer = "RULE: Avoid new entries on symbols with red flags. Monitor held positions with negative sentiment."
    return "\n".join([header] + lines + [footer])
