# News Trading Signal System

Real-time news sentiment analysis → BUY/SELL trading signals → TradingView indicator.

## Architecture

```
News Sources → Sentiment Scoring → Signal Aggregation → REST API → TradingView
```

**Sources** (free tier):
- **Finnhub** — real-time company news (60 calls/min free)
- **Alpha Vantage** — GPT-scored news sentiment (25 req/day free)
- **Financial Modeling Prep** — pre-scored stock news
- **RSS Feeds** — Google Finance, MarketWatch, SEC EDGAR (always free)

**Scoring**:
- Pre-scored articles (Alpha Vantage, FMP) are used directly
- Unscored headlines (Finnhub, RSS) are classified by Claude Haiku in ~200ms

**Signal Generation**:
- Weighted average: confidence × impact × recency decay
- BUY when aggregate sentiment ≥ 0.4 with confidence ≥ 0.6
- SELL when aggregate sentiment ≤ -0.4 with confidence ≥ 0.6
- Signals expire after 5 minutes (configurable)

## Quick Start

```bash
# 1. Clone and install
pip install -r requirements.txt

# 2. Configure API keys
cp .env.example .env
# Edit .env with your API keys (at minimum, get a free Finnhub key)

# 3. Run
python main.py
```

The server starts at `http://localhost:8080`. Check signals:
```bash
curl http://localhost:8080/signals
curl http://localhost:8080/signals/AAPL
```

## API Keys (all have free tiers)

| Source | Free Tier | Sign Up |
|--------|-----------|---------|
| Finnhub | 60 calls/min | https://finnhub.io/register |
| Alpha Vantage | 25 req/day | https://www.alphavantage.co/support/#api-key |
| FMP | 250 req/day | https://site.financialmodelingprep.com/developer |
| Anthropic (Claude) | Pay-per-use | https://console.anthropic.com |

**Minimum setup**: Just a Finnhub key + Anthropic key gives you real-time news with LLM scoring. RSS feeds work with zero keys.

## TradingView Integration

Two Pine Script files are provided in `tradingview/`:

1. **`news_sentiment_indicator.pine`** — Overlay indicator showing BUY/SELL labels
2. **`autoview_strategy.pine`** — Full strategy with TP/SL for auto-trading

See `tradingview/webhook_bridge.md` for setup instructions on connecting the signal API to TradingView.

## Configuration

All settings are in `.env` or environment variables:

```env
# Tickers to watch
WATCH_TICKERS=["AAPL","MSFT","SPY","QQQ","TSLA","GOOGL","AMZN","ES"]

# Signal thresholds
BUY_SIGNAL_THRESHOLD=0.4    # sentiment score to trigger buy
SELL_SIGNAL_THRESHOLD=-0.4  # sentiment score to trigger sell
MIN_CONFIDENCE=0.6          # minimum confidence to act

# Polling intervals (seconds)
FINNHUB_POLL_INTERVAL=30
FMP_POLL_INTERVAL=60
ALPHA_VANTAGE_POLL_INTERVAL=300  # conservative for free tier
RSS_POLL_INTERVAL=60
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/signals` | All active signals |
| GET | `/signals/{ticker}` | Signal for specific ticker |
| GET | `/health` | Health check |
| POST | `/webhook/tradingview` | Receive TradingView alerts (for order execution) |

## Project Structure

```
├── main.py                          # Entry point / orchestrator
├── config/
│   └── settings.py                  # Configuration (env vars)
├── src/
│   ├── models.py                    # Shared data models
│   ├── ingestion/                   # News source adapters
│   │   ├── finnhub_source.py        # Finnhub API
│   │   ├── alphavantage_source.py   # Alpha Vantage API
│   │   ├── fmp_source.py            # Financial Modeling Prep
│   │   └── rss_source.py            # Google News, MarketWatch, SEC RSS
│   ├── scoring/
│   │   └── scorer.py                # Sentiment scoring (pre-scored + LLM)
│   ├── signals/
│   │   └── aggregator.py            # Signal generation engine
│   └── server/
│       └── app.py                   # FastAPI webhook server
└── tradingview/
    ├── news_sentiment_indicator.pine # TradingView indicator
    ├── autoview_strategy.pine        # TradingView auto-trade strategy
    └── webhook_bridge.md             # Bridge setup guide
```
