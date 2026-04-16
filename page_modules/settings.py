"""
Settings Page — read/write config via backend API.
No auth manager, no hardcoded values.
"""

import os
import streamlit as st
import requests

API_BASE = "http://localhost:8000"


def _api(method, path, **kwargs):
    try:
        r = requests.request(method, f"{API_BASE}{path}", timeout=8, **kwargs)
        r.raise_for_status()
        return r.json(), None
    except requests.exceptions.ConnectionError:
        return None, "Backend API not running"
    except requests.exceptions.HTTPError as e:
        return None, f"API error {e.response.status_code}: {e.response.text[:200]}"
    except Exception as e:
        return None, str(e)


def render_settings():
    st.title("Settings")

    status_data, err = _api("GET", "/status")
    if err:
        st.error(err)
        return

    cfg = status_data.get("config") or {}

    # ── Trader Configuration ─────────────────────────────────────────────────
    st.subheader("Autonomous Trader Configuration")
    st.caption("These settings control the daemon behaviour. Changes take effect on the next cycle.")

    with st.form("config_form"):
        col1, col2 = st.columns(2)

        with col1:
            max_pos_pct = st.slider(
                "Max position size (% of portfolio)",
                min_value=1, max_value=25,
                value=int(float(cfg.get("max_position_pct", 0.08)) * 100),
                format="%d%%",
            )
            max_open = st.slider(
                "Max open positions",
                min_value=1, max_value=20,
                value=int(cfg.get("max_open_positions", 8)),
            )
            daily_loss_pct = st.slider(
                "Daily loss limit (% of portfolio)",
                min_value=1, max_value=15,
                value=int(float(cfg.get("daily_loss_limit_pct", 0.05)) * 100),
                format="%d%%",
            )

        with col2:
            confidence = st.slider(
                "Confidence threshold (%)",
                min_value=50, max_value=95,
                value=int(float(cfg.get("confidence_threshold", 0.72)) * 100),
                format="%d%%",
            )
            poll_options = [60, 300, 600, 900, 1800]
            current_poll = int(cfg.get("poll_interval", 300))
            poll_idx = poll_options.index(current_poll) if current_poll in poll_options else 1
            poll_interval = st.selectbox(
                "Poll interval",
                poll_options,
                index=poll_idx,
                format_func=lambda x: f"{x // 60} min" if x >= 60 else f"{x}s",
            )
            market_hours_only = st.toggle(
                "Trade market hours only",
                value=cfg.get("trade_only_market_hours", "true") == "true",
            )

        watchlist_raw = cfg.get("watchlist", "")
        watchlist_lines = "\n".join(s.strip() for s in watchlist_raw.split(",") if s.strip())
        new_watchlist = st.text_area(
            "Watchlist (one symbol per line)",
            value=watchlist_lines,
            height=150,
        )

        if st.form_submit_button("Save Configuration", type="primary"):
            symbols = [s.strip().upper() for s in new_watchlist.splitlines() if s.strip()]
            updates = {
                "max_position_pct": str(max_pos_pct / 100),
                "max_open_positions": str(max_open),
                "daily_loss_limit_pct": str(daily_loss_pct / 100),
                "confidence_threshold": str(confidence / 100),
                "poll_interval": str(poll_interval),
                "trade_only_market_hours": "true" if market_hours_only else "false",
                "watchlist": ",".join(symbols),
            }
            data, save_err = _api("POST", "/config", json={"updates": updates})
            if save_err:
                st.error(save_err)
            else:
                st.success(f"Configuration saved ({len(updates)} values updated)")
                st.rerun()

    st.markdown("---")

    # ── System info ──────────────────────────────────────────────────────────
    st.subheader("System Information")

    account = status_data.get("account") or {}
    clock = status_data.get("clock") or {}

    col1, col2, col3 = st.columns(3)
    col1.metric("Mode", "PAPER" if account.get("paper", True) else "LIVE")
    col2.metric("Account Status", str(account.get("status", "—")).upper())
    col3.metric("Market", "Open" if clock.get("is_open") else "Closed")

    st.markdown("---")

    # ── Alpaca connection ────────────────────────────────────────────────────
    st.subheader("Alpaca Connection")

    api_key = os.environ.get("ALPACA_API_KEY", "")
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
    paper_mode = os.environ.get("ALPACA_PAPER", "true").lower() == "true"

    if api_key:
        st.success(f"API Key configured: `{api_key[:8]}...{api_key[-4:]}`")
    else:
        st.error("ALPACA_API_KEY not set in .env")

    if secret_key:
        st.success("Secret Key: configured")
    else:
        st.error("ALPACA_SECRET_KEY not set in .env")

    st.info(f"Trading mode: {'Paper (simulation)' if paper_mode else 'Live (real money)'}")

    st.markdown("---")

    # ── Usage summary ────────────────────────────────────────────────────────
    st.subheader("Claude API Usage")
    usage_data, _ = _api("GET", "/usage", params={"days": 30})
    if usage_data:
        u1, u2, u3, u4 = st.columns(4)
        u1.metric("Cycles (30d)", usage_data.get("cycles", 0))
        u2.metric("Total Tokens", f"{usage_data.get('total_tokens', 0):,}")
        u3.metric("Cache Read Tokens", f"{usage_data.get('cache_read_tokens', 0):,}")
        cost = usage_data.get("total_cost_usd", 0) or 0
        u4.metric("Total Cost (30d)", f"${cost:.4f}")
    else:
        st.info("Usage data unavailable")
