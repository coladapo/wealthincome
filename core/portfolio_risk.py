"""
Feature 5: Correlation & Portfolio Risk

Computes return correlations between symbols to prevent over-concentration in
highly correlated positions. Injects a risk summary into Claude's prompt.

Functions:
  compute_correlation_matrix(symbols, lookback_days) -> pd.DataFrame
  check_entry_correlation(candidate, open_positions, corr_matrix, threshold) -> Dict
  build_correlation_heatmap_text(corr_matrix, open_positions) -> str
  compute_portfolio_concentration(positions_list, account_value) -> Dict
"""

import logging
from typing import Dict, List, Optional

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


# ─── Correlation matrix ───────────────────────────────────────────────────────

def compute_correlation_matrix(symbols: List[str], lookback_days: int = 60) -> pd.DataFrame:
    """
    Download daily close prices for all symbols and return a Pearson
    correlation matrix of daily returns.

    Uses yfinance batch download for efficiency.
    Missing symbols are dropped silently (no data available).

    Returns a symmetric DataFrame with symbols as both index and columns.
    Diagonal = 1.0 by construction.
    """
    import yfinance as yf
    import warnings
    warnings.filterwarnings("ignore")

    from datetime import datetime, timedelta
    end = datetime.now()
    start = end - timedelta(days=lookback_days + 10)  # extra buffer for weekends/holidays

    # Batch download all symbols at once
    try:
        raw = yf.download(
            symbols,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False,
            timeout=30,
            auto_adjust=True,
        )
    except Exception as e:
        logger.error(f"yfinance batch download failed: {e}")
        # Return identity matrix for single symbol fallback
        df = pd.DataFrame(index=symbols, columns=symbols, dtype=float)
        for s in symbols:
            df.loc[s, s] = 1.0
        return df

    # Extract Close prices — handle MultiIndex columns
    if isinstance(raw.columns, pd.MultiIndex):
        if "Close" in raw.columns.get_level_values(0):
            closes = raw["Close"].copy()
        else:
            # Try first level
            closes = raw.xs("Close", axis=1, level=0) if "Close" in raw.columns.get_level_values(0) else raw.iloc[:, :len(symbols)]
    else:
        closes = raw[["Close"]] if "Close" in raw.columns else raw

    # If only one symbol, yfinance returns flat columns
    if isinstance(closes, pd.Series):
        closes = closes.to_frame(name=symbols[0])

    # Drop columns that are entirely NaN (symbols with no data)
    closes = closes.dropna(axis=1, how="all")

    # Ensure all requested symbols are represented (fill missing with 1.0 on diagonal)
    available = list(closes.columns)

    # Daily returns
    returns = closes.pct_change().dropna()

    if returns.empty or len(returns.columns) < 2:
        # Fall back to identity-like matrix
        corr = pd.DataFrame(1.0, index=symbols, columns=symbols)
        return corr

    # Pearson correlation
    corr_available = returns.corr(method="pearson")

    # Expand to full requested symbol list (missing symbols get NaN off-diagonal, 1.0 on diagonal)
    corr = pd.DataFrame(np.nan, index=symbols, columns=symbols)
    for r in symbols:
        for c in symbols:
            if r == c:
                corr.loc[r, c] = 1.0
            elif r in corr_available.index and c in corr_available.columns:
                corr.loc[r, c] = corr_available.loc[r, c]

    return corr


# ─── Entry correlation check ──────────────────────────────────────────────────

def check_entry_correlation(
    candidate: str,
    open_positions: List[str],
    corr_matrix: pd.DataFrame,
    threshold: float = 0.75,
) -> Dict:
    """
    Check if a candidate symbol is too correlated with any current open position.

    Returns:
        blocked:      bool   — True if any existing position exceeds threshold
        correlations: Dict   — {symbol: correlation_value} for all open positions
        reason:       str    — human-readable explanation
        max_corr:     float  — highest correlation found
    """
    if not open_positions:
        return {
            "blocked":      False,
            "correlations": {},
            "reason":       "no open positions",
            "max_corr":     0.0,
        }

    correlations = {}
    max_corr = 0.0
    blocking_symbol = None

    for sym in open_positions:
        try:
            if candidate in corr_matrix.index and sym in corr_matrix.columns:
                corr_val = corr_matrix.loc[candidate, sym]
                if pd.isna(corr_val):
                    corr_val = 0.0
                correlations[sym] = round(float(corr_val), 4)
                abs_corr = abs(float(corr_val))
                if abs_corr > max_corr:
                    max_corr = abs_corr
                    if abs_corr >= threshold:
                        blocking_symbol = sym
        except Exception as e:
            logger.debug(f"Correlation lookup failed for {candidate}/{sym}: {e}")
            correlations[sym] = 0.0

    blocked = blocking_symbol is not None

    if blocked:
        reason = (f"{candidate} is {correlations[blocking_symbol]:.2f} correlated with "
                  f"{blocking_symbol} (threshold={threshold})")
    elif not correlations:
        reason = "correlation data unavailable"
    else:
        reason = f"max correlation {max_corr:.2f} below threshold {threshold}"

    return {
        "blocked":      blocked,
        "correlations": correlations,
        "reason":       reason,
        "max_corr":     round(max_corr, 4),
    }


# ─── Heatmap text builder ─────────────────────────────────────────────────────

def build_correlation_heatmap_text(
    corr_matrix: pd.DataFrame,
    open_positions: List[str],
) -> str:
    """
    Build a compact correlation table for Claude, showing correlations between
    currently held positions and highlighting high-correlation pairs (> 0.7).
    """
    if corr_matrix.empty or not open_positions:
        return "No correlation data available."

    # Focus on open positions only
    syms = [s for s in open_positions if s in corr_matrix.index]
    if not syms:
        return "Open positions not in correlation matrix."

    lines = ["=== PORTFOLIO CORRELATION ==="]

    # Header row
    header = f"{'':8}" + "".join(f"{s:>8}" for s in syms)
    lines.append(header)

    for r in syms:
        row_str = f"{r:<8}"
        for c in syms:
            val = corr_matrix.loc[r, c] if r in corr_matrix.index and c in corr_matrix.columns else float('nan')
            if pd.isna(val):
                row_str += f"{'N/A':>8}"
            elif r == c:
                row_str += f"{'1.00':>8}"
            else:
                marker = "**" if abs(val) > 0.7 else "  "
                row_str += f"{val:>6.2f}{marker}"
        lines.append(row_str)

    # Highlight high-correlation pairs
    high_corr_pairs = []
    for i, r in enumerate(syms):
        for c in syms[i+1:]:
            try:
                val = corr_matrix.loc[r, c]
                if not pd.isna(val) and abs(val) > 0.7:
                    high_corr_pairs.append((r, c, float(val)))
            except Exception:
                pass

    if high_corr_pairs:
        lines.append("\nHIGH CORRELATION PAIRS (>0.70):")
        for r, c, v in sorted(high_corr_pairs, key=lambda x: -abs(x[2])):
            lines.append(f"  {r}/{c}: {v:.2f}")

    return "\n".join(lines)


# ─── Portfolio concentration ──────────────────────────────────────────────────

def compute_portfolio_concentration(
    positions_list: List[Dict],
    account_value: float,
) -> Dict:
    """
    Compute concentration metrics for current open positions.

    positions_list: list of dicts with 'symbol' and 'market_value' keys
    account_value: total portfolio value

    Returns:
        total_positions:   int
        largest_pct:       float   (% of portfolio in largest single position)
        top3_concentration: float  (% in top 3 positions)
        warnings:          List[str]
    """
    if not positions_list or account_value <= 0:
        return {
            "total_positions":    0,
            "largest_pct":        0.0,
            "top3_concentration": 0.0,
            "warnings":           [],
        }

    # Sort by market value descending
    by_value = sorted(
        [(p.get("symbol", "?"), float(p.get("market_value") or 0)) for p in positions_list],
        key=lambda x: x[1], reverse=True,
    )

    pcts = [(sym, mv / account_value * 100) for sym, mv in by_value]

    largest_pct      = pcts[0][1] if pcts else 0.0
    top3_concentration = sum(pct for _, pct in pcts[:3])

    warnings = []
    if largest_pct > 15:
        warnings.append(f"{pcts[0][0]} is {largest_pct:.1f}% of portfolio — overweight")
    if top3_concentration > 40:
        warnings.append(f"Top 3 positions = {top3_concentration:.1f}% of portfolio — concentrated")
    if len(positions_list) > 10:
        warnings.append(f"{len(positions_list)} open positions — consider reducing")

    return {
        "total_positions":     len(positions_list),
        "largest_pct":         round(largest_pct, 2),
        "top3_concentration":  round(top3_concentration, 2),
        "position_breakdown":  {sym: round(pct, 2) for sym, pct in pcts},
        "warnings":            warnings,
    }
