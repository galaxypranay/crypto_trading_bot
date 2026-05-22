import logging
import asyncio
from datetime import datetime, timezone

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
    Full pipeline:
    1. Fetch latest news from RSS feeds
    2. Filter only new (unseen) articles
    3. Post each new article to Telegram news channel
    4. Analyze each article with AI for trade signal
    5. Pick best signal (highest confidence above threshold)
    6. Send best signal to admin trade bot for approval
    """
    logger.info("Pipeline triggered — fetching news...")

    try:
        all_news = await fetch_news_from_rss(config.NEWS_RSS_URLS)
    except Exception as e:
        error = f"News fetch failed: {e}"
        logger.error(error)
        await send_error_to_admin(error)
        await send_error_to_channel(error)
        return

    # Filter only new articles
    new_articles = [n for n in all_news if n["id"] not in seen_news_ids]

    if not new_articles:
        logger.info("No new articles found.")
        return

    logger.info(f"Found {len(new_articles)} new articles.")

    signals = []

    for article in new_articles:
        # Mark as seen immediately to prevent reprocessing
        seen_news_ids.add(article["id"])

        # Step 1: Post to news channel (every article)
        await post_news_to_channel(article)

        # Small delay to avoid Telegram rate limits
        await asyncio.sleep(1.5)

        # Step 2: Analyze with AI
        logger.info(f"Analyzing: {article['title'][:60]}")
        try:
            signal = await analyze_news(article)
            if signal:
                signals.append(signal)
                if signal.get("tradeable"):
                    logger.info(
                        f"Signal: {signal.get('coin')} {signal.get('direction')} "
                        f"@ {signal.get('confidence')}% confidence"
                    )
                else:
                    logger.info(f"Not tradeable: {signal.get('reason', 'unknown')}")
        except Exception as e:
            logger.error(f"AI analysis error for article '{article['title'][:40]}': {e}")

    # Step 3: Pick best signal above threshold
    best = pick_best_signal(signals)

    if best:
        logger.info(
            f"Best signal: {best['coin']} {best['direction']} "
            f"@ {best['confidence']}% — sending to admin..."
        )
        await send_signal_to_admin(best)
    else:
        logger.info(
            f"No signal met the minimum confidence threshold ({config.MIN_CONFIDENCE}%)."
        )

    # Clean old seen IDs to prevent memory leak (keep last 500)
    if len(seen_news_ids) > 1000:
        keep = list(seen_news_ids)[-500:]
        seen_news_ids.clear()
        seen_news_ids.update(keep)
