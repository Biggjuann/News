"""
FastAPI webhook server for TradingView integration.

Endpoints:
- GET  /signals              — current active signals (polled by TradingView external data)
- GET  /signals/{ticker}     — signal for a specific ticker
- GET  /health               — health check
- POST /webhook/tradingview  — receive alerts FROM TradingView (for order execution)
"""

import logging
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config.settings import settings
from src.models import TradingSignal, SignalDirection

logger = logging.getLogger(__name__)

app = FastAPI(
    title="News Trading Signal API",
    description="Real-time news sentiment → trading signals for TradingView",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global reference to the signal aggregator — set by main.py at startup
_aggregator = None


def set_aggregator(aggregator):
    global _aggregator
    _aggregator = aggregator


# --- Response models ---

class SignalResponse(BaseModel):
    ticker: str
    direction: str
    strength: float
    sentiment_score: float
    confidence: float
    source_count: int
    headlines: list[str]
    created_at: str
    expires_at: str | None


class AllSignalsResponse(BaseModel):
    signals: list[SignalResponse]
    last_updated: str
    active_count: int


class TradingViewAlert(BaseModel):
    """Incoming alert from TradingView (for future order execution)."""
    ticker: str
    action: str  # "buy" or "sell"
    price: float | None = None
    quantity: float | None = None


# --- Endpoints ---

@app.get("/")
async def root():
    return {
        "name": "News Trading Signal API",
        "version": "1.0.0",
        "endpoints": {
            "/signals": "All active signals",
            "/signals/{ticker}": "Signal for a specific ticker",
            "/health": "Health check",
            "/docs": "Interactive API docs (Swagger UI)",
        },
    }


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/signals", response_model=AllSignalsResponse)
async def get_all_signals():
    """Get all active trading signals. TradingView polls this endpoint."""
    if _aggregator is None:
        raise HTTPException(status_code=503, detail="Signal engine not initialized")

    signals = []
    for ticker, sig in _aggregator.active_signals.items():
        signals.append(SignalResponse(
            ticker=sig.ticker,
            direction=sig.direction.value,
            strength=sig.strength,
            sentiment_score=sig.sentiment_score,
            confidence=sig.confidence,
            source_count=sig.source_count,
            headlines=sig.headlines,
            created_at=sig.created_at.isoformat(),
            expires_at=sig.expires_at.isoformat() if sig.expires_at else None,
        ))

    return AllSignalsResponse(
        signals=signals,
        last_updated=datetime.now(timezone.utc).isoformat(),
        active_count=len(signals),
    )


@app.get("/signals/{ticker}", response_model=SignalResponse)
async def get_signal(ticker: str):
    """Get signal for a specific ticker."""
    if _aggregator is None:
        raise HTTPException(status_code=503, detail="Signal engine not initialized")

    ticker = ticker.upper()
    sig = _aggregator.active_signals.get(ticker)
    if sig is None:
        raise HTTPException(status_code=404, detail=f"No active signal for {ticker}")

    return SignalResponse(
        ticker=sig.ticker,
        direction=sig.direction.value,
        strength=sig.strength,
        sentiment_score=sig.sentiment_score,
        confidence=sig.confidence,
        source_count=sig.source_count,
        headlines=sig.headlines,
        created_at=sig.created_at.isoformat(),
        expires_at=sig.expires_at.isoformat() if sig.expires_at else None,
    )


@app.post("/webhook/tradingview")
async def tradingview_webhook(
    alert: TradingViewAlert,
    x_webhook_secret: str = Header(default=""),
):
    """
    Receive webhook alerts FROM TradingView.
    This can be used to trigger order execution on a broker API.
    """
    if x_webhook_secret != settings.webhook_secret:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

    logger.info(f"TradingView alert received: {alert.action} {alert.ticker} @ {alert.price}")

    # TODO: integrate with broker API (Alpaca, IBKR, etc.) for order execution
    return {
        "status": "received",
        "action": alert.action,
        "ticker": alert.ticker,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
