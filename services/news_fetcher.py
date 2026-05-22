import httpx
import hashlib
from datetime import datetime, timezone
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# Tradeable coin tickers — sirf inse related news fetch hogi
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

# CoinGecko free news API
COINGECKO_NEWS_URL = "https://api.coingecko.com/api/v3/news"


def make_news_id(title: str, url: str) -> str:
    """Unique ID for deduplication."""
    return hashlib.md5(f"{title}{url}".encode()).hexdigest()


def extract_coin(title: str, description: str = "") -> Optional[str]:
    """Extract the main tradeable coin ticker from news text."""
    text = (title + " " + description).lower()
    for name, ticker in TRADEABLE_COINS.items():
        if name in text:
            return ticker
    return None


async def fetch_coingecko_news() -> list[dict]:
    """
    Fetch latest news from CoinGecko free API.
    Sirf wo news jo kisi tradeable coin se related ho.
    """
    all_news = []

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                COINGECKO_NEWS_URL,
                params={"per_page": 20},
                headers={"accept": "application/json"},
            )
            response.raise_for_status()
            data = response.json()

        # CoinGecko returns a list directly or under a key
        articles = data if isinstance(data, list) else data.get("data", [])

        for item in articles:
            title       = item.get("title", "").strip()
            url         = item.get("url", "").strip()
            description = item.get("description", item.get("author", "")).strip()
            source      = item.get("news_site", item.get("source", "CoinGecko"))
            published   = item.get("published_at", item.get("created_at", ""))
            thumb       = item.get("thumb_2x", item.get("image_url", ""))

            if not title or not url:
                continue

            # Sirf tradeable coin se related news
            coin = extract_coin(title, description)
            if not coin:
                continue

            # Parse timestamp
            try:
                pub_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
            except Exception:
                pub_dt = datetime.now(timezone.utc)

            all_news.append({
                "id":           make_news_id(title, url),
                "title":        title,
                "url":          url,
                "description":  description[:400] if description else "",
                "source":       source,
                "published_at": pub_dt,
                "coin":         coin,
                "thumb":        thumb,
            })

    except Exception as e:
        logger.error(f"CoinGecko news fetch error: {e}")

    # Newest first
    all_news.sort(key=lambda x: x["published_at"], reverse=True)
    logger.info(f"CoinGecko: fetched {len(all_news)} coin-related articles")
    return all_news
