# confidence_scoring.py
"""
Confidence Scoring System for wealthincome
Developed by: AI Sage, Data Oracle, Code Einstein
Purpose: Aggregate multiple signals into a single confidence score with explanations
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple
import streamlit as st

class ConfidenceScorer:
    """Calculate comprehensive confidence scores for trading decisions"""
    
    def __init__(self):
        # Weight configurations for different factors
        self.weights = {
            'technical': {
                'trend_alignment': 0.20,
                'momentum': 0.15,
                'volume_confirmation': 0.15,
                'support_resistance': 0.10,
                'pattern_quality': 0.10
            },
            'fundamental': {
                'earnings_momentum': 0.10,
                'relative_strength': 0.10,
                'sector_performance': 0.10
            },
            'sentiment': {
                'news_sentiment': 0.10,
                'social_momentum': 0.05,
                'insider_activity': 0.05
            },
            'trend_alignment': {
                'sector_trend': 0.15,
                'market_regime': 0.10,
                'theme_alignment': 0.15
            }
        }
        
        # Confidence thresholds
        self.thresholds = {
            'ultra_high': 85,
            'high': 70,
            'medium': 50,
            'low': 30
        }
        
        # Future trend themes (Quantum Mind's contribution)
        self.future_trends = {
            'quantum_computing': ['IONQ', 'RGTI', 'QUBT', 'IBM', 'GOOGL', 'MSFT'],
            'ai_revolution': ['NVDA', 'AMD', 'MSFT', 'GOOGL', 'META', 'PLTR', 'AI', 'UPST'],
            'clean_energy': ['ENPH', 'SEDG', 'RUN', 'PLUG', 'BLDP', 'FCEL', 'BE'],
            'space_economy': ['RKLB', 'ASTR', 'SPCE', 'LMT', 'NOC', 'BA'],
            'biotech_breakthrough': ['MRNA', 'BNTX', 'CRSP', 'EDIT', 'NTLA', 'BEAM'],
            'metaverse': ['META', 'RBLX', 'U', 'NVDA', 'MSFT', 'AAPL'],
            'cybersecurity': ['CRWD', 'ZS', 'NET', 'S', 'PANW', 'FTNT'],
            'fintech': ['SQ', 'PYPL', 'SOFI', 'AFRM', 'UPST', 'COIN']
        }
    
    def calculate_confidence_score(self, ticker: str, analysis: Dict) -> Dict:
        """
        Calculate comprehensive confidence score with detailed breakdown
        Returns: Dict with score, components, explanations, and recommendations
        """
        
        confidence_components = {}
        explanations = []
        recommendations = []
        
        # 1. Technical Confidence (40% of total)
        tech_score = self._calculate_technical_confidence(analysis)
        confidence_components['technical'] = tech_score
        
        # 2. Fundamental Confidence (20% of total)
        fundamental_score = self._calculate_fundamental_confidence(analysis)
        confidence_components['fundamental'] = fundamental_score
        
        # 3. Sentiment Confidence (20% of total)
        sentiment_score = self._calculate_sentiment_confidence(analysis)
        confidence_components['sentiment'] = sentiment_score
        
        # 4. Trend Alignment (20% of total)
        trend_score = self._calculate_trend_alignment(ticker, analysis)
        confidence_components['trend'] = trend_score
        
        # Calculate weighted total
        total_score = (
            tech_score['score'] * 0.40 +
            fundamental_score['score'] * 0.20 +
            sentiment_score['score'] * 0.20 +
            trend_score['score'] * 0.20
        )
        
        # Generate explanations based on scores
        if total_score >= self.thresholds['ultra_high']:
            explanations.append("🔥 ULTRA HIGH CONFIDENCE: Multiple strong signals aligning perfectly")
            recommendations.append("Consider larger position size (still within risk limits)")
        elif total_score >= self.thresholds['high']:
            explanations.append("✅ HIGH CONFIDENCE: Strong technical and fundamental alignment")
            recommendations.append("Standard position size recommended")
        elif total_score >= self.thresholds['medium']:
            explanations.append("⚡ MEDIUM CONFIDENCE: Mixed signals, but opportunity present")
            recommendations.append("Consider smaller position or wait for confirmation")
        else:
            explanations.append("⚠️ LOW CONFIDENCE: Weak signals, high risk")
            recommendations.append("Avoid trade or wait for better setup")
        
        # Add component-specific insights
        if tech_score['score'] > 80:
            explanations.append(f"📊 Exceptional technical setup: {tech_score['reason']}")
        if sentiment_score['score'] > 80:
            explanations.append(f"📰 Very positive sentiment: {sentiment_score['reason']}")
        if trend_score['score'] > 80:
            explanations.append(f"🚀 Strong trend alignment: {trend_score['reason']}")
        
        # Risk warnings
        if analysis.get('volatility', 0) > 0.5:
            recommendations.append("⚠️ High volatility - use wider stops")
        if analysis.get('volume', 0) < 1000000:
            recommendations.append("⚠️ Low liquidity - use limit orders")
        
        return {
            'total_score': round(total_score, 1),
            'confidence_level': self._get_confidence_level(total_score),
            'components': confidence_components,
            'explanations': explanations,
            'recommendations': recommendations,
            'timestamp': pd.Timestamp.now()
        }
    
    def _calculate_technical_confidence(self, analysis: Dict) -> Dict:
        """Calculate technical analysis confidence"""
        score = 0
        factors = []
        
        tech = analysis.get('technicals', {})
        scores = analysis.get('scores', {})
        
        # Trend alignment (20%)
        if tech.get('sma_20', 0) > tech.get('sma_50', 0) > tech.get('sma_200', 0):
            score += 20
            factors.append("Perfect uptrend alignment")
        elif tech.get('sma_20', 0) > tech.get('sma_50', 0):
            score += 10
            factors.append("Short-term uptrend")
        
        # Momentum (15%)
        rsi = tech.get('rsi', 50)
        if 40 < rsi < 70:
            score += 15
            factors.append("Healthy RSI momentum")
        elif 30 < rsi < 40:
            score += 10
            factors.append("Potential oversold bounce")
        
        # Volume confirmation (15%)
        if analysis.get('rvol', 0) > 1.5:
            score += 15
            factors.append("Strong volume confirmation")
        elif analysis.get('rvol', 0) > 1.0:
            score += 8
            factors.append("Above average volume")
        
        # Support/Resistance (10%)
        price = analysis.get('price', 0)
        support = tech.get('support', 0)
        resistance = tech.get('resistance', 0)
        
        if support > 0 and price > support * 1.02:
            score += 10
            factors.append("Above key support")
        
        # Pattern quality (10%)
        if scores.get('swing_score', 0) > 70:
            score += 10
            factors.append("High-quality pattern detected")
        
        # AI Score bonus
        if scores.get('ai_score', 0) > 80:
            score += 20
            factors.append("AI system highly confident")
        
        return {
            'score': min(score, 100),
            'reason': ', '.join(factors) if factors else 'Limited technical confirmation'
        }
    
    def _calculate_fundamental_confidence(self, analysis: Dict) -> Dict:
        """Calculate fundamental analysis confidence"""
        score = 50  # Base score
        factors = []
        
        info = analysis.get('info', {})
        
        # P/E ratio analysis
        pe = info.get('forwardPE', info.get('trailingPE', 0))
        if 0 < pe < 25:
            score += 10
            factors.append("Reasonable valuation")
        elif 25 <= pe < 40:
            score += 5
            factors.append("Growth premium valuation")
        
        # Revenue growth
        revenue_growth = info.get('revenueGrowth', 0)
        if revenue_growth > 0.20:
            score += 15
            factors.append("Strong revenue growth")
        elif revenue_growth > 0.10:
            score += 8
            factors.append("Solid revenue growth")
        
        # Profit margins
        profit_margin = info.get('profitMargins', 0)
        if profit_margin > 0.20:
            score += 15
            factors.append("Excellent profit margins")
        elif profit_margin > 0.10:
            score += 8
            factors.append("Good profit margins")
        
        # Analyst sentiment
        target_price = info.get('targetMeanPrice', 0)
        current_price = analysis.get('price', 0)
        if target_price > current_price * 1.20:
            score += 10
            factors.append(f"Analysts see {((target_price/current_price - 1) * 100):.0f}% upside")
        
        return {
            'score': min(score, 100),
            'reason': ', '.join(factors) if factors else 'Limited fundamental data'
        }
    
    def _calculate_sentiment_confidence(self, analysis: Dict) -> Dict:
        """Calculate sentiment confidence"""
        score = 50  # Base score
        factors = []
        
        # News sentiment
        news = analysis.get('news_sentiment', {})
        if news.get('label') == 'Positive':
            score += 30
            factors.append("Positive news sentiment")
        elif news.get('label') == 'Negative':
            score -= 20
            factors.append("Negative news sentiment")
        
        # Add sentiment score if available
        sentiment_score = news.get('score', 0)
        if sentiment_score > 0.8:
            score += 20
            factors.append("Very strong positive sentiment")
        elif sentiment_score > 0.6:
            score += 10
            factors.append("Moderately positive sentiment")
        
        return {
            'score': max(0, min(score, 100)),
            'reason': ', '.join(factors) if factors else 'Neutral sentiment'
        }
    
    def _calculate_trend_alignment(self, ticker: str, analysis: Dict) -> Dict:
        """Calculate alignment with future trends"""
        score = 0
        factors = []
        
        # Check which trends this ticker aligns with
        aligned_trends = []
        for trend, tickers in self.future_trends.items():
            if ticker.upper() in tickers:
                aligned_trends.append(trend)
        
        if aligned_trends:
            score += 40
            factors.append(f"Aligned with: {', '.join([t.replace('_', ' ').title() for t in aligned_trends])}")
            
            # Bonus for multiple trend alignment
            if len(aligned_trends) > 1:
                score += 20
                factors.append("Multiple trend convergence")
        
        # Sector momentum
        sector_performance = analysis.get('sector_performance', 0)
        if sector_performance > 0:
            score += 20
            factors.append("Strong sector momentum")
        
        # Market regime alignment
        market_trend = analysis.get('market_trend', 'neutral')
        if market_trend == 'bullish' and analysis.get('beta', 1) > 1.2:
            score += 20
            factors.append("High-beta in bull market")
        elif market_trend == 'bearish' and analysis.get('beta', 1) < 0.8:
            score += 20
            factors.append("Defensive in bear market")
        
        return {
            'score': min(score, 100),
            'reason': ', '.join(factors) if factors else 'No clear trend alignment'
        }
    
    def _get_confidence_level(self, score: float) -> str:
        """Convert numeric score to confidence level"""
        if score >= self.thresholds['ultra_high']:
            return "ULTRA HIGH"
        elif score >= self.thresholds['high']:
            return "HIGH"
        elif score >= self.thresholds['medium']:
            return "MEDIUM"
        else:
            return "LOW"
    
    def get_position_sizing_recommendation(self, confidence_score: float, 
                                           account_size: float, 
                                           risk_per_trade: float = 0.02) -> Dict:
        """
        Calculate position sizing based on confidence score
        Uses Kelly Criterion modified for practical trading
        """
        
        # Base risk allocation
        base_risk = account_size * risk_per_trade
        
        # Adjust based on confidence
        if confidence_score >= self.thresholds['ultra_high']:
            multiplier = 1.5  # Can risk up to 3% on ultra-high confidence
            sizing_note = "Increased size due to exceptional setup"
        elif confidence_score >= self.thresholds['high']:
            multiplier = 1.0  # Standard 2% risk
            sizing_note = "Standard position size"
        elif confidence_score >= self.thresholds['medium']:
            multiplier = 0.5  # Reduced to 1% risk
            sizing_note = "Reduced size due to mixed signals"
        else:
            multiplier = 0.25  # Minimal 0.5% risk
            sizing_note = "Minimal size or consider passing"
        
        adjusted_risk = base_risk * multiplier
        
        return {
            'base_risk': base_risk,
            'adjusted_risk': adjusted_risk,
            'multiplier': multiplier,
            'max_position_percent': (adjusted_risk / account_size) * 100,
            'sizing_note': sizing_note,
            'confidence_level': self._get_confidence_level(confidence_score)
        }


def create_confidence_visualization(confidence_data: Dict) -> None:
    """Create visual representation of confidence score"""
    
    # Main confidence meter
    score = confidence_data['total_score']
    level = confidence_data['confidence_level']
    
    # Color coding based on level
    if level == "ULTRA HIGH":
        color = "#00ff00"
        emoji = "🔥"
    elif level == "HIGH":
        color = "#90EE90"
        emoji = "✅"
    elif level == "MEDIUM":
        color = "#FFD700"
        emoji = "⚡"
    else:
        color = "#FF6B6B"
        emoji = "⚠️"
    
    # Display main score
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown(f"""
        <div style="text-align: center; padding: 20px; background-color: #1e1e1e; border-radius: 10px;">
            <h1 style="color: {color}; margin: 0;">{emoji} {score:.1f}</h1>
            <h3 style="color: {color}; margin: 5px 0;">{level} CONFIDENCE</h3>
        </div>
        """, unsafe_allow_html=True)
    
    # Component breakdown
    st.markdown("### 📊 Confidence Components")
    components = confidence_data['components']
    
    cols = st.columns(4)
    component_names = ['Technical', 'Fundamental', 'Sentiment', 'Trend']
    component_emojis = ['📈', '💰', '📰', '🚀']
    
    for i, (comp_key, comp_name) in enumerate(zip(['technical', 'fundamental', 'sentiment', 'trend'], component_names)):
        with cols[i]:
            comp_data = components.get(comp_key, {'score': 0, 'reason': 'N/A'})
            st.metric(
                f"{component_emojis[i]} {comp_name}",
                f"{comp_data['score']:.0f}%",
                help=comp_data['reason']
            )
    
    # Explanations and recommendations
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("### 💡 Key Insights")
        for explanation in confidence_data['explanations']:
            st.write(explanation)
    
    with col2:
        st.markdown("### 🎯 Recommendations")
        for rec in confidence_data['recommendations']:
            st.write(rec)


# Integration function for your existing code
def enhance_with_confidence(analysis_results: List[Dict]) -> List[Dict]:
    """Add confidence scoring to existing analysis results"""
    
    scorer = ConfidenceScorer()
    
    for result in analysis_results:
        # Calculate confidence score
        confidence = scorer.calculate_confidence_score(
            result['ticker'], 
            result
        )
        
        # Add to results
        result['confidence'] = confidence
        result['confidence_score'] = confidence['total_score']
        result['confidence_level'] = confidence['confidence_level']
    
    # Sort by confidence score
    return sorted(analysis_results, key=lambda x: x['confidence_score'], reverse=True)