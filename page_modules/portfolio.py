"""
Portfolio Page — all data from real backend API and Alpaca.
Zero hardcoded or random values.
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
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


def render_portfolio():
    st.title("Portfolio")

    status_data, err = _api("/status")
    if err:
        st.error(err)
        return

    account = status_data.get("account") or {}
    positions = status_data.get("positions") or []
    perf_data, _ = _api("/performance")
    equity_data, _ = _api("/equity-curve", {"days": 90})
    closed_data, _ = _api("/positions/history", {"limit": 100})

    perf = perf_data or {}
    snapshots = (equity_data or {}).get("snapshots", [])
    closed = (closed_data or {}).get("positions", [])

    portfolio_value = float(account.get("portfolio_value", 0))
    cash = float(account.get("cash", 0))
    daily_pnl = float(account.get("daily_pnl", 0))
    daily_pnl_pct = float(account.get("daily_pnl_pct", 0))
    buying_power = float(account.get("buying_power", 0))

    # ── Summary metrics ──────────────────────────────────────────────────────
    st.subheader("Summary")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Portfolio Value", f"${portfolio_value:,.2f}")
    c2.metric("Cash", f"${cash:,.2f}")
    c3.metric(
        "Today's P&L",
        f"${daily_pnl:+,.2f}",
        f"{daily_pnl_pct:+.2f}%",
        delta_color="normal" if daily_pnl >= 0 else "inverse",
    )
    c4.metric("Open Positions", len(positions))
    c5.metric("Buying Power", f"${buying_power:,.2f}")

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
            fillcolor="rgba(31,119,180,0.1)",
        ))
        fig.update_layout(
            template="plotly_dark",
            height=320,
            margin=dict(t=10),
            xaxis_title="Date",
            yaxis_title="Value ($)",
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Equity curve will appear after first trading cycles")

    st.markdown("---")

    # ── Open positions ────────────────────────────────────────────────────────
    st.subheader("Open Positions")
    if positions:
        col1, col2 = st.columns([2, 1])
        with col1:
            rows = []
            for p in positions:
                mv = float(p.get("market_value", 0))
                rows.append({
                    "Symbol": p.get("symbol"),
                    "Qty": p.get("qty"),
                    "Avg Entry": f"${float(p.get('avg_entry_price', 0)):.2f}",
                    "Current": f"${float(p.get('current_price', 0)):.2f}",
                    "Market Value": f"${mv:,.2f}",
                    "Unrealized P&L": f"${float(p.get('unrealized_pl', 0)):+,.2f}",
                    "P&L %": f"{float(p.get('unrealized_plpc', 0)):+.2%}",
                    "Weight": f"{mv/portfolio_value*100:.1f}%" if portfolio_value > 0 else "—",
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        with col2:
            # Allocation pie
            labels = [p.get("symbol") for p in positions]
            values = [float(p.get("market_value", 0)) for p in positions]
            if cash > 0:
                labels.append("CASH")
                values.append(cash)
            fig = go.Figure(go.Pie(labels=labels, values=values, hole=0.4))
            fig.update_layout(template="plotly_dark", height=300, margin=dict(t=10))
            st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No open positions")

    st.markdown("---")

    # ── Performance metrics ───────────────────────────────────────────────────
    st.subheader("Performance")
    p1, p2, p3, p4, p5 = st.columns(5)
    p1.metric("Total Return", f"{float(perf.get('total_return_pct', 0)):+.2f}%" if perf.get("total_return_pct") is not None else "—")
    p2.metric("Sharpe Ratio", f"{float(perf.get('sharpe_ratio', 0)):.2f}" if perf.get("sharpe_ratio") is not None else "—")
    p3.metric("Max Drawdown", f"{float(perf.get('max_drawdown_pct', 0)):.1f}%" if perf.get("max_drawdown_pct") is not None else "—")
    p4.metric("Win Rate", f"{float(perf.get('win_rate', 0))*100:.0f}%" if perf.get("win_rate") is not None else "—")
    p5.metric("Total Trades", perf.get("total_trades", "—"))

    st.markdown("---")

    # ── Closed positions history ──────────────────────────────────────────────
    st.subheader("Closed Positions")
    if closed:
        df_c = pd.DataFrame(closed)
        display = ["closed_at", "symbol", "action", "qty", "entry_price", "exit_price", "pnl", "pnl_pct", "hold_days"]
        display = [c for c in display if c in df_c.columns]
        for col in ["entry_price", "exit_price"]:
            if col in df_c.columns:
                df_c[col] = df_c[col].apply(lambda x: f"${float(x):.2f}" if x else "—")
        if "pnl" in df_c.columns:
            df_c["pnl"] = df_c["pnl"].apply(lambda x: f"${float(x):+.2f}" if x is not None else "—")
        if "pnl_pct" in df_c.columns:
            df_c["pnl_pct"] = df_c["pnl_pct"].apply(lambda x: f"{float(x):+.2f}%" if x is not None else "—")
        st.dataframe(df_c[display], use_container_width=True, hide_index=True)

        # P&L by symbol
        df_raw = pd.DataFrame(closed)
        if "pnl" in df_raw.columns and "symbol" in df_raw.columns:
            df_raw["pnl"] = df_raw["pnl"].astype(float)
            by_sym = df_raw.groupby("symbol")["pnl"].sum().reset_index().sort_values("pnl")
            fig = px.bar(
                by_sym, x="symbol", y="pnl",
                color="pnl", color_continuous_scale="RdYlGn",
                title="Total P&L by Symbol",
            )
            fig.update_layout(template="plotly_dark", height=280, margin=dict(t=40))
            st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No closed positions yet")
