"""
Analytics Page — wired to real backend API data.
Equity curve, daily P&L, and risk metrics all from the DB.
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import requests
from datetime import datetime

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


def render_analytics():
    st.title("Analytics")

    perf_data, err = _api("/performance", {"days": 252})
    equity_data, _ = _api("/equity-curve", {"days": 90})
    pnl_data, _ = _api("/pnl/daily", {"days": 30})
    trades_data, _ = _api("/trades", {"limit": 500})

    if err:
        st.error(err)
        return

    perf = perf_data or {}
    snapshots = (equity_data or {}).get("snapshots", [])
    daily = (pnl_data or {}).get("days", [])
    trades = (trades_data or {}).get("trades", [])

    # ── Performance overview ─────────────────────────────────────────────────
    st.subheader("Performance Overview")
    c1, c2, c3, c4, c5 = st.columns(5)

    def _fmt_pct(val, key):
        v = perf.get(key)
        return f"{float(v):+.2f}%" if v is not None else "—"

    def _fmt_num(val, key, fmt=".2f"):
        v = perf.get(key)
        return f"{float(v):{fmt}}" if v is not None else "—"

    c1.metric("Total Return", _fmt_pct(None, "total_return_pct"))
    c2.metric("Sharpe Ratio", _fmt_num(None, "sharpe_ratio"))
    c3.metric("Max Drawdown", _fmt_num(None, "max_drawdown_pct", ".1f") + "%" if perf.get("max_drawdown_pct") is not None else "—")
    c4.metric("Volatility", _fmt_num(None, "volatility_pct", ".1f") + "%" if perf.get("volatility_pct") is not None else "—")
    c5.metric("Total Trades", perf.get("total_trades", "—"))

    st.markdown("---")

    # ── Equity curve ─────────────────────────────────────────────────────────
    st.subheader("Equity Curve")
    if snapshots:
        df_eq = pd.DataFrame(snapshots)
        df_eq["snapshot_at"] = pd.to_datetime(df_eq["snapshot_at"])
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df_eq["snapshot_at"],
            y=df_eq["portfolio_value"],
            mode="lines",
            name="Portfolio Value",
            line=dict(color="#1f77b4", width=2),
            fill="tozeroy",
            fillcolor="rgba(31, 119, 180, 0.1)",
        ))
        fig.update_layout(
            xaxis_title="Date",
            yaxis_title="Portfolio Value ($)",
            template="plotly_dark",
            height=350,
            margin=dict(t=20),
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Equity curve will populate once the daemon has run cycles")

    st.markdown("---")

    # ── Daily P&L ────────────────────────────────────────────────────────────
    st.subheader("Daily P&L (Last 30 Days)")
    if daily:
        df_pnl = pd.DataFrame(daily)
        df_pnl["date"] = pd.to_datetime(df_pnl["date"])
        df_pnl["pnl"] = df_pnl["realized_pnl"].astype(float)
        colors = ["green" if v >= 0 else "red" for v in df_pnl["pnl"]]
        fig = go.Figure(go.Bar(
            x=df_pnl["date"],
            y=df_pnl["pnl"],
            marker_color=colors,
        ))
        fig.update_layout(
            xaxis_title="Date",
            yaxis_title="Realized P&L ($)",
            template="plotly_dark",
            height=280,
            margin=dict(t=20),
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Daily P&L will appear after first completed trades")

    st.markdown("---")

    # ── Trade analysis ───────────────────────────────────────────────────────
    closed = [t for t in trades if t.get("pnl") is not None]
    if closed:
        st.subheader("Trade Analysis")
        col1, col2 = st.columns(2)

        with col1:
            # P&L per symbol
            df_t = pd.DataFrame(closed)
            df_t["pnl"] = df_t["pnl"].astype(float)
            by_symbol = df_t.groupby("symbol")["pnl"].sum().reset_index().sort_values("pnl")
            fig = px.bar(
                by_symbol,
                x="symbol",
                y="pnl",
                color="pnl",
                color_continuous_scale="RdYlGn",
                title="Total P&L by Symbol",
            )
            fig.update_layout(template="plotly_dark", height=300, margin=dict(t=40))
            st.plotly_chart(fig, use_container_width=True)

        with col2:
            # P&L distribution
            fig = go.Figure(go.Histogram(
                x=df_t["pnl"],
                nbinsx=20,
                marker_color="rgba(0, 180, 100, 0.7)",
            ))
            fig.update_layout(
                title="P&L Distribution",
                xaxis_title="P&L ($)",
                yaxis_title="Count",
                template="plotly_dark",
                height=300,
                margin=dict(t=40),
            )
            st.plotly_chart(fig, use_container_width=True)

        # Risk metrics detail
        st.subheader("Risk Metrics")
        r1, r2, r3, r4 = st.columns(4)
        r1.metric("Win Rate", f"{perf.get('win_rate', 0)*100:.0f}%" if perf.get('win_rate') is not None else "—")
        r2.metric("Avg Win", f"${perf.get('avg_win', 0):+.2f}" if perf.get('avg_win') is not None else "—")
        r3.metric("Avg Loss", f"${perf.get('avg_loss', 0):+.2f}" if perf.get('avg_loss') is not None else "—")
        r4.metric("Profit Factor", f"{perf.get('profit_factor', 0):.2f}" if perf.get('profit_factor') is not None else "—")
    else:
        st.info("Trade analysis will appear after first completed trades")
