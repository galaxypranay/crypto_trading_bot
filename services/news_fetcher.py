import feedparser
import httpx
import hashlib
from datetime import datetime, timezone
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# Crypto keywords to filter relevant news only
CRYPTO_KEYWORDS = [
    "bitcoin", "btc", "ethereum", "eth", "crypto", "blockchain",
    "altcoin", "defi", "nft", "exchange", "binance", "coinbase",
    "solana", "sol", "xrp", "ripple", "usdt", "stablecoin",
    "whale", "etf", "sec", "regulation", "hack", "listing",
    "delisting", "airdrop", "token", "web3", "layer2", "l2",
]

# Coin name → ticker map for signal generation
COIN_TICKER_MAP = {
    "bitcoin": "BTC", "btc": "BTC",
    "ethereum": "ETH", "eth": "ETH",
    "solana": "SOL", "sol": "SOL",
    "ripple": "XRP", "xrp": "XRP",
    "cardano": "ADA", "ada": "ADA",
    "dogecoin": "DOGE", "doge": "DOGE",
    "binance": "BNB", "bnb": "BNB",
    "avalanche": "AVAX", "avax": "AVAX",
    "polkadot": "DOT", "dot": "DOT",
    "chainlink": "LINK", "link": "LINK",
    "polygon": "MATIC", "matic": "MATIC",
    "shiba": "SHIB", "shib": "SHIB",
    "litecoin": "LTC", "ltc": "LTC",
    "tron": "TRX", "trx": "TRX",
}


def make_news_id(title: str, url: str) -> str:
    """Unique ID for deduplication."""
    raw = f"{title}{url}"
    return hashlib.md5(raw.encode()).hexdigest()


def is_crypto_relevant(title: str, summary: str = "") -> bool:
    """Check if news is about crypto."""
    text = (title + " " + summary).lower()
    return any(kw in text for kw in CRYPTO_KEYWORDS)


def extract_coin(title: str, summary: str = "") -> Optional[str]:
    """Try to extract the main coin ticker from text."""
    text = (title + " " + summary).lower()
    for name, ticker in COIN_TICKER_MAP.items():
        if name in text:
            return ticker
    return None


async def fetch_news_from_rss(rss_urls: list[str]) -> list[dict]:
    """Fetch and parse all RSS feeds, return unified news list."""
    all_news = []

    async with httpx.AsyncClient(timeout=15) as client:
        for url in rss_urls:
            try:
                response = await client.get(url)
                feed = feedparser.parse(response.text)

                for entry in feed.entries[:10]:
                    title = entry.get("title", "").strip()
                    link = entry.get("link", "").strip()
                    summary = entry.get("summary", "").strip()
                    source = feed.feed.get("title", "Unknown")

                    # Parse published date
                    published_raw = entry.get("published", "")
                    try:
                        published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                    except Exception:
                        published = datetime.now(timezone.utc)

                    if not title or not link:
                        continue

                    if not is_crypto_relevant(title, summary):
                        continue

                    all_news.append({
                        "id": make_news_id(title, link),
                        "title": title,
                        "url": link,
                        "summary": summary[:300] if summary else "",
                        "source": source,
                        "published_at": published,
                        "coin": extract_coin(title, summary),
                    })

            except Exception as e:
                logger.error(f"RSS fetch error [{url}]: {e}")

    # Sort newest first
    all_news.sort(key=lambda x: x["published_at"], reverse=True)
    return all_news
