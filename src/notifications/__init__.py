"""
Discord webhook notifier for trading signals.

Sends rich embeds to a Discord channel when BUY/SELL signals fire.
"""

import asyncio
import logging
from datetime import datetime, timezone

import aiohttp

from src.models import TradingSignal, SignalDirection

logger = logging.getLogger(__name__)

# Colors for Discord embeds
COLOR_BUY = 0x00FF00   # green
COLOR_SELL = 0xFF0000   # red
COLOR_NEUTRAL = 0x808080  # gray

DIRECTION_EMOJI = {
    SignalDirection.BUY: "🟢 BUY",
    SignalDirection.SELL: "🔴 SELL",
    SignalDirection.NEUTRAL: "⚪ NEUTRAL",
}


class DiscordNotifier:
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    @property
    def is_configured(self) -> bool:
        return bool(self.webhook_url)

    async def send_signal(self, signal: TradingSignal):
        """Send a trading signal as a rich Discord embed."""
        if not self.is_configured:
            return

        direction = DIRECTION_EMOJI.get(signal.direction, signal.direction.value)

        if signal.direction == SignalDirection.BUY:
            color = COLOR_BUY
        elif signal.direction == SignalDirection.SELL:
            color = COLOR_SELL
        else:
            color = COLOR_NEUTRAL

        # Build headline list
        headlines = "\n".join(f"• {h}" for h in signal.headlines[:5])
        if not headlines:
            headlines = "No headlines available"

        embed = {
            "title": f"{direction}  —  {signal.ticker}",
            "color": color,
            "fields": [
                {
                    "name": "Sentiment Score",
                    "value": f"`{signal.sentiment_score:+.3f}`",
                    "inline": True,
                },
                {
                    "name": "Confidence",
                    "value": f"`{signal.confidence:.1%}`",
                    "inline": True,
                },
                {
                    "name": "Strength",
                    "value": f"`{signal.strength:.1%}`",
                    "inline": True,
                },
                {
                    "name": "Sources",
                    "value": f"`{signal.source_count} articles`",
                    "inline": True,
                },
                {
                    "name": "Headlines",
                    "value": headlines[:1024],  # Discord field limit
                    "inline": False,
                },
            ],
            "timestamp": signal.created_at.isoformat(),
            "footer": {
                "text": f"Expires: {signal.expires_at.strftime('%H:%M:%S UTC') if signal.expires_at else 'N/A'}",
            },
        }

        payload = {
            "username": "News Trading Signals",
            "embeds": [embed],
        }

        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.post(self.webhook_url, json=payload) as resp:
                    if resp.status == 204:
                        logger.info(f"Discord: sent {signal.direction.value} {signal.ticker}")
                    elif resp.status == 429:
                        # Rate limited — wait and retry once
                        data = await resp.json()
                        wait = data.get("retry_after", 1)
                        logger.warning(f"Discord rate limited, waiting {wait}s")
                        await asyncio.sleep(wait)
                        async with session.post(self.webhook_url, json=payload) as retry_resp:
                            if retry_resp.status == 204:
                                logger.info(f"Discord: sent {signal.direction.value} {signal.ticker} (retry)")
                    else:
                        text = await resp.text()
                        logger.error(f"Discord webhook failed ({resp.status}): {text[:200]}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Discord send error: {e}")

    async def send_startup(self, tickers: list[str], sources: list[str]):
        """Send a startup notification."""
        if not self.is_configured:
            return

        embed = {
            "title": "📡 News Trading Signal Engine Started",
            "color": 0x5865F2,  # Discord blurple
            "fields": [
                {
                    "name": "Watching",
                    "value": ", ".join(f"`{t}`" for t in tickers),
                    "inline": False,
                },
                {
                    "name": "Active Sources",
                    "value": ", ".join(f"`{s}`" for s in sources),
                    "inline": False,
                },
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        payload = {
            "username": "News Trading Signals",
            "embeds": [embed],
        }

        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.post(self.webhook_url, json=payload) as resp:
                    if resp.status == 204:
                        logger.info("Discord: sent startup notification")
        except Exception as e:
            logger.error(f"Discord startup notification error: {e}")
