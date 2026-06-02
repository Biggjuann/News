"""
Trump company callout monitor.

Tracks when Trump mentions specific companies in posts, interviews,
or rallies. His praise or criticism of individual companies regularly
moves their stock prices significantly.
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

TRUMP_COMPANY_QUERIES = {
    "trump_company_callout": "Trump+calls+out+OR+slams+OR+praises+OR+threatens+company+OR+stock",
    "trump_tariff_company": "Trump+tariff+OR+ban+OR+sanction+company+OR+Apple+OR+Google+OR+Amazon",
    "trump_deal": "Trump+deal+OR+announces+OR+investment+OR+factory+OR+jobs+company",
    "trump_attacks": "Trump+attacks+OR+criticizes+OR+warns+OR+threatens+company+OR+CEO",
    "trump_truth_company": "Trump+Truth+Social+company+OR+stock+OR+market+OR+billion",
}

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"

TRUMP_TARGET_TICKERS = {
    "apple": "AAPL",
    "google": "GOOGL", "alphabet": "GOOGL",
    "amazon": "AMZN",
    "meta": "META", "facebook": "META", "zuckerberg": "META",
    "microsoft": "MSFT",
    "nvidia": "NVDA", "jensen": "NVDA",
    "tesla": "TSLA", "elon": "TSLA", "musk": "TSLA",
    "twitter": "TSLA",
    "tiktok": "SNAP",
    "bytedance": "META",
    "truth social": "DJT", "tmtg": "DJT",
    "disney": "DIS",
    "netflix": "NFLX",
    "comcast": "CMCSA", "nbc": "CMCSA", "msnbc": "CMCSA",
    "fox": "FOX", "fox news": "FOXA",
    "cnn": "WBD", "warner": "WBD",
    "lockheed": "LMT", "lockheed martin": "LMT",
    "boeing": "BA",
    "raytheon": "RTX",
    "northrop": "NOC",
    "general dynamics": "GD",
    "ford": "F",
    "general motors": "GM",
    "john deere": "DE", "deere": "DE",
    "caterpillar": "CAT",
    "harley": "HOG", "harley-davidson": "HOG",
    "pfizer": "PFE",
    "moderna": "MRNA",
    "johnson & johnson": "JNJ", "j&j": "JNJ",
    "unitedhealth": "UNH",
    "cvs": "CVS",
    "jpmorgan": "JPM", "jp morgan": "JPM", "jamie dimon": "JPM",
    "goldman sachs": "GS", "goldman": "GS",
    "blackrock": "BLK",
    "bank of america": "BAC",
    "wells fargo": "WFC",
    "exxon": "XOM", "exxonmobil": "XOM",
    "chevron": "CVX",
    "bp": "BP",
    "shell": "SHEL",
    "walmart": "WMT",
    "target": "TGT",
    "costco": "COST",
    "nike": "NKE",
    "coca-cola": "KO", "coke": "KO",
    "pepsi": "PEP", "pepsico": "PEP",
    "mcdonald": "MCD", "mcdonalds": "MCD",
    "starbucks": "SBUX",
    "alibaba": "BABA",
    "tencent": "TCEHY",
    "baidu": "BIDU",
    "nio": "NIO",
    "byd": "BYDDY",
    "tsmc": "TSM", "taiwan semiconductor": "TSM",
    "intel": "INTC",
    "amd": "AMD",
    "qualcomm": "QCOM",
    "broadcom": "AVGO",
    "anheuser": "BUD", "bud light": "BUD",
    "tractor supply": "TSCO",
    "blackstone": "BX",
    "softbank": "SFTBY",
    "stock market": "SPY", "s&p": "SPY", "market": "SPY",
    "dow": "DIA",
    "nasdaq": "QQQ",
}


def _extract_tickers(text: str) -> list[str]:
    text_lower = text.lower()
    found = set()
    for company, ticker in TRUMP_TARGET_TICKERS.items():
        if company in text_lower:
            found.add(ticker)
    if not found:
        found.add("SPY")
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


class TrumpCompanySource(NewsSource):
    def __init__(self, query_name: str, query: str):
        super().__init__(f"trump_co_{query_name}")
        self.feed_url = GOOGLE_NEWS_RSS.format(query=query)

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

                full_text = f"{headline} {summary}"
                mentioned_tickers = _extract_tickers(full_text)

                source_tag = f"[{news_source}] " if news_source else ""

                articles.append(NewsArticle(
                    source=self.name,
                    headline=f"[Trump/Co] {source_tag}{headline}",
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


def create_trump_company_sources() -> list[TrumpCompanySource]:
    return [
        TrumpCompanySource(name, query)
        for name, query in TRUMP_COMPANY_QUERIES.items()
    ]
