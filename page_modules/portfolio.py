"""
Portfolio Page - Portfolio management and performance tracking
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta
from typing import Dict, List, Any
import numpy as np

from ui.components import render_metric_card, render_confidence_indicator
from ui.navigation import render_page_header

def render_portfolio():
    """Render portfolio management page"""
    
    render_page_header(
        "💼 Portfolio",
        "Manage your portfolio and track performance",
        actions=[
            {"label": "🔄 Refresh", "key": "refresh_portfolio", "callback": refresh_portfolio_data},
            {"label": "📊 Export Report", "key": "export_portfolio", "callback": export_portfolio_report}
        ]
    )
    
    trading_engine = st.session_state.get('trading_engine')
    data_manager = st.session_state.get('data_manager')
    
    if not trading_engine:
        st.error("Trading engine not initialized")
        return
    
    # Portfolio Summary
    render_portfolio_summary(trading_engine)
    
    st.markdown("---")
    
    # Performance Charts
    render_performance_charts(trading_engine)
    
    st.markdown("---")
    
    # Holdings Analysis
    col1, col2 = st.columns([2, 1])
    
    with col1:
        render_holdings_table(trading_engine, data_manager)
    
    with col2:
        render_allocation_charts(trading_engine)
    
    st.markdown("---")
    
    # Performance Analytics
    render_performance_analytics(trading_engine)

def render_portfolio_summary(trading_engine):
    """Render portfolio summary metrics"""
    
    st.markdown("### 📊 Portfolio Summary")
    
    portfolio_summary = trading_engine.get_portfolio_summary()
    
    # Key metrics
    col1, col2, col3, col4, col5 = st.columns(5)
    
    with col1:
        render_metric_card(
            "Total Value",
            f"${portfolio_summary['total_value']:,.2f}",
            icon="💰"
        )
    
    with col2:
        render_metric_card(
            "Cash",
            f"${portfolio_summary['cash']:,.2f}",
            delta=f"{(portfolio_summary['cash']/portfolio_summary['total_value'])*100:.1f}% allocation",
            icon="💵"
        )
    
    with col3:
        pnl_color = "normal" if portfolio_summary['total_pnl'] >= 0 else "inverse"
        render_metric_card(
            "Total P&L",
            f"${portfolio_summary['total_pnl']:,.2f}",
            delta=f"{portfolio_summary['total_return_pct']:+.2f}%",
            icon="📈" if portfolio_summary['total_pnl'] >= 0 else "📉"
        )
    
    with col4:
        render_metric_card(
            "Positions",
            str(portfolio_summary['positions_count']),
            icon="📋"
        )
    
    with col5:
        render_metric_card(
            "Buying Power",
            f"${portfolio_summary['buying_power']:,.2f}",
            icon="🛒"
        )

def render_performance_charts(trading_engine):
    """Render portfolio performance charts"""
    
    st.markdown("### 📈 Performance History")
    
    # Generate mock historical data for demonstration
    dates = pd.date_range(start='2024-01-01', end=datetime.now(), freq='D')
    portfolio_summary = trading_engine.get_portfolio_summary()
    
    # Simulate portfolio value history
    np.random.seed(42)  # For consistent results
    initial_value = 100000
    returns = np.random.normal(0.0008, 0.02, len(dates))  # Daily returns
    cumulative_returns = np.cumprod(1 + returns)
    portfolio_values = initial_value * cumulative_returns
    
    # Adjust final value to match current portfolio
    portfolio_values = portfolio_values * (portfolio_summary['total_value'] / portfolio_values[-1])
    
    col1, col2 = st.columns([2, 1])
    
    with col1:
        # Portfolio value over time
        fig = go.Figure()
        
        fig.add_trace(go.Scatter(
            x=dates,
            y=portfolio_values,
            mode='lines',
            name='Portfolio Value',
            line=dict(color='#1f77b4', width=2),
            fill='tonexty',
            fillcolor='rgba(31, 119, 180, 0.1)'
        ))
        
        fig.update_layout(
            title="Portfolio Value Over Time",
            xaxis_title="Date",
            yaxis_title="Portfolio Value ($)",
            template="plotly_dark",
            height=400
        )
        
        st.plotly_chart(fig, use_container_width=True, key="portfolio_value_chart")
    
    with col2:
        # Monthly returns
        monthly_returns = pd.Series(returns, index=dates).resample('M').apply(lambda x: (1 + x).prod() - 1)
        monthly_returns = monthly_returns * 100  # Convert to percentage
        
        fig_monthly = go.Figure()
        
        colors = ['green' if x >= 0 else 'red' for x in monthly_returns]
        
        fig_monthly.add_trace(go.Bar(
            x=monthly_returns.index,
            y=monthly_returns,
            marker_color=colors,
            name='Monthly Returns'
        ))
        
        fig_monthly.update_layout(
            title="Monthly Returns (%)",
            xaxis_title="Month",
            yaxis_title="Return (%)",
            template="plotly_dark",
            height=400
        )
        
        st.plotly_chart(fig_monthly, use_container_width=True, key="monthly_returns_chart")

def render_holdings_table(trading_engine, data_manager):
    """Render detailed holdings table"""
    
    st.markdown("### 📋 Current Holdings")
    
    positions = trading_engine.get_positions()
    
    if not positions:
        st.info("No current positions")
        return
    
    # Update market prices
    if data_manager and positions:
        symbols = list(positions.keys())
        try:
            stock_data = data_manager.get_stock_data(symbols)
            price_data = {}
            for symbol in symbols:
                if symbol in stock_data and stock_data[symbol]:
                    price_data[symbol] = stock_data[symbol].get('info', {}).get('regularMarketPrice', 0)
            
            trading_engine.update_market_prices(price_data)
        except Exception as e:
            st.warning(f"Could not update prices: {e}")
    
    # Create holdings dataframe
    holdings_data = []
    for symbol, position in positions.items():
        pnl_pct = (position.unrealized_pnl / position.cost_basis * 100) if position.cost_basis > 0 else 0
        
        holdings_data.append({
            "Symbol": symbol,
            "Quantity": f"{position.quantity:,.0f}",
            "Avg Price": f"${position.avg_price:.2f}",
            "Current Price": f"${position.current_price:.2f}",
            "Market Value": f"${position.market_value:,.2f}",
            "Unrealized P&L": f"${position.unrealized_pnl:,.2f}",
            "Return %": f"{pnl_pct:+.2f}%",
            "Weight %": f"{(position.market_value / trading_engine.get_portfolio_summary()['total_value']) * 100:.1f}%"
        })
    
    if holdings_data:
        df = pd.DataFrame(holdings_data)
        st.dataframe(df, use_container_width=True)
        
        # Position actions
        st.markdown("#### Quick Actions")
        selected_symbol = st.selectbox("Select position for actions:", list(positions.keys()))
        
        if selected_symbol:
            col1, col2, col3 = st.columns(3)
            
            with col1:
                if st.button("📈 Add More", key=f"add_{selected_symbol}"):
                    st.session_state['current_page'] = "Trading"
                    st.session_state['selected_symbol'] = selected_symbol
                    st.session_state['suggested_action'] = 'buy'
                    st.rerun()
            
            with col2:
                if st.button("📉 Reduce Position", key=f"reduce_{selected_symbol}"):
                    st.session_state['current_page'] = "Trading"
                    st.session_state['selected_symbol'] = selected_symbol
                    st.session_state['suggested_action'] = 'sell'
                    st.rerun()
            
            with col3:
                if st.button("🔍 Analyze", key=f"analyze_{selected_symbol}"):
                    st.session_state['current_page'] = "Analytics"
                    st.session_state['selected_symbol'] = selected_symbol
                    st.rerun()

def render_allocation_charts(trading_engine):
    """Render portfolio allocation charts"""
    
    st.markdown("### 🥧 Allocation")
    
    positions = trading_engine.get_positions()
    portfolio_summary = trading_engine.get_portfolio_summary()
    
    if not positions:
        st.info("No positions to show allocation")
        return
    
    # Asset allocation pie chart
    allocation_data = []
    for symbol, position in positions.items():
        weight = (position.market_value / portfolio_summary['total_value']) * 100
        allocation_data.append({
            'Symbol': symbol,
            'Value': position.market_value,
            'Weight': weight
        })
    
    # Add cash allocation
    cash_weight = (portfolio_summary['cash'] / portfolio_summary['total_value']) * 100
    allocation_data.append({
        'Symbol': 'Cash',
        'Value': portfolio_summary['cash'],
        'Weight': cash_weight
    })
    
    df_allocation = pd.DataFrame(allocation_data)
    
    # Pie chart
    fig_pie = px.pie(
        df_allocation,
        values='Weight',
        names='Symbol',
        title="Portfolio Allocation (%)"
    )
    
    fig_pie.update_layout(
        template="plotly_dark",
        height=300
    )
    
    st.plotly_chart(fig_pie, use_container_width=True, key="allocation_pie_chart")
    
    # Top holdings
    st.markdown("#### Top Holdings")
    top_holdings = df_allocation.nlargest(5, 'Weight')[['Symbol', 'Weight']]
    for _, row in top_holdings.iterrows():
        st.write(f"**{row['Symbol']}**: {row['Weight']:.1f}%")

def render_performance_analytics(trading_engine):
    """Render detailed performance analytics"""
    
    st.markdown("### 📊 Performance Analytics")
    
    with st.expander("📈 Risk & Return Metrics", expanded=False):
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("#### Risk Metrics")
            
            # Mock risk metrics for demonstration
            risk_metrics = {
                "Portfolio Beta": 1.15,
                "Sharpe Ratio": 1.23,
                "Max Drawdown": -8.5,
                "Volatility (Annual)": 18.2
            }
            
            for metric, value in risk_metrics.items():
                if isinstance(value, float):
                    if "%" in metric or "Drawdown" in metric:
                        st.metric(metric, f"{value:.1f}%")
                    else:
                        st.metric(metric, f"{value:.2f}")
                else:
                    st.metric(metric, str(value))
        
        with col2:
            st.markdown("#### Return Metrics")
            
            portfolio_summary = trading_engine.get_portfolio_summary()
            
            # Calculate return metrics
            return_metrics = {
                "Total Return": portfolio_summary['total_return_pct'],
                "Annualized Return": portfolio_summary['total_return_pct'] * 2,  # Mock calculation
                "1M Return": np.random.uniform(-5, 8),  # Mock
                "3M Return": np.random.uniform(-10, 15)  # Mock
            }
            
            for metric, value in return_metrics.items():
                color = "normal" if value >= 0 else "inverse"
                st.metric(metric, f"{value:+.2f}%", delta_color=color)
    
    with st.expander("🎯 Performance Attribution", expanded=False):
        positions = trading_engine.get_positions()
        
        if positions:
            # Performance contribution by position
            contrib_data = []
            total_pnl = sum(pos.unrealized_pnl for pos in positions.values())
            
            for symbol, position in positions.items():
                contribution = (position.unrealized_pnl / total_pnl * 100) if total_pnl != 0 else 0
                contrib_data.append({
                    'Symbol': symbol,
                    'P&L': position.unrealized_pnl,
                    'Contribution': contribution
                })
            
            df_contrib = pd.DataFrame(contrib_data)
            df_contrib = df_contrib.sort_values('Contribution', ascending=False)
            
            # Bar chart of contributions
            fig_contrib = px.bar(
                df_contrib,
                x='Symbol',
                y='Contribution',
                title="Performance Contribution by Position (%)",
                color='Contribution',
                color_continuous_scale='RdYlGn'
            )
            
            fig_contrib.update_layout(
                template="plotly_dark",
                height=300
            )
            
            st.plotly_chart(fig_contrib, use_container_width=True, key="contribution_chart")

def refresh_portfolio_data():
    """Refresh portfolio data"""
    st.cache_data.clear()
    st.success("Portfolio data refreshed!")
    st.rerun()

def export_portfolio_report():
    """Export portfolio report"""
    st.info("Portfolio report export feature would be implemented here")
    # In a real implementation, this would generate and download a PDF/Excel report