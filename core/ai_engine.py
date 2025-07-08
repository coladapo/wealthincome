"""
AI Engine - Machine learning and AI capabilities for trading signals
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
import numpy as np
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

class SignalType(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    WATCH = "WATCH"

@dataclass
class AISignal:
    symbol: str
    signal_type: SignalType
    confidence: float
    price_target: Optional[float]
    stop_loss: Optional[float]
    reasoning: List[str]
    technical_indicators: Dict[str, Any]
    sentiment_score: float
    risk_score: float
    timestamp: datetime
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'symbol': self.symbol,
            'signal_type': self.signal_type.value,
            'confidence': self.confidence,
            'price_target': self.price_target,
            'stop_loss': self.stop_loss,
            'reasoning': self.reasoning,
            'technical_indicators': self.technical_indicators,
            'sentiment_score': self.sentiment_score,
            'risk_score': self.risk_score,
            'timestamp': self.timestamp.isoformat()
        }

class AIEngine:
    """AI engine for generating trading signals and market analysis"""
    
    def __init__(self, config=None):
        self.config = config
        self.data_manager = None
        self.model_cache = {}
        self.signal_history = []
        
    def set_data_manager(self, data_manager):
        """Set the data manager for market data"""
        self.data_manager = data_manager
        
    def generate_signals(self, symbols: List[str], 
                        confidence_threshold: float = 0.7) -> List[AISignal]:
        """Generate AI trading signals for given symbols"""
        
        signals = []
        
        for symbol in symbols:
            try:
                signal = self._analyze_symbol(symbol)
                if signal and signal.confidence >= confidence_threshold:
                    signals.append(signal)
            except Exception as e:
                logger.error(f"Error analyzing {symbol}: {e}")
                continue
        
        # Sort by confidence
        signals.sort(key=lambda x: x.confidence, reverse=True)
        
        # Store in history
        self.signal_history.extend(signals)
        
        return signals
    
    def _analyze_symbol(self, symbol: str) -> Optional[AISignal]:
        """Analyze a single symbol and generate signal"""
        
        # Get market data
        if not self.data_manager:
            return self._generate_mock_signal(symbol)
        
        try:
            stock_data = self.data_manager.get_stock_data([symbol], period="30d")
            if symbol not in stock_data or not stock_data[symbol]:
                return None
                
            data = stock_data[symbol]
            
            # Perform technical analysis
            technical_indicators = self._calculate_technical_indicators(data)
            
            # Analyze sentiment
            sentiment_score = self._analyze_sentiment(symbol)
            
            # Calculate risk score
            risk_score = self._calculate_risk_score(data, technical_indicators)
            
            # Generate signal
            signal_type, confidence = self._generate_signal_prediction(
                technical_indicators, sentiment_score, risk_score
            )
            
            # Generate reasoning
            reasoning = self._generate_reasoning(
                signal_type, technical_indicators, sentiment_score, risk_score
            )
            
            # Calculate price targets
            current_price = data.get('info', {}).get('regularMarketPrice', 0)
            price_target, stop_loss = self._calculate_price_targets(
                signal_type, current_price, technical_indicators
            )
            
            return AISignal(
                symbol=symbol,
                signal_type=signal_type,
                confidence=confidence,
                price_target=price_target,
                stop_loss=stop_loss,
                reasoning=reasoning,
                technical_indicators=technical_indicators,
                sentiment_score=sentiment_score,
                risk_score=risk_score,
                timestamp=datetime.now()
            )
            
        except Exception as e:
            logger.error(f"Error in symbol analysis for {symbol}: {e}")
            return None
    
    def _calculate_technical_indicators(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate technical indicators"""
        
        # Mock technical analysis - in real implementation, use TA-Lib or similar
        history = data.get('history', {})
        
        if not history or 'Close' not in history:
            return {}
        
        closes = list(history['Close'].values()) if isinstance(history['Close'], dict) else []
        
        if len(closes) < 20:
            return {}
        
        # Simple moving averages
        sma_20 = np.mean(closes[-20:])
        sma_50 = np.mean(closes[-50:]) if len(closes) >= 50 else sma_20
        
        # RSI calculation (simplified)
        gains = []
        losses = []
        for i in range(1, min(15, len(closes))):
            change = closes[i] - closes[i-1]
            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))
        
        avg_gain = np.mean(gains) if gains else 0
        avg_loss = np.mean(losses) if losses else 0.01
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        current_price = closes[-1]
        
        return {
            'sma_20': sma_20,
            'sma_50': sma_50,
            'rsi': rsi,
            'current_price': current_price,
            'price_vs_sma20': (current_price / sma_20 - 1) * 100,
            'price_vs_sma50': (current_price / sma_50 - 1) * 100,
            'volume_ratio': 1.2  # Mock value
        }
    
    def _analyze_sentiment(self, symbol: str) -> float:
        """Analyze market sentiment for symbol"""
        
        # Mock sentiment analysis - in real implementation, use news/social media data
        np.random.seed(hash(symbol) % 1000)
        
        # Generate sentiment score between 0 and 1
        sentiment = 0.5 + np.random.uniform(-0.3, 0.3)
        return max(0.0, min(1.0, sentiment))
    
    def _calculate_risk_score(self, data: Dict[str, Any], 
                            technical_indicators: Dict[str, Any]) -> float:
        """Calculate risk score for the symbol"""
        
        # Mock risk calculation
        volatility = 0.02  # 2% daily volatility assumption
        market_cap = data.get('info', {}).get('marketCap', 1e9)
        
        # Higher risk for smaller companies and higher volatility
        size_risk = 1 / np.log10(market_cap / 1e6) if market_cap > 1e6 else 1.0
        volatility_risk = volatility * 50  # Scale to 0-1
        
        return max(0.1, min(1.0, (size_risk + volatility_risk) / 2))
    
    def _generate_signal_prediction(self, technical_indicators: Dict[str, Any],
                                  sentiment_score: float, 
                                  risk_score: float) -> Tuple[SignalType, float]:
        """Generate signal prediction and confidence"""
        
        if not technical_indicators:
            return SignalType.HOLD, 0.5
        
        # Scoring system
        score = 0.0
        
        # Technical analysis scoring
        if technical_indicators.get('rsi', 50) < 30:
            score += 0.3  # Oversold
        elif technical_indicators.get('rsi', 50) > 70:
            score -= 0.3  # Overbought
        
        if technical_indicators.get('price_vs_sma20', 0) > 2:
            score += 0.2  # Above SMA
        elif technical_indicators.get('price_vs_sma20', 0) < -2:
            score -= 0.2  # Below SMA
        
        # Sentiment scoring
        score += (sentiment_score - 0.5) * 0.4
        
        # Risk adjustment
        score *= (1 - risk_score * 0.3)
        
        # Determine signal type
        if score > 0.2:
            signal_type = SignalType.BUY
        elif score < -0.2:
            signal_type = SignalType.SELL
        elif abs(score) > 0.1:
            signal_type = SignalType.WATCH
        else:
            signal_type = SignalType.HOLD
        
        # Calculate confidence
        confidence = min(0.95, max(0.5, 0.7 + abs(score)))
        
        return signal_type, confidence
    
    def _generate_reasoning(self, signal_type: SignalType,
                          technical_indicators: Dict[str, Any],
                          sentiment_score: float,
                          risk_score: float) -> List[str]:
        """Generate human-readable reasoning for the signal"""
        
        reasoning = []
        
        if not technical_indicators:
            reasoning.append("Limited technical data available")
            return reasoning
        
        rsi = technical_indicators.get('rsi', 50)
        price_vs_sma20 = technical_indicators.get('price_vs_sma20', 0)
        
        # Technical reasoning
        if rsi < 30:
            reasoning.append("RSI indicates oversold conditions")
        elif rsi > 70:
            reasoning.append("RSI indicates overbought conditions")
        
        if price_vs_sma20 > 5:
            reasoning.append("Price trading significantly above 20-day moving average")
        elif price_vs_sma20 < -5:
            reasoning.append("Price trading below 20-day moving average")
        
        # Sentiment reasoning
        if sentiment_score > 0.7:
            reasoning.append("Positive market sentiment detected")
        elif sentiment_score < 0.3:
            reasoning.append("Negative market sentiment detected")
        
        # Risk reasoning
        if risk_score > 0.7:
            reasoning.append("High volatility and risk factors present")
        elif risk_score < 0.3:
            reasoning.append("Relatively low risk profile")
        
        # Signal-specific reasoning
        if signal_type == SignalType.BUY:
            reasoning.append("Multiple bullish indicators converging")
        elif signal_type == SignalType.SELL:
            reasoning.append("Bearish signals outweigh bullish factors")
        elif signal_type == SignalType.WATCH:
            reasoning.append("Mixed signals suggest monitoring position")
        
        return reasoning[:4]  # Limit to 4 reasons
    
    def _calculate_price_targets(self, signal_type: SignalType, 
                               current_price: float,
                               technical_indicators: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
        """Calculate price targets and stop losses"""
        
        if not current_price or current_price <= 0:
            return None, None
        
        if signal_type == SignalType.BUY:
            # Conservative 5-10% target
            target = current_price * 1.07
            stop_loss = current_price * 0.95
        elif signal_type == SignalType.SELL:
            # Short target
            target = current_price * 0.93
            stop_loss = current_price * 1.05
        else:
            return None, None
        
        return target, stop_loss
    
    def _generate_mock_signal(self, symbol: str) -> AISignal:
        """Generate mock signal for testing when no data manager available"""
        
        np.random.seed(hash(symbol) % 1000)
        
        signal_types = list(SignalType)
        signal_type = np.random.choice(signal_types)
        confidence = np.random.uniform(0.6, 0.9)
        
        current_price = 100.0  # Mock price
        
        if signal_type == SignalType.BUY:
            price_target = current_price * 1.07
            stop_loss = current_price * 0.95
            reasoning = ["Mock bullish signal for testing", "Technical indicators favorable"]
        elif signal_type == SignalType.SELL:
            price_target = current_price * 0.93
            stop_loss = current_price * 1.05
            reasoning = ["Mock bearish signal for testing", "Risk factors identified"]
        else:
            price_target = None
            stop_loss = None
            reasoning = ["Mock signal for testing", "Mixed market conditions"]
        
        return AISignal(
            symbol=symbol,
            signal_type=signal_type,
            confidence=confidence,
            price_target=price_target,
            stop_loss=stop_loss,
            reasoning=reasoning,
            technical_indicators={'rsi': 55, 'sma_20': current_price},
            sentiment_score=0.6,
            risk_score=0.4,
            timestamp=datetime.now()
        )
    
    def get_signal_history(self, days: int = 7) -> List[AISignal]:
        """Get signal history for specified days"""
        
        cutoff_date = datetime.now() - timedelta(days=days)
        
        return [
            signal for signal in self.signal_history
            if signal.timestamp >= cutoff_date
        ]
    
    def get_model_performance(self) -> Dict[str, Any]:
        """Get AI model performance metrics"""
        
        # Mock performance metrics
        return {
            'accuracy': 0.76,
            'precision': 0.72,
            'recall': 0.69,
            'f1_score': 0.71,
            'total_signals': len(self.signal_history),
            'signals_today': len([
                s for s in self.signal_history 
                if s.timestamp.date() == datetime.now().date()
            ]),
            'avg_confidence': np.mean([s.confidence for s in self.signal_history]) if self.signal_history else 0.7
        }
    
    def update_model(self, feedback_data: List[Dict[str, Any]]):
        """Update AI model with feedback data"""
        
        # Mock model update - in real implementation, retrain models
        logger.info(f"Model updated with {len(feedback_data)} feedback samples")
    
    def explain_signal(self, signal: AISignal) -> Dict[str, Any]:
        """Generate detailed explanation for a signal"""
        
        return {
            'signal_strength': {
                'technical': min(1.0, signal.confidence * 1.2),
                'sentiment': signal.sentiment_score,
                'momentum': 0.7,  # Mock
                'volume': 0.8     # Mock
            },
            'key_factors': signal.reasoning,
            'risk_assessment': {
                'overall_risk': signal.risk_score,
                'market_risk': 0.3,
                'company_risk': signal.risk_score * 0.7,
                'timing_risk': 0.4
            },
            'confidence_breakdown': {
                'data_quality': 0.9,
                'model_certainty': signal.confidence,
                'market_conditions': 0.8
            }
        }