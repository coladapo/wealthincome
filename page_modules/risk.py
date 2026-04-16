"""
Risk Management Page — wired to real backend API data.
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import requests

API_BASE = "http://localhost:8000"


def _api(path, params=None):
    try:
        r = requests.get(f"{API_BASE}{path}", params=params, timeout=8)
        r.raise_for_status()
        return r.json(), None
    except requests.exceptions.ConnectionError:
        return None, "Backend API not running"
    except Exception as e:
        return None, str(e)


def render_risk_management():
    st.title("Risk Management")

    perf_data, err = _api("/performance")
    status_data, _ = _api("/status")
    trades_data, _ = _api("/trades", {"limit": 200})

    if err:
        st.error(err)
        return

    perf = perf_data or {}
    account = (status_data or {}).get("account") or {}
    positions = (status_data or {}).get("positions") or []
    cfg = (status_data or {}).get("config") or {}
    trades = (trades_data or {}).get("trades", [])

    # ── Risk overview ────────────────────────────────────────────────────────
    st.subheader("Portfolio Risk Overview")
    c1, c2, c3, c4, c5 = st.columns(5)

    c1.metric(
        "Volatility",
        f"{float(perf['volatility_pct']):.1f}%" if perf.get("volatility_pct") is not None else "—",
    )
    c2.metric(
        "Max Drawdown",
        f"{float(perf['max_drawdown_pct']):.1f}%" if perf.get("max_drawdown_pct") is not None else "—",
    )
    c3.metric(
        "Sharpe Ratio",
        f"{float(perf['sharpe_ratio']):.2f}" if perf.get("sharpe_ratio") is not None else "—",
    )
    c4.metric(
        "Win Rate",
        f"{float(perf['win_rate'])*100:.0f}%" if perf.get("win_rate") is not None else "—",
    )
    portfolio_value = float(account.get("portfolio_value", 0))
    cash = float(account.get("cash", 0))
    invested_pct = ((portfolio_value - cash) / portfolio_value * 100) if portfolio_value > 0 else 0
    c5.metric("Invested", f"{invested_pct:.0f}%")

    st.markdown("---")

    # ── Active positions risk ────────────────────────────────────────────────
    st.subheader("Open Positions")
    if positions:
        rows = []
        for p in positions:
            market_val = float(p.get("market_value", 0))
            cost = float(p.get("cost_basis", 0))
            pnl = float(p.get("unrealized_pl", 0))
            pnl_pct = float(p.get("unrealized_plpc", 0)) * 100
            weight = (market_val / portfolio_value * 100) if portfolio_value > 0 else 0
            rows.append({
                "Symbol": p.get("symbol"),
                "Qty": p.get("qty"),
                "Market Value": f"${market_val:,.2f}",
                "Weight": f"{weight:.1f}%",
                "Unrealized P&L": f"${pnl:+,.2f}",
                "P&L %": f"{pnl_pct:+.2f}%",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        # Concentration chart
        weights = []
        for p in positions:
            mv = float(p.get("market_value", 0))
            weights.append({"Symbol": p.get("symbol"), "Weight": mv / portfolio_value * 100 if portfolio_value > 0 else 0})
        if cash > 0:
            weights.append({"Symbol": "CASH", "Weight": cash / portfolio_value * 100 if portfolio_value > 0 else 0})

        fig = go.Figure(go.Pie(
            labels=[w["Symbol"] for w in weights],
            values=[w["Weight"] for w in weights],
            hole=0.4,
        ))
        fig.update_layout(template="plotly_dark", height=300, margin=dict(t=20))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No open positions")

    st.markdown("---")

    # ── Risk config limits ───────────────────────────────────────────────────
    st.subheader("Active Risk Limits")
    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Max Position Size", f"{float(cfg.get('max_position_pct', 0.08))*100:.0f}%")
    r2.metric("Max Open Positions", cfg.get("max_open_positions", 8))
    r3.metric("Daily Loss Limit", f"{float(cfg.get('daily_loss_limit_pct', 0.05))*100:.0f}%")
    r4.metric("Confidence Threshold", f"{float(cfg.get('confidence_threshold', 0.72))*100:.0f}%")

    daily_pnl = float(account.get("daily_pnl", 0))
    daily_limit_dollar = portfolio_value * float(cfg.get("daily_loss_limit_pct", 0.05))
    if daily_pnl < 0:
        pct_of_limit = abs(daily_pnl) / daily_limit_dollar * 100 if daily_limit_dollar > 0 else 0
        if pct_of_limit > 80:
            st.warning(f"Daily loss at ${daily_pnl:,.2f} — {pct_of_limit:.0f}% of ${daily_limit_dollar:,.0f} limit")
        else:
            st.success(f"Daily loss: ${daily_pnl:,.2f} ({pct_of_limit:.0f}% of daily limit)")

    st.markdown("---")

    # ── Recent trade risk metrics ────────────────────────────────────────────
    closed = [t for t in trades if t.get("pnl") is not None]
    if closed:
        st.subheader("Closed Trade Risk")
        df_t = pd.DataFrame(closed)
        df_t["pnl"] = df_t["pnl"].astype(float)

        col1, col2 = st.columns(2)
        with col1:
            avg_win = df_t[df_t["pnl"] > 0]["pnl"].mean() if len(df_t[df_t["pnl"] > 0]) > 0 else 0
            avg_loss = df_t[df_t["pnl"] <= 0]["pnl"].mean() if len(df_t[df_t["pnl"] <= 0]) > 0 else 0
            st.metric("Avg Win", f"${avg_win:+.2f}")
            st.metric("Avg Loss", f"${avg_loss:+.2f}")
            pf = abs(avg_win / avg_loss) if avg_loss != 0 else None
            st.metric("Profit Factor", f"{pf:.2f}" if pf else "—")

        with col2:
            largest_loss = df_t["pnl"].min()
            largest_win = df_t["pnl"].max()
            st.metric("Largest Win", f"${largest_win:+.2f}")
            st.metric("Largest Loss", f"${largest_loss:+.2f}")
            st.metric("Total Closed Trades", len(closed))
    else:
        st.info("Risk metrics from closed trades will appear here")
