"""
Trading Page — manual order entry via Alpaca API.
All data from live backend; no in-memory engine.
"""

import streamlit as st
import pandas as pd
import requests
from datetime import datetime

API_BASE = "http://localhost:8000"


def _api(method, path, **kwargs):
    try:
        r = requests.request(method, f"{API_BASE}{path}", timeout=10, **kwargs)
        r.raise_for_status()
        return r.json(), None
    except requests.exceptions.ConnectionError:
        return None, "Backend API not running"
    except requests.exceptions.HTTPError as e:
        return None, f"API error {e.response.status_code}: {e.response.text[:200]}"
    except Exception as e:
        return None, str(e)


def _place_order(symbol, side, qty, order_type="market", limit_price=None):
    """Submit order directly to Alpaca."""
    try:
        from core.alpaca_client import AlpacaClient, AlpacaOrderSide
        api_key = os.environ.get("ALPACA_API_KEY")
        secret_key = os.environ.get("ALPACA_SECRET_KEY")
        paper = os.environ.get("ALPACA_PAPER", "true").lower() == "true"
        if not api_key or not secret_key:
            return None, "Alpaca API keys not configured"
        alpaca = AlpacaClient(api_key=api_key, secret_key=secret_key, paper=paper)
        order_side = AlpacaOrderSide.BUY if side == "buy" else AlpacaOrderSide.SELL
        if order_type == "limit" and limit_price:
            order = alpaca.place_limit_order(symbol=symbol, qty=qty, side=order_side, limit_price=limit_price)
        else:
            order = alpaca.place_market_order(symbol=symbol, qty=qty, side=order_side)
        return order, None
    except Exception as e:
        return None, str(e)


def render_trading():
    st.title("Trading")

    status_data, err = _api("GET", "/status")
    if err:
        st.error(err)
        return

    account = status_data.get("account") or {}
    positions = status_data.get("positions") or []
    clock = status_data.get("clock") or {}
    paper = account.get("paper", True)

    portfolio_value = float(account.get("portfolio_value", 0))
    cash = float(account.get("cash", 0))
    buying_power = float(account.get("buying_power", 0))

    # Status bar
    col_s1, col_s2, col_s3 = st.columns(3)
    with col_s1:
        st.info("PAPER MODE" if paper else "⚠️ LIVE MODE")
    with col_s2:
        if clock.get("is_open"):
            st.success("Market Open")
        else:
            st.warning(f"Market Closed — opens {str(clock.get('next_open', ''))[:16]}")
    with col_s3:
        st.metric("Buying Power", f"${buying_power:,.2f}")

    st.markdown("---")

    # ── Account summary ──────────────────────────────────────────────────────
    c1, c2, c3 = st.columns(3)
    c1.metric("Portfolio Value", f"${portfolio_value:,.2f}")
    c2.metric("Cash", f"${cash:,.2f}")
    c3.metric("Open Positions", len(positions))

    st.markdown("---")

    # ── Order form ───────────────────────────────────────────────────────────
    col_form, col_positions = st.columns([1, 1])

    with col_form:
        st.subheader("Place Order")
        with st.form("order_form"):
            symbol = st.text_input("Symbol", placeholder="e.g. AAPL").upper().strip()
            side = st.selectbox("Side", ["buy", "sell"])
            qty = st.number_input("Quantity (shares)", min_value=1, value=1, step=1)
            order_type = st.selectbox("Order Type", ["market", "limit"])
            limit_price = None
            if order_type == "limit":
                limit_price = st.number_input("Limit Price", min_value=0.01, value=100.00, step=0.01, format="%.2f")

            submitted = st.form_submit_button("Submit Order", type="primary")

            if submitted:
                if not symbol:
                    st.error("Symbol required")
                elif not clock.get("is_open") and order_type == "market":
                    st.warning("Market is closed — market orders will queue for next open")
                    order, err2 = _place_order(symbol, side, qty, order_type, limit_price)
                    if err2:
                        st.error(f"Order failed: {err2}")
                    else:
                        st.success(f"Order queued: {side.upper()} {qty} {symbol}")
                else:
                    order, err2 = _place_order(symbol, side, qty, order_type, limit_price)
                    if err2:
                        st.error(f"Order failed: {err2}")
                    else:
                        st.success(f"Order submitted: {side.upper()} {qty} {symbol}")

    with col_positions:
        st.subheader("Open Positions")
        if positions:
            rows = []
            for p in positions:
                mv = float(p.get("market_value", 0))
                rows.append({
                    "Symbol": p.get("symbol"),
                    "Qty": p.get("qty"),
                    "Entry": f"${float(p.get('avg_entry_price', 0)):.2f}",
                    "Current": f"${float(p.get('current_price', 0)):.2f}",
                    "Market Value": f"${mv:,.2f}",
                    "Unrealized P&L": f"${float(p.get('unrealized_pl', 0)):+,.2f}",
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.info("No open positions")

    st.markdown("---")

    # ── Recent orders from DB ────────────────────────────────────────────────
    st.subheader("Order History")
    orders_data, _ = _api("GET", "/orders", params={"limit": 50})
    orders = (orders_data or {}).get("orders", [])

    if orders:
        df = pd.DataFrame(orders)
        display_cols = ["submitted_at", "symbol", "side", "order_type", "qty",
                        "limit_price", "fill_price", "status"]
        display_cols = [c for c in display_cols if c in df.columns]
        for col in ["limit_price", "fill_price"]:
            if col in df.columns:
                df[col] = df[col].apply(lambda x: f"${float(x):.2f}" if x is not None else "—")
        st.dataframe(df[display_cols], use_container_width=True, hide_index=True)
    else:
        st.info("No orders yet")
