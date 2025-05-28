import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta
import yfinance as yf
import sys
import os

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import our analytics module
try:
    from paper_trading_analytics import (
        PaperTradingPortfolio,
        calculate_portfolio_metrics,
        get_performance_chart,
        get_holdings_pie_chart
    )
except ImportError:
    st.error("⚠️ Could not import paper_trading_analytics module. Please ensure it exists.")
    PaperTradingPortfolio = None

# Page config
st.set_page_config(
    page_title="Paper Trading - WealthIncome",
    page_icon="🧾",
    layout="wide"
)

st.title("🧾 Paper Trading")
st.markdown("Practice trading strategies with virtual money before risking real capital")

# Initialize portfolio in session state
if 'paper_portfolio' not in st.session_state:
    if PaperTradingPortfolio:
        st.session_state.paper_portfolio = PaperTradingPortfolio()
    else:
        # Fallback if analytics module is missing
        st.session_state.paper_portfolio = {
            'cash': 100000.0,
            'positions': {},
            'trades': [],
            'portfolio_history': []
        }

# Sidebar configuration
with st.sidebar:
    st.markdown("### ⚙️ Paper Trading Settings")
    
    starting_capital = st.number_input(
        "Starting Capital ($)",
        min_value=1000,
        max_value=1000000,
        value=100000,
        step=1000
    )
    
    if st.button("🔄 Reset Portfolio", type="secondary", use_container_width=True):
        if PaperTradingPortfolio:
            st.session_state.paper_portfolio = PaperTradingPortfolio(starting_capital)
        else:
            st.session_state.paper_portfolio = {
                'cash': starting_capital,
                'positions': {},
                'trades': [],
                'portfolio_history': []
            }
        st.success("Portfolio reset!")
        st.rerun()
    
    st.markdown("---")
    st.markdown("### 📊 Quick Stats")
    
    # Calculate quick stats
    if hasattr(st.session_state.paper_portfolio, 'get_total_value'):
        total_value = st.session_state.paper_portfolio.get_total_value()
        total_return = st.session_state.paper_portfolio.get_total_return()
        win_rate = st.session_state.paper_portfolio.get_win_rate()
    else:
        # Fallback calculations
        positions_value = sum(
            pos.get('quantity', 0) * pos.get('current_price', 0) 
            for pos in st.session_state.paper_portfolio.get('positions', {}).values()
        )
        total_value = st.session_state.paper_portfolio.get('cash', 0) + positions_value
        total_return = ((total_value - starting_capital) / starting_capital) * 100
        win_rate = 0.0
    
    st.metric("Win Rate", f"{win_rate:.1f}%")
    st.metric("Total Trades", len(st.session_state.paper_portfolio.get('trades', [])))

# Main content area
tab1, tab2, tab3, tab4 = st.tabs(["📈 Trade", "💼 Portfolio", "📊 Analytics", "📜 History"])

# Tab 1: Trading Interface
with tab1:
    col1, col2 = st.columns([3, 2])
    
    with col1:
        st.subheader("🎯 Place a Trade")
        
        # Stock selection and price display
        symbol = st.text_input("Stock Symbol", value="AAPL").upper()
        
        # Fetch real-time price
        current_price = 0.0
        if symbol:
            try:
                ticker = yf.Ticker(symbol)
                data = ticker.history(period="1d", interval="1m")
                if not data.empty:
                    current_price = data['Close'].iloc[-1]
                    price_change = current_price - data['Open'].iloc[0]
                    price_change_pct = (price_change / data['Open'].iloc[0]) * 100
                    
                    col_p1, col_p2, col_p3 = st.columns(3)
                    with col_p1:
                        st.metric("Current Price", f"${current_price:.2f}")
                    with col_p2:
                        st.metric("Change", f"${price_change:.2f}", f"{price_change_pct:+.2f}%")
                    with col_p3:
                        st.metric("Volume", f"{int(data['Volume'].sum()):,}")
                else:
                    st.warning(f"Could not fetch price for {symbol}")
            except Exception as e:
                st.error(f"Error fetching price: {str(e)}")
                current_price = st.number_input("Enter price manually", min_value=0.01, value=100.0)
        
        # Trading form
        with st.form("trade_form"):
            col_t1, col_t2, col_t3 = st.columns(3)
            
            with col_t1:
                action = st.selectbox("Action", ["BUY", "SELL"])
            with col_t2:
                quantity = st.number_input("Shares", min_value=1, value=10)
            with col_t3:
                if current_price > 0:
                    st.metric("Total Value", f"${quantity * current_price:,.2f}")
            
            # Order type
            order_type = st.radio("Order Type", ["Market", "Limit"], horizontal=True)
            
            if order_type == "Limit":
                limit_price = st.number_input("Limit Price", min_value=0.01, value=current_price)
            else:
                limit_price = current_price
            
            # Stop loss and take profit
            col_sl, col_tp = st.columns(2)
            with col_sl:
                use_stop_loss = st.checkbox("Set Stop Loss")
                if use_stop_loss:
                    stop_loss = st.number_input("Stop Loss Price", min_value=0.01, value=current_price * 0.95)
            with col_tp:
                use_take_profit = st.checkbox("Set Take Profit")
                if use_take_profit:
                    take_profit = st.number_input("Take Profit Price", min_value=0.01, value=current_price * 1.05)
            
            submit_trade = st.form_submit_button("Execute Trade", type="primary", use_container_width=True)
            
            if submit_trade and current_price > 0:
                # Execute trade
                if hasattr(st.session_state.paper_portfolio, 'execute_trade'):
                    success, message = st.session_state.paper_portfolio.execute_trade(
                        symbol=symbol,
                        action=action,
                        quantity=quantity,
                        price=limit_price,
                        stop_loss=stop_loss if use_stop_loss else None,
                        take_profit=take_profit if use_take_profit else None
                    )
                    if success:
                        st.success(message)
                        st.balloons()
                    else:
                        st.error(message)
                else:
                    # Fallback execution
                    total_cost = quantity * limit_price
                    if action == "BUY":
                        if total_cost <= st.session_state.paper_portfolio['cash']:
                            st.session_state.paper_portfolio['cash'] -= total_cost
                            st.success(f"Bought {quantity} shares of {symbol} at ${limit_price:.2f}")
                        else:
                            st.error("Insufficient funds!")
                    else:
                        st.error("Sell functionality requires analytics module")
                
                st.rerun()
    
    with col2:
        st.subheader("📊 Quick Chart")
        if symbol and current_price > 0:
            try:
                # Fetch data for mini chart
                ticker = yf.Ticker(symbol)
                hist_data = ticker.history(period="5d", interval="30m")
                
                if not hist_data.empty:
                    fig = go.Figure()
                    fig.add_trace(go.Scatter(
                        x=hist_data.index,
                        y=hist_data['Close'],
                        mode='lines',
                        name=symbol,
                        line=dict(color='#1f77b4', width=2)
                    ))
                    fig.update_layout(
                        title=f"{symbol} - 5 Day",
                        height=300,
                        showlegend=False,
                        margin=dict(l=0, r=0, t=30, b=0)
                    )
                    st.plotly_chart(fig, use_container_width=True)
            except:
                st.info("Chart unavailable")

# Tab 2: Portfolio Overview
with tab2:
    st.subheader("💼 Current Portfolio")
    
    # Portfolio metrics
    col1, col2, col3, col4 = st.columns(4)
    
    if hasattr(st.session_state.paper_portfolio, 'cash'):
        cash = st.session_state.paper_portfolio.cash
        positions_value = st.session_state.paper_portfolio.get_positions_value()
        total_value = st.session_state.paper_portfolio.get_total_value()
        total_return = st.session_state.paper_portfolio.get_total_return()
    else:
        cash = st.session_state.paper_portfolio.get('cash', 0)
        positions_value = sum(
            pos.get('quantity', 0) * pos.get('current_price', 0) 
            for pos in st.session_state.paper_portfolio.get('positions', {}).values()
        )
        total_value = cash + positions_value
        total_return = ((total_value - starting_capital) / starting_capital) * 100
    
    with col1:
        st.metric("💵 Cash", f"${cash:,.2f}")
    with col2:
        st.metric("📊 Positions Value", f"${positions_value:,.2f}")
    with col3:
        st.metric("💰 Total Value", f"${total_value:,.2f}")
    with col4:
        st.metric("📈 Total Return", f"{total_return:+.2f}%", 
                 f"${total_value - starting_capital:+,.2f}")
    
    # Positions table
    if hasattr(st.session_state.paper_portfolio, 'positions') and st.session_state.paper_portfolio.positions:
        st.markdown("### 📊 Open Positions")
        
        positions_data = []
        for symbol, pos in st.session_state.paper_portfolio.positions.items():
            # Update current price
            try:
                ticker = yf.Ticker(symbol)
                current = ticker.history(period="1d")['Close'].iloc[-1]
                pos['current_price'] = current
            except:
                current = pos.get('current_price', pos['avg_price'])
            
            market_value = pos['quantity'] * current
            profit_loss = (current - pos['avg_price']) * pos['quantity']
            profit_loss_pct = ((current - pos['avg_price']) / pos['avg_price']) * 100
            
            positions_data.append({
                'Symbol': symbol,
                'Shares': pos['quantity'],
                'Avg Cost': f"${pos['avg_price']:.2f}",
                'Current': f"${current:.2f}",
                'Market Value': f"${market_value:,.2f}",
                'P&L': f"${profit_loss:+,.2f}",
                'P&L %': f"{profit_loss_pct:+.2f}%",
                'Stop Loss': f"${pos.get('stop_loss', 0):.2f}" if pos.get('stop_loss') else '-',
                'Take Profit': f"${pos.get('take_profit', 0):.2f}" if pos.get('take_profit') else '-'
            })
        
        df_positions = pd.DataFrame(positions_data)
        st.dataframe(
            df_positions,
            use_container_width=True,
            hide_index=True,
            column_config={
                "P&L": st.column_config.TextColumn("P&L", help="Profit/Loss"),
                "P&L %": st.column_config.TextColumn("P&L %", help="Profit/Loss Percentage")
            }
        )
        
        # Portfolio composition pie chart
        if get_holdings_pie_chart:
            fig_pie = get_holdings_pie_chart(st.session_state.paper_portfolio)
            if fig_pie:
                st.plotly_chart(fig_pie, use_container_width=True)
    else:
        st.info("No open positions. Start trading to build your portfolio!")

# Tab 3: Analytics
with tab3:
    st.subheader("📊 Performance Analytics")
    
    if hasattr(st.session_state.paper_portfolio, 'trades') and st.session_state.paper_portfolio.trades:
        # Performance metrics
        if calculate_portfolio_metrics:
            metrics = calculate_portfolio_metrics(st.session_state.paper_portfolio)
            
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Sharpe Ratio", f"{metrics.get('sharpe_ratio', 0):.2f}")
                st.metric("Win Rate", f"{metrics.get('win_rate', 0):.1f}%")
            with col2:
                st.metric("Profit Factor", f"{metrics.get('profit_factor', 0):.2f}")
                st.metric("Avg Win", f"${metrics.get('avg_win', 0):.2f}")
            with col3:
                st.metric("Max Drawdown", f"{metrics.get('max_drawdown', 0):.1f}%")
                st.metric("Avg Loss", f"${metrics.get('avg_loss', 0):.2f}")
            with col4:
                st.metric("Total Trades", metrics.get('total_trades', 0))
                st.metric("Best Trade", f"${metrics.get('best_trade', 0):.2f}")
        
        # Performance chart
        if get_performance_chart:
            fig_performance = get_performance_chart(st.session_state.paper_portfolio)
            if fig_performance:
                st.plotly_chart(fig_performance, use_container_width=True)
        
        # Trade distribution
        st.markdown("### 📊 Trade Analysis")
        
        # P&L distribution histogram
        if hasattr(st.session_state.paper_portfolio, 'get_trade_pnls'):
            pnls = st.session_state.paper_portfolio.get_trade_pnls()
            if pnls:
                fig_hist = px.histogram(
                    x=pnls,
                    nbins=20,
                    title="Profit/Loss Distribution",
                    labels={'x': 'Profit/Loss ($)', 'y': 'Number of Trades'}
                )
                fig_hist.add_vline(x=0, line_dash="dash", line_color="red")
                st.plotly_chart(fig_hist, use_container_width=True)
    else:
        st.info("No trades yet. Start trading to see analytics!")

# Tab 4: Trade History
with tab4:
    st.subheader("📜 Trade History")
    
    if hasattr(st.session_state.paper_portfolio, 'trades') and st.session_state.paper_portfolio.trades:
        # Create DataFrame from trades
        trades_data = []
        for trade in reversed(st.session_state.paper_portfolio.trades[-50:]):  # Last 50 trades
            trades_data.append({
                'Time': datetime.fromisoformat(trade['timestamp']).strftime('%Y-%m-%d %H:%M'),
                'Symbol': trade['symbol'],
                'Action': trade['action'],
                'Shares': trade['quantity'],
                'Price': f"${trade['price']:.2f}",
                'Total': f"${trade['total']:.2f}",
                'P&L': f"${trade.get('pnl', 0):+.2f}" if trade.get('pnl') else '-',
                'Status': trade.get('status', 'Executed')
            })
        
        df_trades = pd.DataFrame(trades_data)
        
        # Add filters
        col1, col2, col3 = st.columns(3)
        with col1:
            filter_symbol = st.selectbox(
                "Filter by Symbol",
                ["All"] + list(set(trade['symbol'] for trade in st.session_state.paper_portfolio.trades))
            )
        with col2:
            filter_action = st.selectbox("Filter by Action", ["All", "BUY", "SELL"])
        with col3:
            filter_status = st.selectbox("Filter by Status", ["All", "Executed", "Pending", "Cancelled"])
        
        # Apply filters
        if filter_symbol != "All":
            df_trades = df_trades[df_trades['Symbol'] == filter_symbol]
        if filter_action != "All":
            df_trades = df_trades[df_trades['Action'] == filter_action]
        if filter_status != "All":
            df_trades = df_trades[df_trades['Status'] == filter_status]
        
        st.dataframe(df_trades, use_container_width=True, hide_index=True)
        
        # Export functionality
        if st.button("📥 Export Trade History"):
            csv = df_trades.to_csv(index=False)
            st.download_button(
                label="Download CSV",
                data=csv,
                file_name=f"paper_trades_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv"
            )
    else:
        st.info("No trades yet. Start trading to build your history!")

# Footer
st.markdown("---")
st.markdown(
    """
    <div style='text-align: center; color: gray;'>
        Paper Trading Mode | Not Real Money | Practice Safely
    </div>
    """,
    unsafe_allow_html=True
)
