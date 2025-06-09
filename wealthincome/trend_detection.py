# trend_detection.py
"""
Future Trend Detection Engine for wealthincome
Developed by: Quantum Mind, Data Oracle, Global Navigator
Purpose: Identify emerging trends and find stocks aligned with future themes
"""

import numpy as np
import pandas as pd
import yfinance as yf
from typing import Dict, List, Set, Tuple
import streamlit as st
from datetime import datetime, timedelta
import requests
from collections import defaultdict

class TrendDetectionEngine:
    """Identify and track emerging market trends"""
    
    def __init__(self):
        # Quantum Mind's trend taxonomy
        self.trend_categories = {
            'technological_revolution': {
                'quantum_computing': {
                    'keywords': ['quantum', 'qubit', 'quantum supremacy', 'quantum processor'],
                    'leaders': ['IONQ', 'RGTI', 'QUBT', 'IBM', 'GOOGL'],
                    'related_sectors': ['semiconductors', 'cloud_computing', 'cybersecurity'],
                    'growth_indicators': ['patent_filings', 'research_papers', 'government_funding']
                },
                'artificial_intelligence': {
                    'keywords': ['AI', 'machine learning', 'neural network', 'GPT', 'LLM'],
                    'leaders': ['NVDA', 'MSFT', 'GOOGL', 'META', 'PLTR', 'AI'],
                    'related_sectors': ['semiconductors', 'cloud_computing', 'software'],
                    'growth_indicators': ['ai_adoption_rate', 'compute_demand', 'model_complexity']
                },
                'robotics_automation': {
                    'keywords': ['robotics', 'automation', 'autonomous', 'cobots'],
                    'leaders': ['TSLA', 'ABB', 'ROK', 'ISRG', 'IRBT'],
                    'related_sectors': ['manufacturing', 'logistics', 'healthcare'],
                    'growth_indicators': ['labor_shortage', 'automation_roi', 'deployment_rate']
                }
            },
            'energy_transition': {
                'renewable_energy': {
                    'keywords': ['solar', 'wind', 'renewable', 'clean energy', 'green'],
                    'leaders': ['ENPH', 'SEDG', 'RUN', 'NEE', 'BEP'],
                    'related_sectors': ['utilities', 'energy_storage', 'grid_infrastructure'],
                    'growth_indicators': ['policy_support', 'cost_parity', 'adoption_rate']
                },
                'energy_storage': {
                    'keywords': ['battery', 'energy storage', 'lithium', 'grid storage'],
                    'leaders': ['TSLA', 'ALB', 'LAC', 'QS', 'PLUG'],
                    'related_sectors': ['electric_vehicles', 'renewable_energy', 'materials'],
                    'growth_indicators': ['battery_costs', 'energy_density', 'grid_integration']
                },
                'hydrogen_economy': {
                    'keywords': ['hydrogen', 'fuel cell', 'green hydrogen', 'H2'],
                    'leaders': ['PLUG', 'BLDP', 'FCEL', 'BE', 'NEL'],
                    'related_sectors': ['renewable_energy', 'transportation', 'industrial'],
                    'growth_indicators': ['production_costs', 'infrastructure', 'policy_mandates']
                }
            },
            'biotech_revolution': {
                'gene_therapy': {
                    'keywords': ['CRISPR', 'gene editing', 'gene therapy', 'CAR-T'],
                    'leaders': ['CRSP', 'EDIT', 'NTLA', 'BEAM', 'BLUE'],
                    'related_sectors': ['pharmaceuticals', 'diagnostics', 'agriculture'],
                    'growth_indicators': ['clinical_trials', 'FDA_approvals', 'treatment_costs']
                },
                'personalized_medicine': {
                    'keywords': ['precision medicine', 'biomarkers', 'genomics', 'diagnostics'],
                    'leaders': ['ILMN', 'TMO', 'DHR', 'A', 'EXAS'],
                    'related_sectors': ['diagnostics', 'data_analytics', 'pharmaceuticals'],
                    'growth_indicators': ['sequencing_costs', 'data_availability', 'clinical_adoption']
                }
            },
            'digital_transformation': {
                'metaverse': {
                    'keywords': ['metaverse', 'virtual reality', 'AR', 'VR', 'virtual worlds'],
                    'leaders': ['META', 'RBLX', 'U', 'NVDA', 'MSFT'],
                    'related_sectors': ['gaming', 'social_media', 'semiconductors'],
                    'growth_indicators': ['user_adoption', 'hardware_costs', 'content_creation']
                },
                'fintech_revolution': {
                    'keywords': ['fintech', 'digital payments', 'blockchain', 'DeFi', 'crypto'],
                    'leaders': ['SQ', 'PYPL', 'COIN', 'SOFI', 'AFRM'],
                    'related_sectors': ['banking', 'payments', 'blockchain'],
                    'growth_indicators': ['digital_payment_volume', 'unbanked_population', 'regulatory_clarity']
                },
                'cybersecurity': {
                    'keywords': ['cybersecurity', 'zero trust', 'ransomware', 'cloud security'],
                    'leaders': ['CRWD', 'ZS', 'PANW', 'NET', 'S', 'FTNT'],
                    'related_sectors': ['cloud_computing', 'software', 'enterprise_services'],
                    'growth_indicators': ['breach_costs', 'attack_frequency', 'security_spending']
                }
            }
        }
        
        # Trend strength scoring weights
        self.trend_weights = {
            'momentum': 0.25,          # Price momentum of leaders
            'volume': 0.20,            # Volume trends
            'news_mentions': 0.20,     # Media coverage
            'correlation': 0.15,       # Cross-stock correlation
            'fundamentals': 0.20       # Revenue growth of sector
        }
        
    def scan_for_trends(self, timeframe: str = '3mo') -> Dict[str, Dict]:
        """
        Scan all tracked trends and score their current strength
        Returns ranked trends with scores and opportunities
        """
        
        trend_scores = {}
        
        for category, trends in self.trend_categories.items():
            for trend_name, trend_data in trends.items():
                # Calculate trend strength
                trend_score = self._calculate_trend_strength(
                    trend_name, 
                    trend_data, 
                    timeframe
                )
                
                # Find opportunities within trend
                opportunities = self._find_trend_opportunities(
                    trend_data['leaders'],
                    trend_score
                )
                
                trend_scores[trend_name] = {
                    'category': category,
                    'score': trend_score['total_score'],
                    'momentum': trend_score['momentum'],
                    'leaders': trend_data['leaders'],
                    'opportunities': opportunities,
                    'keywords': trend_data['keywords'],
                    'growth_indicators': trend_data['growth_indicators'],
                    'strength_factors': trend_score['factors']
                }
        
        # Sort by score
        sorted_trends = dict(sorted(
            trend_scores.items(), 
            key=lambda x: x[1]['score'], 
            reverse=True
        ))
        
        return sorted_trends
    
    def _calculate_trend_strength(self, trend_name: str, trend_data: Dict, timeframe: str) -> Dict:
        """Calculate comprehensive trend strength score"""
        
        factors = []
        scores = {}
        
        # 1. Momentum Score (25%)
        momentum_score = self._calculate_momentum_score(trend_data['leaders'], timeframe)
        scores['momentum'] = momentum_score
        if momentum_score > 70:
            factors.append(f"Strong price momentum ({momentum_score:.0f})")
        
        # 2. Volume Score (20%)
        volume_score = self._calculate_volume_score(trend_data['leaders'])
        scores['volume'] = volume_score
        if volume_score > 70:
            factors.append(f"Increasing volume interest ({volume_score:.0f})")
        
        # 3. News Mentions (20%) - Simplified for now
        news_score = self._calculate_news_score(trend_data['keywords'])
        scores['news'] = news_score
        if news_score > 70:
            factors.append(f"High media coverage ({news_score:.0f})")
        
        # 4. Correlation Score (15%)
        correlation_score = self._calculate_correlation_score(trend_data['leaders'])
        scores['correlation'] = correlation_score
        if correlation_score > 70:
            factors.append(f"Sector moving together ({correlation_score:.0f})")
        
        # 5. Fundamental Score (20%)
        fundamental_score = self._calculate_fundamental_score(trend_data['leaders'])
        scores['fundamentals'] = fundamental_score
        if fundamental_score > 70:
            factors.append(f"Strong fundamentals ({fundamental_score:.0f})")
        
        # Calculate weighted total
        total_score = (
            scores['momentum'] * self.trend_weights['momentum'] +
            scores['volume'] * self.trend_weights['volume'] +
            scores['news'] * self.trend_weights['news_mentions'] +
            scores['correlation'] * self.trend_weights['correlation'] +
            scores['fundamentals'] * self.trend_weights['fundamentals']
        )
        
        return {
            'total_score': round(total_score, 1),
            'momentum': round(momentum_score, 1),
            'volume': round(volume_score, 1),
            'news': round(news_score, 1),
            'correlation': round(correlation_score, 1),
            'fundamentals': round(fundamental_score, 1),
            'factors': factors
        }
    
    def _calculate_momentum_score(self, tickers: List[str], timeframe: str) -> float:
        """Calculate price momentum for trend leaders"""
        
        momentum_scores = []
        
        for ticker in tickers[:5]:  # Top 5 leaders
            try:
                stock = yf.Ticker(ticker)
                hist = stock.history(period=timeframe)
                
                if len(hist) > 0:
                    # Calculate return
                    returns = (hist['Close'][-1] / hist['Close'][0] - 1) * 100
                    
                    # Convert to score (0-100)
                    if returns > 50:
                        score = 100
                    elif returns > 20:
                        score = 80 + (returns - 20) * 0.67
                    elif returns > 0:
                        score = 50 + (returns * 1.5)
                    else:
                        score = max(0, 50 + returns)
                    
                    momentum_scores.append(score)
            except:
                continue
        
        return np.mean(momentum_scores) if momentum_scores else 50
    
    def _calculate_volume_score(self, tickers: List[str]) -> float:
        """Calculate volume trend score"""
        
        volume_scores = []
        
        for ticker in tickers[:5]:
            try:
                stock = yf.Ticker(ticker)
                hist = stock.history(period='3mo')
                
                if len(hist) > 20:
                    # Compare recent volume to average
                    recent_vol = hist['Volume'][-10:].mean()
                    avg_vol = hist['Volume'][:-10].mean()
                    
                    if avg_vol > 0:
                        vol_ratio = recent_vol / avg_vol
                        
                        # Convert to score
                        if vol_ratio > 2:
                            score = 100
                        elif vol_ratio > 1.5:
                            score = 80 + (vol_ratio - 1.5) * 40
                        elif vol_ratio > 1:
                            score = 50 + (vol_ratio - 1) * 60
                        else:
                            score = vol_ratio * 50
                        
                        volume_scores.append(score)
            except:
                continue
        
        return np.mean(volume_scores) if volume_scores else 50
    
    def _calculate_news_score(self, keywords: List[str]) -> float:
        """Simplified news score - in production would use news API"""
        # Placeholder - returns higher scores for hot trends
        hot_keywords = ['AI', 'quantum', 'cybersecurity', 'gene therapy']
        
        score = 50  # Base score
        for keyword in keywords:
            if any(hot in keyword.lower() for hot in hot_keywords):
                score += 10
        
        return min(score, 100)
    
    def _calculate_correlation_score(self, tickers: List[str]) -> float:
        """Calculate how correlated the trend leaders are"""
        
        try:
            # Get price data for correlation
            price_data = {}
            for ticker in tickers[:5]:
                stock = yf.Ticker(ticker)
                hist = stock.history(period='1mo')
                if len(hist) > 0:
                    price_data[ticker] = hist['Close']
            
            if len(price_data) > 1:
                # Create DataFrame and calculate correlation
                df = pd.DataFrame(price_data)
                corr_matrix = df.pct_change().corr()
                
                # Average correlation (excluding diagonal)
                mask = np.ones_like(corr_matrix, dtype=bool)
                np.fill_diagonal(mask, 0)
                avg_corr = corr_matrix.where(mask).mean().mean()
                
                # Convert to score (higher correlation = stronger trend)
                score = min(100, max(0, avg_corr * 100))
                return score
        except:
            pass
        
        return 50
    
    def _calculate_fundamental_score(self, tickers: List[str]) -> float:
        """Calculate fundamental strength of trend leaders"""
        
        fundamental_scores = []
        
        for ticker in tickers[:5]:
            try:
                stock = yf.Ticker(ticker)
                info = stock.info
                
                score = 50  # Base score
                
                # Revenue growth
                rev_growth = info.get('revenueGrowth', 0)
                if rev_growth > 0.3:
                    score += 20
                elif rev_growth > 0.15:
                    score += 10
                
                # Gross margins
                margins = info.get('grossMargins', 0)
                if margins > 0.5:
                    score += 15
                elif margins > 0.3:
                    score += 8
                
                # Forward P/E vs trailing (growth expectation)
                forward_pe = info.get('forwardPE', 0)
                trailing_pe = info.get('trailingPE', 0)
                if forward_pe > 0 and trailing_pe > 0 and forward_pe < trailing_pe:
                    score += 15  # Earnings expected to grow
                
                fundamental_scores.append(min(score, 100))
            except:
                continue
        
        return np.mean(fundamental_scores) if fundamental_scores else 50
    
    def _find_trend_opportunities(self, leaders: List[str], trend_score: Dict) -> List[Dict]:
        """Find stocks that could benefit from the trend"""
        
        opportunities = []
        
        # For now, return the leaders with opportunity scores
        # In production, would scan for related stocks
        for ticker in leaders:
            opportunities.append({
                'ticker': ticker,
                'opportunity_score': trend_score['total_score'] * 0.8,  # Slight discount
                'reason': 'Trend leader',
                'entry_timing': 'On pullback to support' if trend_score['momentum'] > 80 else 'Current levels'
            })
        
        return opportunities[:5]  # Top 5 opportunities
    
    def find_stocks_by_trend(self, trend_name: str) -> List[Dict]:
        """Find all stocks related to a specific trend"""
        
        # Find the trend in our taxonomy
        for category, trends in self.trend_categories.items():
            if trend_name in trends:
                trend_data = trends[trend_name]
                
                # Get extended list (in production, would query for related stocks)
                stocks = []
                
                # Add leaders
                for ticker in trend_data['leaders']:
                    stocks.append({
                        'ticker': ticker,
                        'role': 'Leader',
                        'trend': trend_name,
                        'category': category
                    })
                
                # Add related sectors (placeholder for extended search)
                # In production, would search for stocks in related sectors
                
                return stocks
        
        return []
    
    def get_trend_report(self, trend_name: str) -> Dict:
        """Generate comprehensive report for a specific trend"""
        
        for category, trends in self.trend_categories.items():
            if trend_name in trends:
                trend_data = trends[trend_name]
                
                # Calculate current strength
                trend_score = self._calculate_trend_strength(
                    trend_name,
                    trend_data,
                    '3mo'
                )
                
                # Create report
                report = {
                    'name': trend_name.replace('_', ' ').title(),
                    'category': category.replace('_', ' ').title(),
                    'current_score': trend_score['total_score'],
                    'momentum': trend_score['momentum'],
                    'leaders': trend_data['leaders'],
                    'keywords': trend_data['keywords'],
                    'growth_drivers': trend_data['growth_indicators'],
                    'investment_thesis': self._generate_investment_thesis(trend_name, trend_score),
                    'risks': self._identify_trend_risks(trend_name),
                    'timeline': self._estimate_trend_timeline(trend_name),
                    'related_trends': trend_data.get('related_sectors', [])
                }
                
                return report
        
        return {}
    
    def _generate_investment_thesis(self, trend_name: str, trend_score: Dict) -> str:
        """Generate investment thesis for the trend"""
        
        thesis_templates = {
            'quantum_computing': "Quantum computing represents a paradigm shift in computational power, "
                                "potentially solving problems impossible for classical computers. "
                                "Early investors could benefit from the transition from research to commercialization.",
            
            'artificial_intelligence': "AI is transforming every industry, creating unprecedented demand "
                                      "for compute power, software, and services. The total addressable market "
                                      "continues to expand as new use cases emerge.",
            
            'renewable_energy': "The global energy transition is accelerating, driven by policy support, "
                               "cost competitiveness, and climate urgency. Renewable energy is moving from "
                               "alternative to mainstream.",
            
            'gene_therapy': "Gene therapy promises to cure previously untreatable diseases. As costs decline "
                           "and efficacy improves, the market opportunity expands exponentially.",
            
            'cybersecurity': "Rising cyber threats and digital transformation create sustained demand for "
                            "security solutions. The shift to cloud and remote work amplifies this need.",
            
            'metaverse': "The metaverse represents the next evolution of the internet, combining social, "
                        "gaming, and commerce in immersive digital worlds. Early platforms are defining "
                        "the standards.",
            
            'fintech_revolution': "Financial technology is democratizing access to financial services and "
                                 "creating more efficient markets. Traditional finance disruption creates "
                                 "massive opportunities."
        }
        
        base_thesis = thesis_templates.get(trend_name, f"The {trend_name.replace('_', ' ')} trend is emerging.")
        
        if trend_score['total_score'] > 80:
            strength_comment = " The trend shows exceptional strength with broad-based momentum."
        elif trend_score['total_score'] > 60:
            strength_comment = " Current indicators suggest the trend is gaining mainstream adoption."
        else:
            strength_comment = " The trend is in early stages but showing promising developments."
        
        return base_thesis + strength_comment
    
    def _identify_trend_risks(self, trend_name: str) -> List[str]:
        """Identify key risks for the trend"""
        
        common_risks = {
            'quantum_computing': [
                "Technical challenges in scaling quantum systems",
                "Long development timeline to commercial viability",
                "Competition from improving classical computing"
            ],
            'artificial_intelligence': [
                "Regulatory uncertainty and potential restrictions",
                "High valuations reflecting future expectations",
                "Concentration risk in few large players"
            ],
            'renewable_energy': [
                "Policy dependency and political risk",
                "Intermittency and storage challenges",
                "Commodity price volatility"
            ],
            'gene_therapy': [
                "Clinical trial failures and safety concerns",
                "Regulatory approval uncertainty",
                "High development costs and long timelines"
            ],
            'cybersecurity': [
                "Rapid technology evolution requiring constant innovation",
                "Price competition as market matures",
                "Economic sensitivity of IT spending"
            ],
            'metaverse': [
                "Uncertain user adoption and retention",
                "High infrastructure and development costs",
                "Privacy and safety concerns"
            ],
            'fintech_revolution': [
                "Regulatory crackdowns and compliance costs",
                "Competition from traditional finance adapting",
                "Economic cycle sensitivity"
            ]
        }
        
        return common_risks.get(trend_name, ["Market adoption uncertainty", "Competition", "Regulatory risk"])
    
    def _estimate_trend_timeline(self, trend_name: str) -> Dict[str, str]:
        """Estimate timeline for trend development"""
        
        timelines = {
            'quantum_computing': {
                'near_term': '1-2 years: Continued R&D, limited commercial applications',
                'medium_term': '3-5 years: First killer apps, industry adoption begins',
                'long_term': '5-10 years: Mainstream adoption, market maturity'
            },
            'artificial_intelligence': {
                'near_term': '1-2 years: Rapid deployment across industries',
                'medium_term': '3-5 years: AI becomes standard business infrastructure',
                'long_term': '5-10 years: AGI development, societal transformation'
            },
            'renewable_energy': {
                'near_term': '1-2 years: Continued cost declines, grid parity expansion',
                'medium_term': '3-5 years: Storage solutions scale, fossil fuel displacement',
                'long_term': '5-10 years: Dominant energy source globally'
            }
        }
        
        default_timeline = {
            'near_term': '1-2 years: Early adoption and proof of concept',
            'medium_term': '3-5 years: Mainstream adoption begins',
            'long_term': '5-10 years: Market maturity and consolidation'
        }
        
        return timelines.get(trend_name, default_timeline)


def create_trend_visualization(trends: Dict[str, Dict]) -> None:
    """Create visual representation of trend landscape"""
    
    st.markdown("## 🌐 Trend Landscape")
    
    # Top trends summary
    top_trends = list(trends.items())[:5]
    
    cols = st.columns(5)
    for i, (trend_name, trend_data) in enumerate(top_trends):
        with cols[i]:
            score = trend_data['score']
            
            # Color based on score
            if score > 80:
                color = "#00ff00"
                strength = "🔥 Hot"
            elif score > 60:
                color = "#90EE90"
                strength = "📈 Strong"
            else:
                color = "#FFD700"
                strength = "🌱 Emerging"
            
            st.markdown(f"""
            <div style="text-align: center; padding: 10px; background-color: #1e1e1e; 
                        border-radius: 10px; border: 2px solid {color};">
                <h4 style="color: {color}; margin: 0;">{trend_name.replace('_', ' ').title()}</h4>
                <h2 style="color: {color}; margin: 5px 0;">{score:.0f}</h2>
                <p style="color: #888; margin: 0; font-size: 12px;">{strength}</p>
            </div>
            """, unsafe_allow_html=True)
    
    # Detailed trend cards
    st.markdown("### 📊 Trend Details")
    
    for trend_name, trend_data in top_trends:
        with st.expander(f"{trend_name.replace('_', ' ').title()} - Score: {trend_data['score']:.0f}"):
            col1, col2 = st.columns(2)
            
            with col1:
                st.markdown("**Leaders:**")
                for leader in trend_data['leaders'][:5]:
                    st.write(f"• {leader}")
                
                st.markdown("**Key Factors:**")
                for factor in trend_data.get('strength_factors', [])[:3]:
                    st.write(f"• {factor}")
            
            with col2:
                st.markdown("**Momentum Score:** {:.0f}".format(trend_data.get('momentum', 0)))
                st.markdown("**Category:** {}".format(trend_data.get('category', '').replace('_', ' ').title()))
                
                if trend_data.get('opportunities'):
                    st.markdown("**Top Opportunity:**")
                    opp = trend_data['opportunities'][0]
                    st.write(f"{opp['ticker']} - Score: {opp['opportunity_score']:.0f}")


# Integration function
def scan_market_trends() -> Tuple[Dict, pd.DataFrame]:
    """Scan market for all trends and return analysis"""
    
    engine = TrendDetectionEngine()
    
    # Scan all trends
    trends = engine.scan_for_trends()
    
    # Convert to DataFrame for easy filtering
    trend_data = []
    for trend_name, data in trends.items():
        for opp in data.get('opportunities', []):
            trend_data.append({
                'Trend': trend_name.replace('_', ' ').title(),
                'Category': data['category'].replace('_', ' ').title(),
                'Trend Score': data['score'],
                'Ticker': opp['ticker'],
                'Opportunity Score': opp['opportunity_score'],
                'Entry': opp['entry_timing']
            })
    
    df = pd.DataFrame(trend_data)
    
    return trends, df