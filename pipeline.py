import logging
import asyncio

from services.news_fetcher import fetch_news_from_rss
from services.ai_analyzer import analyze_news, pick_best_signal
from handlers.news_bot import post_news_to_channel, send_error_to_channel
from handlers.trade_bot import send_signal_to_admin, send_error_to_admin
import config

logger = logging.getLogger(__name__)

# Track already-processed news IDs to avoid duplicates
seen_news_ids: set[str] = set()


async def run_pipeline():
    """
    Updated pipeline — AI filter pehle, channel post baad mein:
    1. Fetch news from RSS feeds
    2. Filter only new (unseen) articles
    3. AI analyze karo — sirf tradeable news aage jaaye
    4. Tradeable news → channel mein post karo
    5. Best signal (highest confidence) → admin trade bot ko bhejo
    """
    logger.info("Pipeline triggered — fetching news...")

    try:
        all_news = await fetch_news_from_rss(config.NEWS_RSS_URLS)
    except Exception as e:
        error = f"News fetch failed: {e}"
        logger.error(error)
        await send_error_to_admin(error)
        return

    # Sirf naye articles
    new_articles = [n for n in all_news if n["id"] not in seen_news_ids]

    if not new_articles:
        logger.info("No new articles found.")
        return

    logger.info(f"Found {len(new_articles)} new articles — sending to AI filter...")

    signals = []

    for article in new_articles:
        # Pehle seen mark karo — reprocessing rokne ke liye
        seen_news_ids.add(article["id"])

        logger.info(f"AI analyzing: {article['title'][:60]}")

        try:
            signal = await analyze_news(article)
        except Exception as e:
            logger.error(f"AI error for '{article['title'][:40]}': {e}")
            continue

        if not signal:
            logger.info("AI returned no signal — skipping.")
            continue

        if not signal.get("tradeable"):
            # News kaam ka nahi — channel mein nahi jayega
            logger.info(f"Not tradeable → ignored: {signal.get('reason', 'no reason')}")
            continue

        # ✅ AI ne approve kiya — channel mein post karo
        logger.info(
            f"Tradeable! {signal.get('coin')} {signal.get('direction')} "
            f"@ {signal.get('confidence')}% — posting to channel..."
        )
        await post_news_to_channel(article)
        signals.append(signal)

        # Telegram rate limit se bachne ke liye
        await asyncio.sleep(1.5)

    # Best signal admin ko bhejo
    best = pick_best_signal(signals)

    if best:
        logger.info(
            f"Best signal: {best['coin']} {best['direction']} "
            f"@ {best['confidence']}% — sending to admin..."
        )
        await send_signal_to_admin(best)
    else:
        logger.info(
            f"No signal met the confidence threshold ({config.MIN_CONFIDENCE}%)."
        )

    # Memory leak se bachao — purane IDs hata do
    if len(seen_news_ids) > 1000:
        keep = list(seen_news_ids)[-500:]
        seen_news_ids.clear()
        seen_news_ids.update(keep)
