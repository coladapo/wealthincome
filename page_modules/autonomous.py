"""
Autonomous Trader Page — Read-only dashboard that talks to the backend API.
All state lives in the backend daemon + SQLite. This page only displays and sends commands.
"""

import streamlit as st
import pandas as pd
import json
import requests
from datetime import datetime

API_BASE = "http://localhost:8000"


def _api(method: str, path: str, **kwargs):
    """Make an API call. Returns (data, error_str)."""
    try:
        resp = requests.request(method, f"{API_BASE}{path}", timeout=10, **kwargs)
        resp.raise_for_status()
        return resp.json(), None
    except requests.exceptions.ConnectionError:
        return None, "Backend API not running — start it with: `python -m backend.api`"
    except requests.exceptions.HTTPError as e:
        return None, f"API error {e.response.status_code}: {e.response.text[:200]}"
    except Exception as e:
        return None, str(e)


def render():
    st.title("Autonomous Trader")

    # ─── API connection check ────────────────────────────────────────────────
    status_data, err = _api("GET", "/status")
    if err:
        st.error(err)
        st.info("Start the backend: `ALPACA_API_KEY=... ALPACA_SECRET_KEY=... python -m backend.api`")
        return

    cfg = status_data.get("config", {})
    account = status_data.get("account") or {}
    positions = status_data.get("positions") or []
    clock = status_data.get("clock") or {}
    last_cycle = status_data.get("last_cycle") or {}
    trader_running = status_data.get("trader_running", False)
    paper = account.get("paper", True)

    # Mode badge
    mode_label = "PAPER" if paper else "LIVE"
    badge_color = "green" if paper else "red"
    st.markdown(
        f"<span style='background:{badge_color};color:white;padding:3px 10px;"
        f"border-radius:4px;font-weight:bold;font-size:13px'>{mode_label} MODE</span>",
        unsafe_allow_html=True,
    )
    st.markdown("---")

    # ─── Control Bar ────────────────────────────────────────────────────────
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        if trader_running:
            if st.button("⏹ Stop Daemon", type="secondary", use_container_width=True):
                data, err = _api("POST", "/stop")
                if err:
                    st.error(err)
                else:
                    st.success("Stop signal sent — daemon will halt after current cycle")
                    st.rerun()
        else:
            if st.button("▶ Start Daemon", type="primary", use_container_width=True):
                data, err = _api("POST", "/start")
                if err:
                    st.error(err)
                else:
                    st.success("Daemon started — run `python backend/trader.py` in terminal")
                    st.rerun()

    with col2:
        if st.button("⚡ Run Cycle Now", use_container_width=True):
            data, err = _api("POST", "/trigger")
            if err:
                st.error(err)
            else:
                st.info("Cycle triggered — refresh in ~30s for results")

    with col3:
        if st.button("🔄 Refresh", use_container_width=True):
            st.rerun()

    with col4:
        # Trigger status
        ts_data, _ = _api("GET", "/trigger/status")
        if ts_data and ts_data.get("running"):
            st.warning("⏳ Cycle running...")

    st.markdown("---")

    # ─── Metrics ────────────────────────────────────────────────────────────
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Daemon", "Running ✓" if trader_running else "Stopped")
    c2.metric("Market", "Open ✓" if clock.get("is_open") else "Closed")
    c3.metric("Portfolio", f"${account.get('portfolio_value', 0):,.2f}")
    c4.metric("Cash", f"${account.get('cash', 0):,.2f}")
    c5.metric("Positions", len(positions))

    st.markdown("---")

    # ─── Positions ──────────────────────────────────────────────────────────
    st.subheader("Open Positions")
    if positions:
        df = pd.DataFrame(positions)
        if "unrealized_pl" in df.columns:
            df["unrealized_pl"] = df["unrealized_pl"].apply(lambda x: f"${x:+,.2f}")
        if "unrealized_plpc" in df.columns:
            df["unrealized_plpc"] = df["unrealized_plpc"].apply(lambda x: f"{x:+.2%}")
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No open positions")

    st.markdown("---")

    # ─── Two-column: Risk Config | Watchlist ────────────────────────────────
    left, right = st.columns(2)

    with left:
        st.subheader("Risk Config")
        with st.form("risk_form"):
            max_pos = st.slider(
                "Max position size (%)", 1, 20,
                int(float(cfg.get("max_position_pct", 0.08)) * 100),
            )
            max_open = st.slider(
                "Max open positions", 1, 20,
                int(cfg.get("max_open_positions", 8)),
            )
            daily_limit = st.slider(
                "Daily loss limit (%)", 1, 20,
                int(float(cfg.get("daily_loss_limit_pct", 0.05)) * 100),
            )
            conf_thresh = st.slider(
                "Confidence threshold (%)", 50, 95,
                int(float(cfg.get("confidence_threshold", 0.72)) * 100),
            )
            poll = st.selectbox(
                "Poll interval",
                [60, 300, 600, 900, 1800],
                index=[60, 300, 600, 900, 1800].index(
                    int(cfg.get("poll_interval", 300))
                ) if int(cfg.get("poll_interval", 300)) in [60, 300, 600, 900, 1800] else 1,
                format_func=lambda x: f"{x//60} min" if x >= 60 else f"{x}s",
            )
            market_hours_only = st.toggle(
                "Trade market hours only",
                value=cfg.get("trade_only_market_hours", "true") == "true",
                help="Uncheck to allow test cycles when market is closed",
            )
            if st.form_submit_button("Update Config"):
                updates = {
                    "max_position_pct": str(max_pos / 100),
                    "max_open_positions": str(max_open),
                    "daily_loss_limit_pct": str(daily_limit / 100),
                    "confidence_threshold": str(conf_thresh / 100),
                    "poll_interval": str(poll),
                    "trade_only_market_hours": "true" if market_hours_only else "false",
                }
                data, err = _api("POST", "/config", json={"updates": updates})
                if err:
                    st.error(err)
                else:
                    st.success("Config updated")
                    st.rerun()

    with right:
        st.subheader("Watchlist")
        current_watchlist = cfg.get("watchlist", "")
        symbols_list = "\n".join(s.strip() for s in current_watchlist.split(",") if s.strip())
        new_watchlist_str = st.text_area(
            "Symbols to trade (one per line)",
            value=symbols_list,
            height=200,
        )
        if st.button("Update Watchlist"):
            symbols = [s.strip().upper() for s in new_watchlist_str.splitlines() if s.strip()]
            data, err = _api("POST", "/config", json={"updates": {"watchlist": ",".join(symbols)}})
            if err:
                st.error(err)
            else:
                st.success(f"Watchlist updated: {len(symbols)} symbols")
                st.rerun()

    st.markdown("---")

    # ─── Claude's Last Reasoning ────────────────────────────────────────────
    raw_json = last_cycle.get("raw_json")
    cycle_time = last_cycle.get("started_at", "")
    cycle_status = last_cycle.get("status", "")

    if raw_json:
        try:
            last_output = json.loads(raw_json)
            label = f"Claude's Last Reasoning — {cycle_time[:19]} ({cycle_status})"
            with st.expander(label, expanded=True):
                st.markdown(f"**Market Summary:** {last_output.get('market_summary', '—')}")
                st.markdown(f"**Cycle Notes:** {last_output.get('cycle_notes', '—')}")
                decisions = last_output.get("decisions", [])
                if decisions:
                    st.markdown("**Decisions:**")
                    for d in decisions:
                        action = d.get("action", "").upper()
                        symbol = d.get("symbol", "")
                        conf = float(d.get("confidence", 0))
                        reason = d.get("reasoning", "")
                        size = float(d.get("position_size_pct", 0))
                        color = "green" if action == "BUY" else "red" if action == "SELL" else "gray"
                        st.markdown(
                            f"<span style='color:{color};font-weight:bold'>{action}</span> "
                            f"**{symbol}** — {conf:.0%} confidence | {size:.0%} position | {reason}",
                            unsafe_allow_html=True,
                        )
                else:
                    st.markdown("**Decisions:** No trades — holding cash")
        except Exception:
            st.text(f"Last cycle: {cycle_time[:19]} | Status: {cycle_status}")
    elif cycle_time:
        st.info(f"Last cycle: {cycle_time[:19]} — status: {cycle_status}")

    st.markdown("---")

    # ─── Trade Log ──────────────────────────────────────────────────────────
    trades_data, _ = _api("GET", "/trades", params={"limit": 50})
    trades = (trades_data or {}).get("trades", [])

    st.subheader(f"Trade Log ({len(trades)} recent trades)")
    if trades:
        df = pd.DataFrame(trades)
        display_cols = ["executed_at", "symbol", "action", "qty", "signal_price",
                        "confidence", "order_status", "take_profit", "stop_loss"]
        display_cols = [c for c in display_cols if c in df.columns]
        if "confidence" in df.columns:
            df["confidence"] = df["confidence"].apply(
                lambda x: f"{float(x):.0%}" if x is not None else "—"
            )
        if "signal_price" in df.columns:
            df["signal_price"] = df["signal_price"].apply(
                lambda x: f"${float(x):.2f}" if x is not None else "—"
            )
        st.dataframe(df[display_cols], use_container_width=True, hide_index=True)
    else:
        st.info("No trades executed yet")

    # ─── Error Log ──────────────────────────────────────────────────────────
    errors_data, _ = _api("GET", "/errors")
    errors = (errors_data or {}).get("errors", [])
    if errors:
        with st.expander(f"Error Log ({len(errors)} entries)", expanded=False):
            st.dataframe(pd.DataFrame(errors), use_container_width=True, hide_index=True)

    # ─── Token Usage ────────────────────────────────────────────────────────
    with st.expander("Token Usage", expanded=False):
        col_d, col_w, col_all = st.columns(3)
        for label, days, col in [("Today", 1, col_d), ("This Week", 7, col_w), ("All Time", 3650, col_all)]:
            u_data, _ = _api("GET", "/usage", params={"days": days})
            if u_data:
                with col:
                    st.markdown(f"**{label}**")
                    st.metric("Cycles", u_data.get("cycles", 0))
                    total = u_data.get("total_tokens", 0)
                    st.metric("Total Tokens", f"{total:,}")
                    st.metric("Input", f"{u_data.get('input_tokens', 0):,}")
                    st.metric("Output", f"{u_data.get('output_tokens', 0):,}")
                    st.metric("Cache Read", f"{u_data.get('cache_read_tokens', 0):,}")
                    avg_ms = u_data.get("avg_duration_ms", 0)
                    st.metric("Avg Duration", f"{avg_ms/1000:.1f}s" if avg_ms else "—")

    # ─── Market Clock ───────────────────────────────────────────────────────
    with st.expander("Market Clock & Cycle History"):
        st.write(f"**Next open:** {clock.get('next_open', '—')}")
        st.write(f"**Next close:** {clock.get('next_close', '—')}")

        cycles_data, _ = _api("GET", "/cycles", params={"limit": 10})
        cycles = (cycles_data or {}).get("cycles", [])
        if cycles:
            df = pd.DataFrame(cycles)
            display = ["started_at", "status", "market_open", "decisions", "market_summary"]
            display = [c for c in display if c in df.columns]
            st.dataframe(df[display], use_container_width=True, hide_index=True)
