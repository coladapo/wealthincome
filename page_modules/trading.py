"""
Trading Page - Execute trades and manage positions
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime
from typing import Dict, List, Any, Optional

from core.trading_engine import TradingEngine, OrderType, OrderSide
from ui.components import render_metric_card, render_alert_banner
from ui.navigation import render_page_header

def render_trading():
    """Render trading page"""
    
    render_page_header(
        "📈 Trading",
        "Execute trades and manage your portfolio",
        actions=[
            {"label": "🔄 Refresh", "key": "refresh_trading", "callback": refresh_trading_data}
        ]
    )
    
    # Get trading engine from session
    trading_engine = st.session_state.get('trading_engine')
    data_manager = st.session_state.get('data_manager')
    
    if not trading_engine:
        st.error("Trading engine not initialized")
        return
    
    # Portfolio Overview
    render_portfolio_overview(trading_engine)
    
    st.markdown("---")
    
    # Trading Interface
    col1, col2 = st.columns([1, 1])
    
    with col1:
        render_trading_form(trading_engine, data_manager)
    
    with col2:
        render_current_positions(trading_engine)
    
    st.markdown("---")
    
    # Recent Orders and Transactions
    render_order_history(trading_engine)

def render_portfolio_overview(trading_engine: TradingEngine):
    """Render portfolio overview section"""
    
    st.markdown("### 💼 Portfolio Overview")
    
    portfolio_summary = trading_engine.get_portfolio_summary()
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        render_metric_card(
            "Total Value",
            f"${portfolio_summary['total_value']:,.2f}",
            delta=f"{portfolio_summary['total_return_pct']:+.2f}%",
            icon="💰"
        )
    
    with col2:
        render_metric_card(
            "Cash Available",
            f"${portfolio_summary['cash']:,.2f}",
            icon="💵"
        )
    
    with col3:
        render_metric_card(
            "Total P&L",
            f"${portfolio_summary['total_pnl']:,.2f}",
            delta="Unrealized + Realized",
            icon="📊"
        )
    
    with col4:
        render_metric_card(
            "Positions",
            str(portfolio_summary['positions_count']),
            delta=f"{portfolio_summary['active_orders']} active orders",
            icon="📋"
        )

def render_trading_form(trading_engine: TradingEngine, data_manager):
    """Render trading order form"""
    
    st.markdown("### 🎯 Place Order")
    
    with st.form("trading_form"):
        # Symbol selection
        symbol = st.text_input(
            "Symbol",
            value=st.session_state.get('selected_symbol', ''),
            placeholder="Enter stock symbol (e.g., AAPL)"
        ).upper()
        
        # Order side
        side = st.selectbox(
            "Order Type",
            ["Buy", "Sell"],
            index=0 if st.session_state.get('suggested_action') == 'buy' else 1
        )
        
        # Quantity
        quantity = st.number_input(
            "Quantity",
            min_value=1,
            value=100,
            step=1
        )
        
        # Order type
        order_type = st.selectbox(
            "Order Method",
            ["Market Order", "Limit Order"],
            help="Market orders execute immediately at current price"
        )
        
        # Limit price (if limit order)
        limit_price = None
        if order_type == "Limit Order":
            limit_price = st.number_input(
                "Limit Price",
                min_value=0.01,
                value=100.00,
                step=0.01,
                format="%.2f"
            )
        
        # Current price display
        if symbol and data_manager:
            try:
                stock_data = data_manager.get_stock_data([symbol])
                if symbol in stock_data and stock_data[symbol]:
                    current_price = stock_data[symbol].get('info', {}).get('regularMarketPrice', 0)
                    if current_price:
                        st.info(f"Current Price: **${current_price:.2f}**")
                        
                        # Estimate order value
                        estimated_value = quantity * (limit_price if limit_price else current_price)
                        st.caption(f"Estimated Order Value: ${estimated_value:,.2f}")
            except Exception as e:
                st.warning(f"Could not fetch current price: {e}")
        
        # Submit button
        submitted = st.form_submit_button("🚀 Place Order", type="primary")
        
        if submitted:
            if not symbol:
                st.error("Please enter a stock symbol")
            else:
                # Place the order
                order_side = OrderSide.BUY if side == "Buy" else OrderSide.SELL
                order_type_enum = OrderType.MARKET if order_type == "Market Order" else OrderType.LIMIT
                
                try:
                    order_id = trading_engine.place_order(
                        symbol=symbol,
                        side=order_side,
                        quantity=quantity,
                        order_type=order_type_enum,
                        price=limit_price
                    )
                    
                    st.success(f"Order placed successfully! Order ID: {order_id}")
                    st.rerun()
                    
                except Exception as e:
                    st.error(f"Failed to place order: {e}")

def render_current_positions(trading_engine: TradingEngine):
    """Render current positions"""
    
    st.markdown("### 📊 Current Positions")
    
    positions = trading_engine.get_positions()
    
    if not positions:
        st.info("No current positions")
        return
    
    for symbol, position in positions.items():
        with st.container():
            col1, col2, col3 = st.columns([2, 1, 1])
            
            with col1:
                st.markdown(f"**{symbol}**")
                st.caption(f"{position.quantity} shares @ ${position.avg_price:.2f}")
            
            with col2:
                st.metric(
                    "Current Price",
                    f"${position.current_price:.2f}",
                    delta=f"${position.current_price - position.avg_price:.2f}"
                )
            
            with col3:
                pnl_color = "normal" if position.unrealized_pnl >= 0 else "inverse"
                st.metric(
                    "Unrealized P&L",
                    f"${position.unrealized_pnl:.2f}",
                    delta=f"{(position.unrealized_pnl/position.cost_basis)*100:.1f}%" if position.cost_basis > 0 else "0%",
                    delta_color=pnl_color
                )
            
            # Quick action buttons
            col1, col2 = st.columns(2)
            with col1:
                if st.button(f"Sell Half", key=f"sell_half_{symbol}"):
                    try:
                        trading_engine.place_order(
                            symbol=symbol,
                            side=OrderSide.SELL,
                            quantity=position.quantity / 2,
                            order_type=OrderType.MARKET
                        )
                        st.success("Sell order placed")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")
            
            with col2:
                if st.button(f"Sell All", key=f"sell_all_{symbol}"):
                    try:
                        trading_engine.place_order(
                            symbol=symbol,
                            side=OrderSide.SELL,
                            quantity=position.quantity,
                            order_type=OrderType.MARKET
                        )
                        st.success("Sell order placed")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")
            
            st.markdown("---")

def render_order_history(trading_engine: TradingEngine):
    """Render order history and transactions"""
    
    st.markdown("### 📋 Order History")
    
    # Recent orders
    orders = trading_engine.get_orders()
    
    if orders:
        orders_data = []
        for order in orders[:10]:  # Show last 10 orders
            orders_data.append({
                "Time": order.created_at.strftime("%Y-%m-%d %H:%M"),
                "Symbol": order.symbol,
                "Side": order.side.value.upper(),
                "Quantity": f"{order.quantity:,.0f}",
                "Type": order.order_type.value.upper(),
                "Price": f"${order.price:.2f}" if order.price else "Market",
                "Status": order.status.value.upper(),
                "Filled": f"${order.filled_price:.2f}" if order.filled_price else "-"
            })
        
        df = pd.DataFrame(orders_data)
        st.dataframe(df, use_container_width=True)
    else:
        st.info("No orders yet")
    
    # Transaction history
    if st.expander("📊 Transaction History", expanded=False):
        transactions = trading_engine.get_transaction_history(days=30)
        
        if transactions:
            tx_data = []
            for tx in transactions:
                tx_data.append({
                    "Date": tx['timestamp'].strftime("%Y-%m-%d") if hasattr(tx['timestamp'], 'strftime') else str(tx['timestamp']),
                    "Symbol": tx['symbol'],
                    "Side": tx['side'].upper(),
                    "Quantity": f"{tx['quantity']:,.0f}",
                    "Price": f"${tx['price']:.2f}",
                    "Total": f"${tx['total']:,.2f}"
                })
            
            df_tx = pd.DataFrame(tx_data)
            st.dataframe(df_tx, use_container_width=True)
        else:
            st.info("No transactions yet")

def refresh_trading_data():
    """Refresh trading data"""
    st.cache_data.clear()
    st.success("Trading data refreshed!")
    st.rerun()