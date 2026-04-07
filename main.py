"""
News Trading Signal System — Main Orchestrator

Pipeline: Fetch → Score → Aggregate → Serve via API

Runs:
1. Async news fetchers on configurable intervals
2. Sentiment scoring (pre-scored + LLM fallback)
3. Signal aggregation with weighted scoring
4. FastAPI server exposing signals for TradingView
"""

import asyncio
import logging
import sys

import uvicorn

from config.settings import settings
from src.ingestion.finnhub_source import FinnhubSource, FinnhubSentimentSource
from src.ingestion.alphavantage_source import AlphaVantageSource
from src.ingestion.fmp_source import FMPSource
from src.ingestion.rss_source import create_rss_sources
from src.scoring.scorer import SentimentScorer
from src.signals.aggregator import SignalAggregator
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

        # Initialize all configured sources
        self.sources = []
        for source_cls in [FinnhubSource, FinnhubSentimentSource, AlphaVantageSource, FMPSource]:
            source = source_cls()
            if source.is_configured:
                self.sources.append(source)
                logger.info(f"Enabled source: {source.name}")
            else:
                logger.warning(f"Skipped source (no API key): {source.name}")

        # RSS sources are always available (free, no key needed)
        rss_sources = create_rss_sources()
        self.sources.extend(rss_sources)
        for s in rss_sources:
            logger.info(f"Enabled source: {s.name}")

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

        except Exception as e:
            logger.error(f"Error processing {source.name}: {e}")

    async def run_source_loop(self, source, interval: int):
        """Continuously fetch from a source at the given interval."""
        while True:
            await self.fetch_and_process(source)
            await asyncio.sleep(interval)

    def get_interval(self, source) -> int:
        """Get the polling interval for a source."""
        name = source.name.lower()
        if "finnhub" in name:
            return settings.finnhub_poll_interval
        elif "alpha_vantage" in name:
            return settings.alpha_vantage_poll_interval
        elif "fmp" in name:
            return settings.fmp_poll_interval
        else:
            return settings.rss_poll_interval

    async def start(self):
        """Start all source loops and the API server."""
        logger.info("=" * 60)
        logger.info("NEWS TRADING SIGNAL ENGINE STARTING")
        logger.info(f"Watching tickers: {settings.watch_tickers}")
        logger.info(f"Active sources: {[s.name for s in self.sources]}")
        logger.info(f"Buy threshold: {settings.buy_signal_threshold}")
        logger.info(f"Sell threshold: {settings.sell_signal_threshold}")
        logger.info(f"API server: http://{settings.host}:{settings.port}")
        logger.info("=" * 60)

        # Start source fetch loops
        tasks = []
        for source in self.sources:
            interval = self.get_interval(source)
            logger.info(f"Starting {source.name} loop (every {interval}s)")
            tasks.append(asyncio.create_task(self.run_source_loop(source, interval)))

        # Start API server
        config = uvicorn.Config(
            app,
            host=settings.host,
            port=settings.port,
            log_level="info",
        )
        server = uvicorn.Server(config)
        tasks.append(asyncio.create_task(server.serve()))

        await asyncio.gather(*tasks)


async def main():
    engine = NewsSignalEngine()
    await engine.start()


if __name__ == "__main__":
    asyncio.run(main())
