# WealthIncome Unified Trading Platform 🚀

A unified AI-driven trading platform that combines advanced frontend design with robust trading capabilities. Built with Streamlit and powered by machine learning for intelligent trading signals.

## 🌟 Features

### 🤖 AI-Powered Intelligence
- **Advanced AI Signals**: Real-time trading signals with confidence scoring
- **Sentiment Analysis**: News and social media sentiment integration
- **Technical Analysis**: Comprehensive technical indicator analysis
- **Risk Management**: AI-powered risk assessment and position sizing

### 📊 Comprehensive Analytics
- **Real-time Dashboard**: Live market data and portfolio tracking
- **Portfolio Analytics**: Detailed performance metrics and attribution
- **Market Overview**: Major indices and sector analysis
- **Trading Journal**: Complete trade history and performance analysis

### 💼 Trading Capabilities
- **Paper Trading**: Risk-free practice environment
- **Portfolio Management**: Position tracking and performance monitoring
- **Watchlist Management**: Personalized stock monitoring
- **Alert System**: Custom price and technical alerts

### 🎨 Modern UI/UX
- **Responsive Design**: Works on desktop and mobile
- **Dark Theme**: Professional trading interface
- **Real-time Updates**: WebSocket integration for live data
- **Interactive Charts**: Advanced charting with Plotly

## 🚀 Quick Start

### Prerequisites
- Python 3.11+
- Redis (optional, for enhanced performance)
- API keys for data providers (see configuration)

### Installation

1. **Clone the repository**
```bash
git clone https://github.com/wealthincome/wealthincome-unified.git
cd wealthincome-unified
```

2. **Install dependencies**
```bash
pip install -r requirements.txt
```

3. **Configure environment**
```bash
cp .env.example .env
# Edit .env with your API keys and preferences
```

4. **Run the application**
```bash
streamlit run app.py
```

5. **Access the platform**
Open your browser to `http://localhost:8501`

### Default Login
- **Username**: `admin`
- **Password**: `admin123`

## 🐳 Docker Deployment

### Quick Start with Docker
```bash
docker build -t wealthincome-unified .
docker run -p 8501:8501 wealthincome-unified
```

### Full Stack with Docker Compose
```bash
# Start all services
docker-compose up -d

# Start with WebSocket server
docker-compose --profile websocket up -d

# Production deployment with Nginx
docker-compose --profile production up -d
```

## ⚙️ Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ENVIRONMENT` | `development` | Environment mode |
| `DEBUG` | `true` | Enable debug mode |
| `OPENAI_API_KEY` | - | OpenAI API key for AI features |
| `REDIS_URL` | - | Redis connection string |
| `DEFAULT_PORTFOLIO_VALUE` | `100000` | Starting portfolio value |
| `CONFIDENCE_THRESHOLD` | `0.7` | Minimum AI confidence for signals |

### Feature Flags

Enable/disable features using environment variables:
- `ENABLE_PAPER_TRADING=true`
- `ENABLE_LIVE_TRADING=false`
- `ENABLE_AI_INSIGHTS=true`
- `ENABLE_NEWS_SENTIMENT=true`

## 📱 User Guide

### Getting Started
1. **Login** with your credentials
2. **Configure Watchlist** - Add stocks you want to monitor
3. **Review AI Signals** - Check daily AI-generated trading signals
4. **Paper Trade** - Practice with the built-in paper trading system
5. **Monitor Portfolio** - Track your performance and analytics

### Key Pages

#### 🏠 Dashboard
- Market overview and AI insights
- Portfolio summary and performance
- Top trading opportunities
- Quick actions and navigation

#### 🧠 AI Signals
- Real-time AI-generated trading signals
- Confidence scoring and reasoning
- Technical and sentiment analysis
- Customizable signal filtering

#### 📈 Trading
- Execute paper trades
- Position management
- Real-time portfolio tracking
- Risk management tools

#### 📊 Analytics
- Detailed performance metrics
- Risk analysis and attribution
- Historical trade analysis
- Custom reporting

## 🛠️ Development

### Project Structure
```
wealthincome-unified/
├── app.py                 # Main application entry point
├── config.py             # Configuration management
├── requirements.txt      # Python dependencies
├── Dockerfile           # Docker configuration
├── docker-compose.yml   # Multi-service deployment
├── core/                # Core business logic
│   ├── __init__.py
│   ├── data_manager.py  # Data management and APIs
│   ├── auth.py          # Authentication system
│   ├── trading_engine.py # Trading logic
│   └── ai_engine.py     # AI/ML capabilities
├── ui/                  # User interface components
│   ├── __init__.py
│   ├── navigation.py    # Navigation system
│   ├── components.py    # Reusable UI components
│   ├── charts.py        # Chart components
│   └── alerts.py        # Alert components
├── pages/               # Application pages
│   ├── __init__.py
│   ├── dashboard.py     # Main dashboard
│   ├── ai_signals.py    # AI signals page
│   ├── trading.py       # Trading interface
│   ├── portfolio.py     # Portfolio management
│   ├── analytics.py     # Analytics and reporting
│   ├── risk.py          # Risk management
│   ├── news.py          # News and sentiment
│   ├── journal.py       # Trading journal
│   └── settings.py      # User settings
├── tests/               # Test suite
└── data/                # Data storage
    ├── cache/           # Temporary data cache
    └── persistent/      # Persistent data storage
```

### Running Tests
```bash
pytest tests/
```

### Code Quality
```bash
# Linting
flake8 .

# Formatting
black .

# Type checking
mypy .
```

## 🔒 Security

### Authentication
- Secure password hashing with bcrypt
- Session management with timeout
- User role-based access control

### Data Protection
- No sensitive data stored in logs
- API keys secured in environment variables
- Database encryption for production

### Best Practices
- Regular security updates
- Input validation and sanitization
- Rate limiting for API endpoints

## 📊 Performance

### Optimization Features
- Redis caching for market data
- Lazy loading for large datasets
- Background data updates
- Efficient chart rendering

### Monitoring
- Application health checks
- Performance metrics
- Error tracking and logging
- Resource usage monitoring

## 🤝 Contributing

We welcome contributions! Please see our [Contributing Guide](CONTRIBUTING.md) for details.

### Development Workflow
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Submit a pull request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🆘 Support

### Documentation
- [User Guide](docs/user-guide.md)
- [API Reference](docs/api-reference.md)
- [Deployment Guide](docs/deployment.md)

### Community
- [GitHub Issues](https://github.com/wealthincome/wealthincome-unified/issues)
- [Discord Community](https://discord.gg/wealthincome)
- [Support Email](mailto:support@wealthincome.ai)

## 🚧 Roadmap

### Upcoming Features
- [ ] Options trading support
- [ ] Social sentiment analysis
- [ ] Advanced chart patterns
- [ ] Mobile app development
- [ ] API for third-party integrations

### Version History
- **v1.0.0** - Initial unified platform release
- **v0.9.0** - Beta release with core features
- **v0.8.0** - AI signals integration
- **v0.7.0** - Frontend redesign completion

## ⚠️ Disclaimer

**This software is for educational and research purposes only. It does not constitute financial advice. Trading involves significant risk and may result in the loss of your invested capital. Always do your own research and consult with financial professionals before making investment decisions.**

---

<div align="center">
  <p><strong>Built with ❤️ by the WealthIncome Team</strong></p>
  <p>🚀 <a href="https://wealthincome.ai">WealthIncome.ai</a> | 📧 <a href="mailto:hello@wealthincome.ai">Contact Us</a> | 🐦 <a href="https://twitter.com/wealthincome">Follow Us</a></p>
</div>