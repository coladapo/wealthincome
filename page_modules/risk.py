"""
Risk Management Page - Portfolio risk analysis and management tools
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta
from typing import Dict, List, Any
import numpy as np

from ui.components import render_metric_card, render_alert_banner
from ui.navigation import render_page_header

def render_risk_management():
    """Render risk management page"""
    
    render_page_header(
        "⚠️ Risk Management",
        "Monitor and manage portfolio risk exposure",
        actions=[
            {"label": "🔄 Refresh", "key": "refresh_risk", "callback": refresh_risk_data},
            {"label": "📊 Risk Report", "key": "generate_risk_report", "callback": generate_risk_report}
        ]
    )
    
    trading_engine = st.session_state.get('trading_engine')
    data_manager = st.session_state.get('data_manager')
    
    if not trading_engine:
        st.error("Trading engine not initialized")
        return
    
    # Risk Overview
    render_risk_overview(trading_engine)
    
    st.markdown("---")
    
    # Risk Analysis
    col1, col2 = st.columns([2, 1])
    
    with col1:
        render_risk_metrics(trading_engine)
    
    with col2:
        render_risk_alerts(trading_engine)
    
    st.markdown("---")
    
    # Detailed Risk Analysis
    render_detailed_risk_analysis(trading_engine, data_manager)

def render_risk_overview(trading_engine):
    """Render risk overview section"""
    
    st.markdown("### ⚡ Risk Overview")
    
    portfolio_summary = trading_engine.get_portfolio_summary()
    
    # Calculate mock risk metrics for demonstration
    portfolio_volatility = np.random.uniform(15, 25)
    portfolio_beta = np.random.uniform(0.8, 1.3)
    max_drawdown = np.random.uniform(-15, -5)
    var_95 = np.random.uniform(-5, -2)
    sharpe_ratio = np.random.uniform(0.5, 2.0)
    
    col1, col2, col3, col4, col5 = st.columns(5)
    
    with col1:
        render_metric_card(
            "Volatility",
            f"{portfolio_volatility:.1f}%",
            delta="Annualized",
            icon="📊"
        )
    
    with col2:
        render_metric_card(
            "Beta",
            f"{portfolio_beta:.2f}",
            delta="vs S&P 500",
            icon="📈"
        )
    
    with col3:
        render_metric_card(
            "Max Drawdown",
            f"{max_drawdown:.1f}%",
            delta="1 Year",
            icon="📉"
        )
    
    with col4:
        render_metric_card(
            "VaR (95%)",
            f"{var_95:.1f}%",
            delta="1-day",
            icon="⚠️"
        )
    
    with col5:
        render_metric_card(
            "Sharpe Ratio",
            f"{sharpe_ratio:.2f}",
            delta="Risk-adjusted",
            icon="⚖️"
        )

def render_risk_metrics(trading_engine):
    """Render detailed risk metrics and analysis"""
    
    st.markdown("### 📊 Risk Analysis")
    
    # Risk score gauge
    portfolio_risk_score = np.random.uniform(20, 80)
    
    fig_gauge = go.Figure(go.Indicator(
        mode="gauge+number",
        value=portfolio_risk_score,
        domain={'x': [0, 1], 'y': [0, 1]},
        title={'text': "Portfolio Risk Score"},
        gauge={
            'axis': {'range': [None, 100]},
            'bar': {'color': "darkblue"},
            'steps': [
                {'range': [0, 30], 'color': "lightgreen"},
                {'range': [30, 60], 'color': "yellow"},
                {'range': [60, 80], 'color': "orange"},
                {'range': [80, 100], 'color': "red"}
            ],
            'threshold': {
                'line': {'color': "red", 'width': 4},
                'thickness': 0.75,
                'value': 70
            }
        }
    ))
    
    fig_gauge.update_layout(
        template="plotly_dark",
        height=300
    )
    
    st.plotly_chart(fig_gauge, use_container_width=True, key="risk_score_gauge")
    
    # Risk breakdown by position
    positions = trading_engine.get_positions()
    
    if positions:
        st.markdown("#### Position Risk Analysis")
        
        risk_data = []
        total_value = trading_engine.get_portfolio_summary()['total_value']
        
        for symbol, position in positions.items():
            weight = (position.market_value / total_value) * 100
            # Mock risk metrics
            position_volatility = np.random.uniform(15, 40)
            position_beta = np.random.uniform(0.5, 2.0)
            risk_contribution = weight * position_volatility / 100
            
            risk_data.append({
                'Symbol': symbol,
                'Weight': weight,
                'Volatility': position_volatility,
                'Beta': position_beta,
                'Risk Contribution': risk_contribution,
                'Market Value': position.market_value
            })
        
        df_risk = pd.DataFrame(risk_data)
        
        # Risk vs Weight scatter plot
        fig_scatter = px.scatter(
            df_risk,
            x='Weight',
            y='Risk Contribution',
            size='Market Value',
            text='Symbol',
            title="Risk Contribution vs Portfolio Weight",
            labels={
                'Weight': 'Portfolio Weight (%)',
                'Risk Contribution': 'Risk Contribution (%)'
            }
        )
        
        fig_scatter.update_traces(textposition="top center")
        fig_scatter.update_layout(
            template="plotly_dark",
            height=400
        )
        
        st.plotly_chart(fig_scatter, use_container_width=True, key="risk_weight_scatter")

def render_risk_alerts(trading_engine):
    """Render risk alerts and warnings"""
    
    st.markdown("### 🚨 Risk Alerts")
    
    # Generate mock alerts
    alerts = []
    
    # Check portfolio concentration
    positions = trading_engine.get_positions()
    if positions:
        total_value = trading_engine.get_portfolio_summary()['total_value']
        max_position_weight = 0
        max_position_symbol = ""
        
        for symbol, position in positions.items():
            weight = (position.market_value / total_value) * 100
            if weight > max_position_weight:
                max_position_weight = weight
                max_position_symbol = symbol
        
        if max_position_weight > 20:
            alerts.append({
                'type': 'warning',
                'title': 'High Concentration Risk',
                'message': f'{max_position_symbol} represents {max_position_weight:.1f}% of portfolio'
            })
    
    # Mock additional alerts
    if np.random.random() > 0.5:
        alerts.append({
            'type': 'info',
            'title': 'Market Volatility',
            'message': 'Market volatility has increased by 15% this week'
        })
    
    if np.random.random() > 0.7:
        alerts.append({
            'type': 'error',
            'title': 'Stop Loss Triggered',
            'message': 'Position in TECH has hit stop loss level'
        })
    
    if alerts:
        for alert in alerts:
            if alert['type'] == 'warning':
                st.warning(f"**{alert['title']}**: {alert['message']}")
            elif alert['type'] == 'error':
                st.error(f"**{alert['title']}**: {alert['message']}")
            else:
                st.info(f"**{alert['title']}**: {alert['message']}")
    else:
        st.success("✅ No risk alerts at this time")
    
    # Risk limits
    st.markdown("#### Risk Limits")
    
    with st.expander("📋 Current Limits", expanded=True):
        limits = {
            "Max Position Size": "20%",
            "Sector Concentration": "30%", 
            "Daily VaR Limit": "3%",
            "Portfolio Beta Range": "0.8 - 1.5"
        }
        
        for limit_name, limit_value in limits.items():
            col1, col2 = st.columns([2, 1])
            with col1:
                st.write(limit_name)
            with col2:
                st.write(limit_value)

def render_detailed_risk_analysis(trading_engine, data_manager):
    """Render detailed risk analysis sections"""
    
    # Correlation Analysis
    with st.expander("🔗 Correlation Analysis", expanded=False):
        render_correlation_analysis(trading_engine)
    
    # Stress Testing
    with st.expander("🧪 Stress Testing", expanded=False):
        render_stress_testing(trading_engine)
    
    # Historical Risk
    with st.expander("📈 Historical Risk Metrics", expanded=False):
        render_historical_risk(trading_engine)

def render_correlation_analysis(trading_engine):
    """Render portfolio correlation analysis"""
    
    st.markdown("#### Portfolio Correlation Matrix")
    
    positions = trading_engine.get_positions()
    
    if len(positions) < 2:
        st.info("Need at least 2 positions for correlation analysis")
        return
    
    # Generate mock correlation data
    symbols = list(positions.keys())[:10]  # Limit to 10 symbols for display
    n_symbols = len(symbols)
    
    # Create correlation matrix
    correlation_matrix = np.random.uniform(-0.5, 0.8, (n_symbols, n_symbols))
    # Make matrix symmetric
    correlation_matrix = (correlation_matrix + correlation_matrix.T) / 2
    # Set diagonal to 1
    np.fill_diagonal(correlation_matrix, 1.0)
    
    fig_corr = go.Figure(data=go.Heatmap(
        z=correlation_matrix,
        x=symbols,
        y=symbols,
        colorscale='RdYlBu',
        zmid=0,
        text=correlation_matrix,
        texttemplate="%{text:.2f}",
        textfont={"size": 10}
    ))
    
    fig_corr.update_layout(
        title="Asset Correlation Matrix",
        template="plotly_dark",
        height=400
    )
    
    st.plotly_chart(fig_corr, use_container_width=True, key="correlation_matrix")
    
    # Correlation insights
    st.markdown("#### Correlation Insights")
    
    # Find highest correlation
    mask = np.triu(np.ones_like(correlation_matrix), k=1).astype(bool)
    max_corr_idx = np.unravel_index(np.argmax(correlation_matrix[mask]), correlation_matrix.shape)
    max_corr = correlation_matrix[max_corr_idx]
    
    st.write(f"**Highest Correlation**: {symbols[max_corr_idx[0]]} - {symbols[max_corr_idx[1]]} ({max_corr:.2f})")
    
    if max_corr > 0.7:
        st.warning("High correlation detected - consider diversification")

def render_stress_testing(trading_engine):
    """Render stress testing scenarios"""
    
    st.markdown("#### Stress Test Scenarios")
    
    portfolio_value = trading_engine.get_portfolio_summary()['total_value']
    
    scenarios = [
        {"name": "Market Crash (-20%)", "impact": -0.20},
        {"name": "Tech Sector Decline (-30%)", "impact": -0.15},
        {"name": "Interest Rate Spike", "impact": -0.10},
        {"name": "Currency Crisis", "impact": -0.08},
        {"name": "Inflation Surge", "impact": -0.12}
    ]
    
    stress_data = []
    for scenario in scenarios:
        impact_value = portfolio_value * scenario["impact"]
        new_value = portfolio_value + impact_value
        
        stress_data.append({
            'Scenario': scenario["name"],
            'Impact (%)': f"{scenario['impact']*100:+.1f}%",
            'Impact ($)': f"${impact_value:,.0f}",
            'New Value': f"${new_value:,.0f}"
        })
    
    df_stress = pd.DataFrame(stress_data)
    st.dataframe(df_stress, use_container_width=True)
    
    # Stress test chart
    fig_stress = go.Figure(data=[
        go.Bar(
            x=[s["name"] for s in scenarios],
            y=[s["impact"] * 100 for s in scenarios],
            marker_color=['red' if x < 0 else 'green' for x in [s["impact"] for s in scenarios]]
        )
    ])
    
    fig_stress.update_layout(
        title="Stress Test Impact (%)",
        xaxis_title="Scenario",
        yaxis_title="Portfolio Impact (%)",
        template="plotly_dark",
        height=300
    )
    
    st.plotly_chart(fig_stress, use_container_width=True, key="stress_test_chart")

def render_historical_risk(trading_engine):
    """Render historical risk metrics"""
    
    st.markdown("#### Historical Risk Trends")
    
    # Generate mock historical data
    dates = pd.date_range(start='2024-01-01', end=datetime.now(), freq='W')
    
    # Mock risk metrics over time
    volatility_history = 20 + np.random.normal(0, 3, len(dates))
    var_history = -3 + np.random.normal(0, 0.5, len(dates))
    beta_history = 1.0 + np.random.normal(0, 0.1, len(dates))
    
    fig_history = go.Figure()
    
    fig_history.add_trace(go.Scatter(
        x=dates,
        y=volatility_history,
        mode='lines',
        name='Volatility (%)',
        line=dict(color='blue')
    ))
    
    fig_history.add_trace(go.Scatter(
        x=dates,
        y=np.abs(var_history) * 10,  # Scale for visibility
        mode='lines',
        name='VaR (scaled)',
        line=dict(color='red'),
        yaxis='y2'
    ))
    
    fig_history.update_layout(
        title="Historical Risk Metrics",
        xaxis_title="Date",
        yaxis_title="Volatility (%)",
        yaxis2=dict(
            title="VaR (scaled)",
            overlaying='y',
            side='right'
        ),
        template="plotly_dark",
        height=400
    )
    
    st.plotly_chart(fig_history, use_container_width=True, key="historical_risk_chart")
    
    # Risk statistics
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.metric(
            "Avg Volatility",
            f"{np.mean(volatility_history):.1f}%",
            delta=f"{volatility_history[-1] - np.mean(volatility_history):+.1f}%"
        )
    
    with col2:
        st.metric(
            "Avg VaR",
            f"{np.mean(var_history):.1f}%",
            delta=f"{var_history[-1] - np.mean(var_history):+.1f}%"
        )
    
    with col3:
        st.metric(
            "Avg Beta",
            f"{np.mean(beta_history):.2f}",
            delta=f"{beta_history[-1] - np.mean(beta_history):+.2f}"
        )

def refresh_risk_data():
    """Refresh risk analysis data"""
    st.cache_data.clear()
    st.success("Risk data refreshed!")
    st.rerun()

def generate_risk_report():
    """Generate comprehensive risk report"""
    st.info("Risk report generation feature would be implemented here")
    # In a real implementation, this would generate a detailed PDF risk report