def calculate_ai_scores(ticker_data):
    scores = {'day_trade': 0, 'swing_trade': 0, 'position_trade': 0, 'overall': 0}
    if not ticker_data: return scores

    tech = ticker_data.get('technicals')
    intra = ticker_data.get('intraday')
    fund = ticker_data.get('fundamentals')
    news = ticker_data.get('news_sentiment')

    # Day Trading Score (focus on momentum and intraday metrics)
    if intra:
        # Intraday momentum (0-30 points)
        if intra.get('intraday_change', 0) > 2:
            scores['day_trade'] += min(intra['intraday_change'] * 3, 15)
        elif intra.get('intraday_change', 0) < -2:
            scores['day_trade'] -= 10
            
        # Price position in range (0-15 points)
        price_pos = intra.get('price_position', 0.5)
        if price_pos > 0.8:  # Near high of day
            scores['day_trade'] += 15
        elif price_pos > 0.6:
            scores['day_trade'] += 10
        elif price_pos < 0.2:  # Near low of day
            scores['day_trade'] -= 5
            
        # Volume surge (0-20 points)
        vol_surge = intra.get('volume_surge', 1)
        if vol_surge > 3:
            scores['day_trade'] += 20
        elif vol_surge > 2:
            scores['day_trade'] += 15
        elif vol_surge > 1.5:
            scores['day_trade'] += 10
            
    # Technical indicators scoring (applies to all strategies)
    if tech:
        rsi = tech.get('rsi')
        price = tech.get('price', 0)
        sma_20 = tech.get('sma_20')
        sma_50 = tech.get('sma_50')
        macd = tech.get('macd')
        macd_signal = tech.get('macd_signal')
        bb_upper = tech.get('bb_upper')
        bb_lower = tech.get('bb_lower')
        volume_trend = tech.get('volume_trend', 0)
        
        # RSI scoring
        if rsi is not None:
            # Day trade: extreme levels for quick reversals
            if 30 < rsi < 70:
                scores['day_trade'] += 10
            if rsi < 30:  # Oversold bounce potential
                scores['day_trade'] += 15
                scores['swing_trade'] += 20
            elif rsi > 70:  # Overbought but could continue
                scores['day_trade'] += 5
                scores['swing_trade'] -= 5
                
            # Swing/Position: prefer moderate RSI
            if 40 < rsi < 60:
                scores['swing_trade'] += 15
                scores['position_trade'] += 15
                
        # Moving average alignment
        if price and sma_20 and sma_50:
            # Bullish alignment
            if price > sma_20 > sma_50:
                scores['day_trade'] += 10
                scores['swing_trade'] += 20
                scores['position_trade'] += 25
            # Price above 20 SMA
            elif price > sma_20:
                scores['day_trade'] += 5
                scores['swing_trade'] += 10
                scores['position_trade'] += 10
            # Bearish alignment
            elif price < sma_20 < sma_50:
                scores['day_trade'] -= 10
                scores['swing_trade'] -= 15
                scores['position_trade'] -= 20
                
        # MACD scoring
        if macd is not None and macd_signal is not None:
            # Bullish crossover
            if macd > macd_signal:
                scores['day_trade'] += 10
                scores['swing_trade'] += 15
                scores['position_trade'] += 10
            # Strong momentum
            if macd > 0 and macd > macd_signal:
                scores['swing_trade'] += 10
                scores['position_trade'] += 15
                
        # Bollinger Bands
        if price and bb_upper and bb_lower:
            # Near lower band (oversold)
            if price < bb_lower * 1.02:
                scores['day_trade'] += 15
                scores['swing_trade'] += 20
            # Near upper band (overbought)
            elif price > bb_upper * 0.98:
                scores['day_trade'] += 5  # Could continue
                scores['swing_trade'] -= 5
                
        # Volume trend
        if volume_trend > 2:
            scores['day_trade'] += 15
            scores['swing_trade'] += 10
        elif volume_trend > 1.5:
            scores['day_trade'] += 10
            scores['swing_trade'] += 5
            
    # Fundamental scoring (mainly for position trading)
    if fund:
        pe_ratio = fund.get('pe_ratio')
        peg_ratio = fund.get('peg_ratio')
        revenue_growth = fund.get('revenue_growth')
        profit_margins = fund.get('profit_margins')
        debt_to_equity = fund.get('debt_to_equity')
        
        # PE Ratio
        if pe_ratio and 0 < pe_ratio < 25:
            scores['position_trade'] += 15
            scores['swing_trade'] += 5
        elif pe_ratio and pe_ratio > 50:
            scores['position_trade'] -= 10
            
        # PEG Ratio
        if peg_ratio and 0 < peg_ratio < 1.5:
            scores['position_trade'] += 15
            scores['swing_trade'] += 10
            
        # Revenue Growth
        if revenue_growth and revenue_growth > 0.2:
            scores['position_trade'] += 20
            scores['swing_trade'] += 10
        elif revenue_growth and revenue_growth > 0.1:
            scores['position_trade'] += 10
            scores['swing_trade'] += 5
            
        # Profit Margins
        if profit_margins and profit_margins > 0.2:
            scores['position_trade'] += 15
        elif profit_margins and profit_margins > 0.1:
            scores['position_trade'] += 10
            
        # Debt to Equity
        if debt_to_equity is not None:
            if debt_to_equity < 0.5:
                scores['position_trade'] += 10
            elif debt_to_equity > 2:
                scores['position_trade'] -= 10
    
    # Add relative volume boost for all strategies
    rvol = ticker_data.get('rvol', 0)
    if rvol > 3:
        scores['day_trade'] += 10
        scores['swing_trade'] += 5
    elif rvol > 2:
        scores['day_trade'] += 5
        scores['swing_trade'] += 3
        
    # Add market cap consideration
    market_cap = ticker_data.get('market_cap', 0)
    if market_cap > 10_000_000_000:  # Large cap
        scores['position_trade'] += 10
    elif market_cap > 2_000_000_000:  # Mid cap
        scores['swing_trade'] += 5
    elif 300_000_000 < market_cap < 2_000_000_000:  # Small cap
        scores['day_trade'] += 5
        
    # News sentiment boost/penalty
    if news:
        news_boost = 0
        if news['label'] == 'Positive':
            news_boost = news['score'] * 10  # Max +10 points
        elif news['label'] == 'Negative':
            news_boost = -abs(news['score']) * 10  # Max -10 points
            
        # Apply news boost to all scores
        scores['day_trade'] = scores['day_trade'] + news_boost * 1.5  # Day traders care more about news
        scores['swing_trade'] = scores['swing_trade'] + news_boost
        scores['position_trade'] = scores['position_trade'] + news_boost * 0.5  # Position traders care less about short-term news
    
    # Ensure scores are within 0-100 range and round
    for key in ['day_trade', 'swing_trade', 'position_trade']:
        scores[key] = round(max(0, min(100, scores[key])), 2)
    
    # Calculate overall score
    scores['overall'] = round((scores['day_trade'] + scores['swing_trade'] + scores['position_trade']) / 3, 2)
    
    return scores
