"""
Trading Journal — real trade history from the DB.
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
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


def render_journal():
    st.title("Trading Journal")

    trades_data, err = _api("/trades", {"limit": 500})
    cycles_data, _ = _api("/cycles", {"limit": 50})
    perf_data, _ = _api("/performance")

    if err:
        st.error(err)
        return

    trades = (trades_data or {}).get("trades", [])
    cycles = (cycles_data or {}).get("cycles", [])
    perf = perf_data or {}

    # ── Summary stats ────────────────────────────────────────────────────────
    closed = [t for t in trades if t.get("pnl") is not None]
    wins = [t for t in closed if float(t.get("pnl", 0)) > 0]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Trades", len(trades))
    c2.metric("Completed Trades", len(closed))
    c3.metric(
        "Win Rate",
        f"{len(wins)/len(closed):.0%}" if closed else "—",
    )
    c4.metric(
        "Total P&L",
        f"${sum(float(t.get('pnl',0)) for t in closed):+,.2f}" if closed else "—",
    )

    st.markdown("---")

    # ── Full trade log ───────────────────────────────────────────────────────
    st.subheader("Trade History")
    if trades:
        df = pd.DataFrame(trades)
        display_cols = ["executed_at", "symbol", "action", "qty", "signal_price",
                        "confidence", "order_status", "pnl"]
        display_cols = [c for c in display_cols if c in df.columns]

        for col in ["signal_price"]:
            if col in df.columns:
                df[col] = df[col].apply(lambda x: f"${float(x):.2f}" if x is not None else "—")
        if "confidence" in df.columns:
            df["confidence"] = df["confidence"].apply(lambda x: f"{float(x):.0%}" if x is not None else "—")
        if "pnl" in df.columns:
            df["pnl"] = df["pnl"].apply(lambda x: f"${float(x):+.2f}" if x is not None else "—")

        st.dataframe(df[display_cols], use_container_width=True, hide_index=True)
    else:
        st.info("No trades yet — will populate once daemon executes orders")

    st.markdown("---")

    # ── Cycle log ────────────────────────────────────────────────────────────
    st.subheader("Claude Cycle Log")
    if cycles:
        df_c = pd.DataFrame(cycles)
        display = ["started_at", "status", "market_open", "decisions", "market_summary"]
        display = [c for c in display if c in df_c.columns]
        if "market_summary" in df_c.columns:
            df_c["market_summary"] = df_c["market_summary"].apply(
                lambda x: (str(x)[:80] + "...") if isinstance(x, str) and len(x) > 80 else (x if isinstance(x, str) else "—")
            )
        st.dataframe(df_c[display], use_container_width=True, hide_index=True)
    else:
        st.info("No cycles recorded yet")

    st.markdown("---")

    # ── P&L over time ────────────────────────────────────────────────────────
    if closed:
        st.subheader("Cumulative P&L")
        df_closed = pd.DataFrame(closed)
        df_closed["executed_at"] = pd.to_datetime(df_closed["executed_at"])
        df_closed["pnl"] = df_closed["pnl"].astype(float)
        df_closed = df_closed.sort_values("executed_at")
        df_closed["cumulative_pnl"] = df_closed["pnl"].cumsum()

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df_closed["executed_at"],
            y=df_closed["cumulative_pnl"],
            mode="lines+markers",
            line=dict(color="#00cc66", width=2),
            fill="tozeroy",
            fillcolor="rgba(0, 204, 102, 0.1)",
        ))
        fig.update_layout(
            xaxis_title="Date",
            yaxis_title="Cumulative P&L ($)",
            template="plotly_dark",
            height=300,
            margin=dict(t=20),
        )
        st.plotly_chart(fig, use_container_width=True)
