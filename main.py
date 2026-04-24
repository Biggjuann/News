"""
Trump/White House → SPY Signal Engine

Pipeline: Political news → LLM scoring → Signal → Discord alerts

Sources (layered for reliability):
1. Direct: Truth Social API, White House RSS (may be blocked by some hosts)
2. Filtered: Finnhub + Alpha Vantage general news, filtered for political keywords
3. Search: Google News RSS with political search queries
"""

import asyncio
import logging
import signal
import sys

import uvicorn

from config.settings import settings
from src.ingestion.truth_social_api import TruthSocialAPISource
from src.ingestion.truth_social_source import TruthSocialSource
from src.ingestion.x_source import XSource
from src.ingestion.whitehouse_source import create_whitehouse_sources
from src.ingestion.google_news_political import create_google_political_sources
from src.ingestion.finnhub_source import FinnhubSource, FinnhubSentimentSource
from src.ingestion.alphavantage_source import AlphaVantageSource
from src.ingestion.political_filter import PoliticalFilter
from src.scoring.scorer import SentimentScorer
from src.signals.aggregator import SignalAggregator
from src.notifications import DiscordNotifier
from src.server.app import app, set_aggregator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


class NewsSignalEngine:
    """Orchestrates the fetch → score → signal pipeline."""

    def __init__(self):
        self.scorer = SentimentScorer()
        self.aggregator = SignalAggregator()
        self.discord = DiscordNotifier(settings.discord_webhook_url)
        self._shutdown = False

        if self.discord.is_configured:
            logger.info("Discord notifications: ENABLED")
        else:
            logger.warning("Discord notifications: DISABLED (no webhook URL)")

        self.sources = []

        # --- Layer 1: Direct sources (may be blocked on some hosts) ---
        self.sources.append(TruthSocialAPISource())
        self.sources.append(TruthSocialSource())
        self.sources.append(XSource())
        wh_sources = create_whitehouse_sources()
        self.sources.extend(wh_sources)
        logger.info("Layer 1 (direct): Truth Social, X, White House feeds")

        # --- Layer 2: Reliable news APIs filtered for political keywords ---
        for source_cls in [FinnhubSource, FinnhubSentimentSource, AlphaVantageSource]:
            source = source_cls()
            if source.is_configured:
                filtered = PoliticalFilter(source)
                self.sources.append(filtered)
                logger.info(f"Layer 2 (filtered): {source.name} → political filter")

        # --- Layer 3: Google News political search (always free) ---
        google_sources = create_google_political_sources()
        self.sources.extend(google_sources)
        logger.info(f"Layer 3 (search): {len(google_sources)} Google News political feeds")

        set_aggregator(self.aggregator)

    async def fetch_and_process(self, source):
        """Fetch from a single source, score, and aggregate."""
        try:
            articles = await source.fetch(settings.watch_tickers)
            if not articles:
                return

            logger.info(f"{source.name}: fetched {len(articles)} new articles")

            scored = await self.scorer.score_batch(articles)
            self.aggregator.ingest(scored)
            new_signals = self.aggregator.generate_signals()

            for sig in new_signals:
                logger.info(
                    f"NEW SIGNAL: {sig.direction.value} {sig.ticker} "
                    f"strength={sig.strength:.2f} confidence={sig.confidence:.2f}"
                )
                await self.discord.send_signal(sig)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Error processing {source.name}: {e}")

    async def run_source_loop(self, source, interval: int):
        """Continuously fetch from a source at the given interval."""
        try:
            while not self._shutdown:
                await self.fetch_and_process(source)
                try:
                    await asyncio.sleep(interval)
                except asyncio.CancelledError:
                    break
        except asyncio.CancelledError:
            pass
        finally:
            logger.info(f"Source loop stopped: {source.name}")

    def get_interval(self, source) -> int:
        """Get the polling interval for a source."""
        name = source.name.lower()
        if "truth" in name:
            return settings.truth_social_poll_interval
        elif "x_" in name or "twitter" in name:
            return settings.x_poll_interval
        elif "google" in name:
            return 60
        elif "political_finnhub" in name:
            return settings.finnhub_poll_interval
        elif "political_alpha" in name:
            return settings.alpha_vantage_poll_interval
        else:
            return settings.whitehouse_poll_interval

    async def start(self):
        """Start all source loops and the API server."""
        from config.settings import ENV_FILE
        logger.info(f"Loading .env from: {ENV_FILE} (exists: {ENV_FILE.exists()})")
        for name in ["finnhub_api_key", "alpha_vantage_api_key", "anthropic_api_key", "discord_webhook_url"]:
            val = getattr(settings, name, "")
            status = f"{val[:4]}***{val[-4:]}" if len(val) > 8 else ("SET" if val else "NOT SET")
            logger.info(f"  {name}: {status}")

        logger.info("=" * 60)
        logger.info("TRUMP/WHITE HOUSE → SPY SIGNAL ENGINE")
        logger.info(f"Watching: {settings.watch_tickers}")
        logger.info(f"Sources: {len(self.sources)} total")
        logger.info(f"Buy threshold: {settings.buy_signal_threshold}")
        logger.info(f"Sell threshold: {settings.sell_signal_threshold}")
        logger.info(f"API server: http://{settings.host}:{settings.port}")
        logger.info("=" * 60)

        await self.discord.send_startup(
            settings.watch_tickers,
            [s.name for s in self.sources],
        )

        tasks = []
        for source in self.sources:
            interval = self.get_interval(source)
            logger.info(f"Starting {source.name} (every {interval}s)")
            tasks.append(asyncio.create_task(
                self.run_source_loop(source, interval),
                name=f"source_{source.name}",
            ))

        config = uvicorn.Config(
            app,
            host=settings.host,
            port=settings.port,
            log_level="info",
        )
        server = uvicorn.Server(config)

        loop = asyncio.get_event_loop()
        for sig_name in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig_name, lambda: asyncio.create_task(self.shutdown(tasks, server)))

        tasks.append(asyncio.create_task(server.serve(), name="uvicorn"))
        await asyncio.gather(*tasks, return_exceptions=True)

    async def shutdown(self, tasks, server):
        logger.info("Shutting down...")
        self._shutdown = True
        server.should_exit = True
        for task in tasks:
            if not task.done():
                task.cancel()


async def main():
    engine = NewsSignalEngine()
    await engine.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped by user.")
