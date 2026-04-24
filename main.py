"""
Trump/White House → SPY Signal Engine

Pipeline: Trump posts + WH briefings → LLM scoring → Signal → Discord alerts

Sources:
- Trump Truth Social posts
- Trump/POTUS/WhiteHouse X posts
- White House official briefings and statements
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

        # Initialize political news sources
        self.sources = []

        # Trump Truth Social — direct API (primary) + RSS bridge (backup)
        self.sources.append(TruthSocialAPISource())
        logger.info("Enabled source: Truth Social API (@realDonaldTrump, direct)")

        self.sources.append(TruthSocialSource())
        logger.info("Enabled source: Truth Social RSS (@realDonaldTrump, backup)")

        # Trump / POTUS / WhiteHouse X accounts
        self.sources.append(XSource())
        logger.info("Enabled source: X (@realDonaldTrump, @POTUS, @WhiteHouse)")

        # White House official feeds
        wh_sources = create_whitehouse_sources()
        self.sources.extend(wh_sources)
        for s in wh_sources:
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
                # Push to Discord
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
        else:
            return settings.whitehouse_poll_interval

    async def start(self):
        """Start all source loops and the API server."""
        # Log key loading status (masked)
        from config.settings import ENV_FILE
        logger.info(f"Loading .env from: {ENV_FILE} (exists: {ENV_FILE.exists()})")
        for name in ["anthropic_api_key", "discord_webhook_url"]:
            val = getattr(settings, name, "")
            status = f"{val[:4]}***{val[-4:]}" if len(val) > 8 else ("SET" if val else "NOT SET")
            logger.info(f"  {name}: {status}")

        logger.info("=" * 60)
        logger.info("NEWS TRADING SIGNAL ENGINE STARTING")
        logger.info(f"Watching tickers: {settings.watch_tickers}")
        logger.info(f"Active sources: {[s.name for s in self.sources]}")
        logger.info(f"Buy threshold: {settings.buy_signal_threshold}")
        logger.info(f"Sell threshold: {settings.sell_signal_threshold}")
        logger.info(f"API server: http://{settings.host}:{settings.port}")
        logger.info("=" * 60)

        # Send Discord startup notification
        await self.discord.send_startup(
            settings.watch_tickers,
            [s.name for s in self.sources],
        )

        # Start source fetch loops
        tasks = []
        for source in self.sources:
            interval = self.get_interval(source)
            logger.info(f"Starting {source.name} loop (every {interval}s)")
            tasks.append(asyncio.create_task(
                self.run_source_loop(source, interval),
                name=f"source_{source.name}",
            ))

        # Start API server
        config = uvicorn.Config(
            app,
            host=settings.host,
            port=settings.port,
            log_level="info",
        )
        server = uvicorn.Server(config)

        # Handle shutdown gracefully
        loop = asyncio.get_event_loop()
        for sig_name in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig_name, lambda: asyncio.create_task(self.shutdown(tasks, server)))

        tasks.append(asyncio.create_task(server.serve(), name="uvicorn"))

        await asyncio.gather(*tasks, return_exceptions=True)

    async def shutdown(self, tasks, server):
        """Gracefully shut down all tasks."""
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
