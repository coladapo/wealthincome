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

    # ── Validation Agent Stats ────────────────────────────────────────────────
    st.divider()
    st.subheader("Validation Agent")
    try:
        import sys
        sys.path.insert(0, ".")
        from backend.db import get_connection
        conn = get_connection()

        val_rows = conn.execute("""
            SELECT verdict, source, symbol, action, risk_score, block_reason, validated_at
            FROM validation_results
            ORDER BY validated_at DESC
            LIMIT 100
        """).fetchall()

        if val_rows:
            total   = len(val_rows)
            passed  = sum(1 for r in val_rows if r[0] == "pass")
            warned  = sum(1 for r in val_rows if r[0] == "warn")
            blocked = sum(1 for r in val_rows if r[0] == "block")

            v1, v2, v3, v4 = st.columns(4)
            v1.metric("Total Validated", total)
            v2.metric("Passed", passed, delta=f"{passed/total:.0%}" if total else None)
            v3.metric("Warned", warned)
            v4.metric("Blocked", blocked, delta=f"-{blocked} trades prevented" if blocked else None)

            # Recent validations table
            val_df = pd.DataFrame([dict(r) for r in val_rows[:20]])
            val_df["validated_at"] = pd.to_datetime(val_df["validated_at"]).dt.strftime("%m-%d %H:%M")
            val_df = val_df.rename(columns={
                "validated_at": "Time", "symbol": "Symbol", "action": "Action",
                "verdict": "Verdict", "risk_score": "Risk", "block_reason": "Block Reason", "source": "Source"
            })
            def color_verdict(v):
                if v == "block": return "background-color: #ff4444; color: white"
                if v == "warn":  return "background-color: #ffaa00; color: black"
                return "background-color: #22aa44; color: white"
            styled = val_df[["Time","Symbol","Action","Verdict","Risk","Block Reason"]].style.applymap(
                color_verdict, subset=["Verdict"]
            )
            st.dataframe(styled, use_container_width=True, hide_index=True)
        else:
            st.info("No validation results yet — will populate once trading starts")

        conn.close()
    except Exception as e:
        st.warning(f"Could not load validation stats: {e}")

    # ── Token / Cost / Data Quality ───────────────────────────────────────────
    st.divider()
    st.subheader("AI Cost & Data Quality")
    try:
        from backend.db import get_token_usage
        u = get_token_usage()
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Today Cost", f"${u.get('today_cost_usd', 0):.4f}")
        c2.metric("Alltime Cost", f"${u.get('alltime_cost_usd', 0):.4f}")
        c3.metric("Total Cycles", u.get("alltime_cycles", 0))
        c4.metric("Errored Cycles", u.get("errored_cycles", 0))
        c5.metric("Avg Data Quality", f"{(u.get('avg_data_quality') or 0):.0%}")
    except Exception as e:
        st.warning(f"Could not load cost stats: {e}")

    # ── LLM Provider ─────────────────────────────────────────────────────────
    st.divider()
    st.subheader("LLM Provider")
    try:
        from backend.db import get_config, set_config
        cfg = get_config()
        cur_provider = cfg.get("llm_provider", "anthropic_cli")
        cur_model    = cfg.get("llm_model", "claude-sonnet-4-6")

        p1, p2 = st.columns(2)
        providers = ["anthropic_cli", "anthropic_api", "openai", "gemini", "grok", "ollama"]
        new_provider = p1.selectbox("Provider", providers, index=providers.index(cur_provider) if cur_provider in providers else 0)
        new_model    = p2.text_input("Model", value=cur_model)

        if st.button("Update Provider / Model"):
            set_config("llm_provider", new_provider)
            set_config("llm_model", new_model)
            st.success(f"Updated: {new_provider} / {new_model} — takes effect on next cycle")
            st.rerun()
    except Exception as e:
        st.warning(f"Could not load provider config: {e}")
