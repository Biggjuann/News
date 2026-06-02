"""
Jensen Huang / Nvidia CEO news monitor.

Searches for mentions of Jensen Huang in news, interviews, and conference
coverage, then extracts company names he calls out and scores the sentiment
for each mentioned company.
"""

import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import aiohttp

from src.models import NewsArticle
from src.ingestion.base import NewsSource

logger = logging.getLogger(__name__)

JENSEN_QUERIES = {
    "jensen_conference": "Jensen+Huang+conference+OR+keynote+OR+GTC+OR+Computex",
    "jensen_interview": "Jensen+Huang+interview+OR+says+OR+announces+OR+partnership",
    "jensen_companies": "Jensen+Huang+company+OR+deal+OR+partner+OR+customer+OR+investment",
    "nvidia_partnerships": "Nvidia+partnership+OR+collaboration+OR+deal+OR+customer+OR+announces",
}

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"

NVIDIA_ECOSYSTEM_TICKERS = {
    "nvidia": "NVDA", "nvda": "NVDA",
    "tsmc": "TSM", "taiwan semiconductor": "TSM",
    "microsoft": "MSFT", "azure": "MSFT",
    "google": "GOOGL", "alphabet": "GOOGL", "gcp": "GOOGL",
    "amazon": "AMZN", "aws": "AMZN",
    "meta": "META", "facebook": "META",
    "tesla": "TSLA",
    "apple": "AAPL",
    "amd": "AMD", "advanced micro": "AMD",
    "intel": "INTC",
    "broadcom": "AVGO",
    "qualcomm": "QCOM",
    "arm": "ARM", "arm holdings": "ARM",
    "super micro": "SMCI", "supermicro": "SMCI",
    "dell": "DELL", "dell technologies": "DELL",
    "hewlett": "HPE", "hpe": "HPE",
    "oracle": "ORCL",
    "salesforce": "CRM",
    "servicenow": "NOW",
    "snowflake": "SNOW",
    "palantir": "PLTR",
    "crowdstrike": "CRWD",
    "marvell": "MRVL",
    "micron": "MU",
    "samsung": "SSNLF",
    "asml": "ASML",
    "synopsys": "SNPS",
    "cadence": "CDNS",
    "arista": "ANET",
    "vertiv": "VRT",
    "eaton": "ETN",
    "softbank": "SFTBY",
    "coreweave": "NVDA",
    "databricks": "NVDA",
}


def _extract_tickers(text: str) -> list[str]:
    text_lower = text.lower()
    found = set()
    for company, ticker in NVIDIA_ECOSYSTEM_TICKERS.items():
        if company in text_lower:
            found.add(ticker)
    if not found:
        found.add("NVDA")
    return sorted(found)


def _parse_rss(content: str) -> list[dict]:
    entries = []
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return entries
    for item in root.iter("item"):
        entries.append({
            "title": (item.findtext("title") or "").strip(),
            "description": (item.findtext("description") or "").strip(),
            "link": (item.findtext("link") or "").strip(),
            "published": (item.findtext("pubDate") or "").strip(),
            "source": (item.findtext("source") or "").strip(),
        })
    return entries[:15]


def _parse_date(date_str: str) -> datetime:
    if not date_str:
        return datetime.now(timezone.utc)
    try:
        return parsedate_to_datetime(date_str)
    except Exception:
        return datetime.now(timezone.utc)


def _strip_html(text: str) -> str:
    clean = re.sub(r'<[^>]+>', '', text)
    clean = clean.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    clean = clean.replace('&#39;', "'").replace('&quot;', '"')
    return clean.strip()


class JensenHuangSource(NewsSource):
    def __init__(self, query_name: str, query: str):
        super().__init__(f"jensen_{query_name}")
        self.feed_url = GOOGLE_NEWS_RSS.format(query=query)
        self._shared_seen: set[str] | None = None

    async def fetch(self, tickers: list[str]) -> list[NewsArticle]:
        articles = []
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
                async with session.get(
                    self.feed_url,
                    headers={"User-Agent": "NewsTradingBot/1.0"},
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"{self.name}: HTTP {resp.status}")
                        return []
                    content = await resp.text()

            entries = _parse_rss(content)
            for entry in entries:
                headline = _strip_html(entry["title"])
                summary = _strip_html(entry["description"])
                news_source = entry.get("source", "")

                if not headline:
                    continue

                # Cross-feed dedup: skip if another Jensen feed already saw this headline
                headline_key = headline.lower().strip()
                if self._shared_seen is not None:
                    if headline_key in self._shared_seen:
                        continue
                    self._shared_seen.add(headline_key)

                clean_headline = headline
                if news_source and headline.endswith(f" - {news_source}"):
                    clean_headline = headline[:-(len(news_source) + 3)]

                full_text = f"{clean_headline} {summary}"
                mentioned_tickers = _extract_tickers(full_text)

                source_tag = f"[{news_source}] " if news_source else ""

                articles.append(NewsArticle(
                    source=self.name,
                    headline=f"[Jensen/NVDA] {source_tag}{clean_headline}",
                    summary=summary[:500],
                    url=entry["link"],
                    tickers=mentioned_tickers,
                    published_at=_parse_date(entry["published"]),
                ))

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"{self.name} fetch error: {e}")

        return self._deduplicate(articles)


# Shared headline dedup across all Jensen feeds so the same article
# from multiple search queries doesn't count as multiple sources.
_shared_seen_headlines: set[str] = set()


def create_jensen_sources() -> list[JensenHuangSource]:
    _shared_seen_headlines.clear()
    sources = []
    for name, query in JENSEN_QUERIES.items():
        s = JensenHuangSource(name, query)
        s._shared_seen = _shared_seen_headlines
        sources.append(s)
    return sources
