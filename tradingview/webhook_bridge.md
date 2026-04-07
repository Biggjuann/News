# TradingView Webhook Bridge Setup

## The Problem
TradingView Pine Script **cannot** make outbound HTTP requests. To get real-time news
sentiment signals into TradingView, you need a bridge.

## Solution Options (pick one)

### Option 1: Polling Bridge (Recommended — Free)
Use a small script that polls your signal API and sends alerts to TradingView via
a browser extension.

**How it works:**
```
Signal API (your server) → Polling Script → Browser Extension → TradingView Alert
```

**Tools:**
- **AutoView** (Chrome extension) — reads alert messages and executes on brokers
- **TradingConnector** — connects TradingView alerts to MT4/MT5
- **PineConnector** — similar, for MT4/MT5

### Option 2: Direct Webhook (TradingView → Your Server)
Set up a TradingView Alert that sends a webhook to your signal server.

1. In TradingView, add the News Sentiment indicator to your chart
2. Create an Alert → Condition: "News BUY Signal" or "News SELL Signal"
3. Set Webhook URL: `https://your-server.com/webhook/tradingview`
4. Add header: `x-webhook-secret: your-secret-here`

This lets TradingView **confirm** signals back to your server for order execution.

### Option 3: TradingView + Custom Data (Advanced)
Use TradingView's custom data feed or a partner data feed to push sentiment
directly into the chart.

**Steps:**
1. Deploy signal API publicly (ngrok, Railway, Fly.io, etc.)
2. Create a middleware that formats signals as TradingView-compatible JSON
3. Use the Pine Script `request.security()` or `input.source()` to reference the data

## Recommended Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    YOUR SERVER                                │
│                                                               │
│  ┌─────────────┐  ┌─────────┐  ┌──────────┐  ┌───────────┐ │
│  │ News Sources │→ │ Scorer  │→ │ Signals  │→ │ REST API  │ │
│  │ (Finnhub,   │  │ (Claude │  │ Engine   │  │ /signals  │ │
│  │  AV, FMP,   │  │  Haiku) │  │          │  │           │ │
│  │  RSS)        │  └─────────┘  └──────────┘  └─────┬─────┘ │
│  └─────────────┘                                     │       │
└──────────────────────────────────────────────────────│───────┘
                                                       │
                                              Poll every 15-30s
                                                       │
                                                       ▼
                                              ┌────────────────┐
                                              │  TradingView   │
                                              │  Alert Bridge  │
                                              │  (AutoView /   │
                                              │  custom script) │
                                              └────────┬───────┘
                                                       │
                                                       ▼
                                              ┌────────────────┐
                                              │  TradingView   │
                                              │  Chart Alert   │
                                              │  BUY / SELL    │
                                              └────────────────┘
```

## Quick Start with ngrok (for testing)

```bash
# Terminal 1: Start the signal server
python main.py

# Terminal 2: Expose it publicly
ngrok http 8080

# Use the ngrok URL in TradingView webhook alerts
```
