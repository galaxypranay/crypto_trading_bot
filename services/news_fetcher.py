import httpx
import feedparser
import hashlib
from datetime import datetime, timezone
from typing import Optional
import logging
import config

logger = logging.getLogger(__name__)

# ── Supported tradeable coins ─────────────────────────────────
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
    "near": "NEAR",
    "stellar": "XLM", "xlm": "XLM",
    "atom": "ATOM", "cosmos": "ATOM",
    "uniswap": "UNI", "uni": "UNI",
    "render": "RNDR", "rndr": "RNDR",
}

# ── RSS feeds (backup + additional sources) ───────────────────
RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
    "https://cryptonews.com/news/feed/",
    "https://bitcoinist.com/feed/",
    "https://coinjournal.net/feed/",
    "https://www.theblock.co/rss.xml",
]

# Common browser headers — kuch feeds bot requests block karte hain
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}


def make_news_id(title: str, url: str) -> str:
    return hashlib.md5(f"{title}{url}".encode()).hexdigest()


def extract_coin(title: str, description: str = "") -> Optional[str]:
    text = (title + " " + description).lower()
    for name, ticker in TRADEABLE_COINS.items():
        if name in text:
            return ticker
    return None


def _clean_html(text: str) -> str:
    """Simple HTML tag remover for RSS summaries."""
    import re
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean[:500]


async def _fetch_coingecko_news() -> list[dict]:
    """
    CoinGecko /news API se latest crypto news fetch karo.
    Free tier mein bhi kaam karta hai (optional API key).
    """
    articles = []
    headers = {"accept": "application/json"}
    if config.COINGECKO_API_KEY:
        headers["x-cg-demo-api-key"] = config.COINGECKO_API_KEY

    url = "https://api.coingecko.com/api/v3/news"

    try:
        async with httpx.AsyncClient(timeout=15, headers=headers) as client:
            response = await client.get(url, params={"per_page": 50})
            if response.status_code != 200:
                logger.warning(f"CoinGecko news API: {response.status_code}")
                return []

            data = response.json()
            items = data if isinstance(data, list) else data.get("data", [])

            for item in items:
                title       = (item.get("title") or item.get("name") or "").strip()
                article_url = (item.get("url") or item.get("news_url") or "").strip()
                description = _clean_html(item.get("description") or item.get("text") or "")
                source      = item.get("news_site") or item.get("author") or "CoinGecko News"

                if not title or not article_url:
                    continue

                coin = extract_coin(title, description)
                if not coin:
                    continue

                # Timestamp parse karo
                ts = item.get("published_at") or item.get("date") or item.get("created_at")
                try:
                    if isinstance(ts, (int, float)):
                        pub_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                    elif isinstance(ts, str):
                        from dateutil import parser as dtparser
                        pub_dt = dtparser.parse(ts).replace(tzinfo=timezone.utc) \
                            if dtparser.parse(ts).tzinfo is None \
                            else dtparser.parse(ts).astimezone(timezone.utc)
                    else:
                        pub_dt = datetime.now(timezone.utc)
                except Exception:
                    pub_dt = datetime.now(timezone.utc)

                articles.append({
                    "id":           make_news_id(title, article_url),
                    "title":        title,
                    "url":          article_url,
                    "description":  description,
                    "source":       source,
                    "published_at": pub_dt,
                    "coin":         coin,
                    "from_source":  "coingecko",
                })

    except Exception as e:
        logger.error(f"CoinGecko news fetch error: {e}")

    logger.info(f"CoinGecko API: {len(articles)} coin-related articles")
    return articles


async def _fetch_rss_news() -> list[dict]:
    """RSS feeds se news fetch karo — CoinGecko ke liye backup."""
    articles = []

    async with httpx.AsyncClient(timeout=20, headers=REQUEST_HEADERS) as client:
        for feed_url in RSS_FEEDS:
            try:
                response = await client.get(feed_url)
                response.raise_for_status()
                feed   = feedparser.parse(response.text)
                source = feed.feed.get("title", "Crypto News")

                for entry in feed.entries[:20]:
                    title       = entry.get("title", "").strip()
                    url         = entry.get("link", "").strip()
                    description = _clean_html(entry.get("summary", ""))

                    if not title or not url:
                        continue

                    coin = extract_coin(title, description)
                    if not coin:
                        continue

                    try:
                        pub_dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                    except Exception:
                        pub_dt = datetime.now(timezone.utc)

                    articles.append({
                        "id":           make_news_id(title, url),
                        "title":        title,
                        "url":          url,
                        "description":  description,
                        "source":       source,
                        "published_at": pub_dt,
                        "coin":         coin,
                        "from_source":  "rss",
                    })

            except Exception as e:
                logger.error(f"RSS fetch error [{feed_url}]: {e}")

    logger.info(f"RSS feeds: {len(articles)} coin-related articles")
    return articles


async def fetch_coingecko_news() -> list[dict]:
    """
    CoinGecko API + RSS feeds dono se news fetch karo.
    Merge + deduplicate karke newest-first return karo.
    """
    cg_news  = await _fetch_coingecko_news()
    rss_news = await _fetch_rss_news()

    all_news = cg_news + rss_news

    # Deduplicate by ID, newest first
    seen   = set()
    unique = []
    for n in sorted(all_news, key=lambda x: x["published_at"], reverse=True):
        if n["id"] not in seen:
            seen.add(n["id"])
            unique.append(n)

    logger.info(f"Total unique coin-related articles: {len(unique)}")
    return unique
