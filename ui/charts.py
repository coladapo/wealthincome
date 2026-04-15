"""
Chart rendering utilities
"""

import streamlit as st
import plotly.graph_objects as go
from typing import Optional, Dict, Any


def render_stock_chart(symbol: str, data: Optional[Dict] = None):
    """Render a stock price chart"""
    if not data:
        st.info(f"No chart data available for {symbol}")
        return

    history = data.get("history", {})
    closes = history.get("Close", {})

    if not closes:
        st.info(f"No price history for {symbol}")
        return

    dates = list(closes.keys())
    prices = list(closes.values())

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=dates, y=prices, mode="lines", name=symbol))
    fig.update_layout(
        title=f"{symbol} Price",
        xaxis_title="Date",
        yaxis_title="Price ($)",
        height=400,
        template="plotly_dark",
    )
    st.plotly_chart(fig, use_container_width=True)


def render_portfolio_chart(portfolio_data: Optional[Dict] = None):
    """Render portfolio allocation pie chart"""
    if not portfolio_data:
        st.info("No portfolio data available")
        return

    positions = portfolio_data.get("positions", {})
    if not positions:
        st.info("No open positions to chart")
        return

    labels = list(positions.keys())
    values = [p.get("market_value", 0) for p in positions.values()]

    fig = go.Figure(data=[go.Pie(labels=labels, values=values, hole=0.4)])
    fig.update_layout(title="Portfolio Allocation", height=400, template="plotly_dark")
    st.plotly_chart(fig, use_container_width=True)
