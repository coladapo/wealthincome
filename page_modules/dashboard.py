"""
Dashboard Page — all data from real backend API and yfinance.
Zero hardcoded or random values.
"""

import streamlit as st
import plotly.graph_objects as go
import requests
from datetime import datetime
import yfinance as yf


@st.cache_data(ttl=300)
def _fetch_indices():
    symbols = ["SPY", "QQQ", "DIA", "IWM"]
    names = {"SPY": "S&P 500", "QQQ": "NASDAQ", "DIA": "DOW", "IWM": "Russell 2000"}
    results = []
    try:
        tickers = yf.Tickers(" ".join(symbols))
        for sym in symbols:
            try:
                info = tickers.tickers[sym].fast_info
                price = info.last_price
                prev = info.previous_close
                chg = ((price - prev) / prev * 100) if price and prev else 0
                results.append((names.get(sym, sym), price, chg))
            except Exception:
                pass
    except Exception:
        pass
    return results

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


def render_dashboard():
    st.title("Dashboard")

    status_data, err = _api("/status")
    if err:
        st.error(err)
        return

    account = status_data.get("account") or {}
    positions = status_data.get("positions") or []
    clock = status_data.get("clock") or {}
    last_cycle = status_data.get("last_cycle") or {}
    cfg = status_data.get("config") or {}
    trader_running = status_data.get("trader_running", False)

    perf_data, _ = _api("/performance")
    equity_data, _ = _api("/equity-curve", {"days": 30})
    trades_data, _ = _api("/trades", {"limit": 10})

    perf = perf_data or {}
    snapshots = (equity_data or {}).get("snapshots", [])
    recent_trades = (trades_data or {}).get("trades", [])

    # ── Status bar ───────────────────────────────────────────────────────────
    col_s1, col_s2, col_s3 = st.columns(3)
    with col_s1:
        if trader_running:
            st.success("Daemon Running")
        else:
            st.warning("Daemon Stopped")
    with col_s2:
        if clock.get("is_open"):
            st.success("Market Open")
        else:
            st.info(f"Market Closed — opens {clock.get('next_open', '—')[:16]}")
    with col_s3:
        paper = account.get("paper", True)
        st.info("PAPER MODE" if paper else "⚠️ LIVE MODE")

    st.markdown("---")

    # ── Account metrics ──────────────────────────────────────────────────────
    st.subheader("Account")
    c1, c2, c3, c4, c5 = st.columns(5)
    portfolio_value = float(account.get("portfolio_value", 0))
    cash = float(account.get("cash", 0))
    buying_power = float(account.get("buying_power", 0))
    raw_pnl = account.get("daily_pnl")
    raw_pct = account.get("daily_pnl_pct")
    daily_pnl = float(raw_pnl) if raw_pnl is not None else 0.0
    daily_pnl_pct = float(raw_pct) if raw_pct is not None else 0.0

    c1.metric("Portfolio Value", f"${portfolio_value:,.2f}")

    # Show buying power when cash is negative (margin in use)
    if cash < 0:
        c2.metric(
            "Buying Power",
            f"${buying_power:,.2f}",
            f"Margin: ${cash:,.0f}",
            delta_color="off",
        )
    else:
        c2.metric("Cash", f"${cash:,.2f}")

    if raw_pnl is not None:
        c3.metric(
            "Today's P&L",
            f"${daily_pnl:+,.2f}",
            f"{daily_pnl_pct:+.2f}%",
            delta_color="normal" if daily_pnl >= 0 else "inverse",
        )
    else:
        c3.metric("Today's P&L", "—", "no baseline yet")

    c4.metric("Open Positions", len(positions))
    c5.metric(
        "Win Rate",
        f"{float(perf['win_rate'])*100:.0f}%" if perf.get("win_rate") is not None else "—",
    )

    st.markdown("---")

    # ── Market indices ────────────────────────────────────────────────────────
    st.subheader("Market")

    indices = _fetch_indices()
    if indices:
        cols = st.columns(len(indices))
        for i, (name, price, chg) in enumerate(indices):
            cols[i].metric(name, f"${price:.2f}", f"{chg:+.2f}%",
                delta_color="normal" if chg >= 0 else "inverse")
    else:
        st.info("Market data temporarily unavailable")

    st.markdown("---")

    # ── Equity curve ─────────────────────────────────────────────────────────
    st.subheader("Equity Curve (30 days)")
    if snapshots:
        import pandas as pd
        df = pd.DataFrame(snapshots)
        df["snapshot_at"] = pd.to_datetime(df["snapshot_at"])
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df["snapshot_at"],
            y=df["portfolio_value"],
            mode="lines",
            line=dict(color="#1f77b4", width=2),
            fill="tozeroy",
            fillcolor="rgba(31,119,180,0.1)",
        ))
        fig.update_layout(
            template="plotly_dark",
            height=250,
            margin=dict(t=10, b=30),
            xaxis_title="Date",
            yaxis_title="Value ($)",
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Equity curve will appear after first trading cycles")

    st.markdown("---")

    # ── Last Claude cycle ─────────────────────────────────────────────────────
    st.subheader("Last Claude Cycle")
    if last_cycle:
        import json
        cycle_time = last_cycle.get("started_at", "")[:19]
        cycle_status = last_cycle.get("status", "")
        raw_json = last_cycle.get("raw_json")

        st.caption(f"{cycle_time} — {cycle_status}")

        if raw_json:
            try:
                output = json.loads(raw_json)
                st.markdown(f"**Market:** {output.get('market_summary', '—')}")
                st.markdown(f"**Notes:** {output.get('cycle_notes', '—')}")
                decisions = output.get("decisions", [])
                if decisions:
                    for d in decisions:
                        action = d.get("action", "").upper()
                        symbol = d.get("symbol", "")
                        conf = float(d.get("confidence", 0))
                        color = "green" if action == "BUY" else "red" if action == "SELL" else "gray"
                        st.markdown(
                            f"<span style='color:{color};font-weight:bold'>{action}</span> "
                            f"**{symbol}** — {conf:.0%} | {d.get('reasoning','')[:80]}",
                            unsafe_allow_html=True,
                        )
                else:
                    st.markdown("No trades — holding cash")
            except Exception:
                st.caption("Could not parse last cycle output")
    else:
        st.info("No cycles run yet — daemon will execute first cycle when market opens")

    st.markdown("---")

    # ── Open positions ────────────────────────────────────────────────────────
    st.subheader("Open Positions")
    if positions:
        import pandas as pd
        df = pd.DataFrame(positions)
        if "unrealized_pl" in df.columns:
            df["unrealized_pl"] = df["unrealized_pl"].apply(lambda x: f"${float(x):+,.2f}")
        if "unrealized_plpc" in df.columns:
            df["unrealized_plpc"] = df["unrealized_plpc"].apply(lambda x: f"{float(x):+.2%}")
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No open positions")

    st.markdown("---")

    # ── Recent trades ─────────────────────────────────────────────────────────
    st.subheader("Recent Trades")
    if recent_trades:
        import pandas as pd
        df = pd.DataFrame(recent_trades)
        cols = ["executed_at", "symbol", "action", "qty", "signal_price", "confidence", "order_status", "pnl"]
        cols = [c for c in cols if c in df.columns]
        if "signal_price" in df.columns:
            df["signal_price"] = df["signal_price"].apply(lambda x: f"${float(x):.2f}" if x else "—")
        if "confidence" in df.columns:
            df["confidence"] = df["confidence"].apply(lambda x: f"{float(x):.0%}" if x else "—")
        if "pnl" in df.columns:
            df["pnl"] = df["pnl"].apply(lambda x: f"${float(x):+.2f}" if x is not None else "—")
        st.dataframe(df[cols], use_container_width=True, hide_index=True)
    else:
        st.info("No trades yet")
