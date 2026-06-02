"""
News Trading Signal Engine

Three monitors:
1. Political/Trump news → SPY signals
2. Jensen Huang / Nvidia ecosystem → per-ticker signals
3. Trump company callouts → per-ticker signals

Sources:
- Finnhub + Alpha Vantage → political keyword filter → SPY
- Google News political search → SPY
- Google News Jensen Huang search → NVDA + mentioned tickers
- Google News Trump company callouts → mentioned tickers
"""

import asyncio
import logging
import signal
import sys

import uvicorn

from config.settings import settings
from src.ingestion.google_news_political import create_google_political_sources
from src.ingestion.jensen_huang_source import create_jensen_sources
from src.ingestion.trump_companies_source import create_trump_company_sources
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

        # --- Political news → SPY ---
        for source_cls in [FinnhubSource, FinnhubSentimentSource, AlphaVantageSource]:
            source = source_cls()
            if source.is_configured:
                filtered = PoliticalFilter(source)
                self.sources.append(filtered)
                logger.info(f"Political: {source.name} → keyword filter → SPY")

        google_political = create_google_political_sources()
        self.sources.extend(google_political)
        logger.info(f"Political: {len(google_political)} Google News search feeds → SPY")

        # --- Jensen Huang / Nvidia ecosystem → per-ticker ---
        jensen_sources = create_jensen_sources()
        self.sources.extend(jensen_sources)
        logger.info(f"Jensen/NVDA: {len(jensen_sources)} Google News search feeds → multi-ticker")

        # --- Trump company callouts → per-ticker ---
        trump_co_sources = create_trump_company_sources()
        self.sources.extend(trump_co_sources)
        logger.info(f"Trump/Co: {len(trump_co_sources)} Google News search feeds → multi-ticker")

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
        if "jensen" in name or "trump_co" in name:
            return 60
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
        logger.info("NEWS TRADING SIGNAL ENGINE (Political + Jensen + Trump/Co)")
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
