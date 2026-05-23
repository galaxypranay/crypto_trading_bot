import httpx
import feedparser
import hashlib
from datetime import datetime, timezone
from typing import Optional
import logging
import config

logger = logging.getLogger(__name__)

# ── Sirf early.bulk.trade pe available coins ─────────────────
TRADEABLE_COINS = {
    "bitcoin": "BTC",   "btc": "BTC",
    "ethereum": "ETH",  "eth": "ETH",
    "solana": "SOL",    "sol": "SOL",
    "ripple": "XRP",    "xrp": "XRP",
    "sui": "SUI",
    "binance": "BNB",   "bnb": "BNB",
    "zcash": "ZEC",     "zec": "ZEC",
    "dogecoin": "DOGE", "doge": "DOGE",
    "fartcoin": "FARTCOIN",
}

RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
    "https://cryptonews.com/news/feed/",
    "https://bitcoinist.com/feed/",
    "https://coinjournal.net/feed/",
    "https://www.theblock.co/rss.xml",
]

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
    import re
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean[:500]


async def _fetch_coingecko_news() -> list[dict]:
    articles = []
    headers  = {"accept": "application/json"}
    if config.COINGECKO_API_KEY:
        headers["x-cg-demo-api-key"] = config.COINGECKO_API_KEY

    try:
        async with httpx.AsyncClient(timeout=15, headers=headers) as client:
            resp = await client.get(
                "https://api.coingecko.com/api/v3/news",
                params={"per_page": 50},
            )
            if resp.status_code != 200:
                logger.warning(f"CoinGecko news API: {resp.status_code}")
                return []

            data  = resp.json()
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

                ts = item.get("published_at") or item.get("date") or item.get("created_at")
                try:
                    if isinstance(ts, (int, float)):
                        pub_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                    elif isinstance(ts, str):
                        from dateutil import parser as dtparser
                        parsed = dtparser.parse(ts)
                        pub_dt = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None \
                            else parsed.astimezone(timezone.utc)
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
    articles = []

    async with httpx.AsyncClient(timeout=20, headers=REQUEST_HEADERS) as client:
        for feed_url in RSS_FEEDS:
            try:
                resp   = await client.get(feed_url)
                resp.raise_for_status()
                feed   = feedparser.parse(resp.text)
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
    """CoinGecko + RSS dono se news, merge + deduplicate, newest first."""
    cg_news  = await _fetch_coingecko_news()
    rss_news = await _fetch_rss_news()
    all_news = cg_news + rss_news

    seen   = set()
    unique = []
    for n in sorted(all_news, key=lambda x: x["published_at"], reverse=True):
        if n["id"] not in seen:
            seen.add(n["id"])
            unique.append(n)

    logger.info(f"Total unique coin-related articles: {len(unique)}")
    return unique
