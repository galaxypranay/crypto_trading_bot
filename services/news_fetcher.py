import httpx
import feedparser
import hashlib
from datetime import datetime, timezone
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# Tradeable coins only
TRADEABLE_COINS = {
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
    "pepe": "PEPE",
    "sui": "SUI",
    "aptos": "APT", "apt": "APT",
    "arbitrum": "ARB", "arb": "ARB",
    "optimism": "OP",
    "injective": "INJ", "inj": "INJ",
}

# Free RSS feeds — no API key needed
RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
    "https://cryptonews.com/news/feed/",
    "https://bitcoinist.com/feed/",
]


def make_news_id(title: str, url: str) -> str:
    return hashlib.md5(f"{title}{url}".encode()).hexdigest()


def extract_coin(title: str, description: str = "") -> Optional[str]:
    text = (title + " " + description).lower()
    for name, ticker in TRADEABLE_COINS.items():
        if name in text:
            return ticker
    return None


async def fetch_coingecko_news() -> list[dict]:
    """
    Fetch crypto news from free RSS feeds.
    Sirf tradeable coin se related news return hoti hai.
    """
    all_news = []

    async with httpx.AsyncClient(timeout=15) as client:
        for feed_url in RSS_FEEDS:
            try:
                response = await client.get(feed_url)
                feed = feedparser.parse(response.text)
                source = feed.feed.get("title", "Crypto News")

                for entry in feed.entries[:15]:
                    title       = entry.get("title", "").strip()
                    url         = entry.get("link", "").strip()
                    description = entry.get("summary", "").strip()[:400]

                    if not title or not url:
                        continue

                    coin = extract_coin(title, description)
                    if not coin:
                        continue

                    try:
                        pub_dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                    except Exception:
                        pub_dt = datetime.now(timezone.utc)

                    all_news.append({
                        "id":           make_news_id(title, url),
                        "title":        title,
                        "url":          url,
                        "description":  description,
                        "source":       source,
                        "published_at": pub_dt,
                        "coin":         coin,
                    })

            except Exception as e:
                logger.error(f"RSS fetch error [{feed_url}]: {e}")

    # Newest first, deduplicate by id
    seen = set()
    unique = []
    for n in sorted(all_news, key=lambda x: x["published_at"], reverse=True):
        if n["id"] not in seen:
            seen.add(n["id"])
            unique.append(n)

    logger.info(f"Fetched {len(unique)} unique coin-related articles from RSS")
    return unique
