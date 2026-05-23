import logging
import asyncio

from services.news_fetcher import fetch_coingecko_news
from services.ai_analyzer import generate_description, analyze_news, pick_best_signal
from services.database import is_seen, mark_seen
from handlers.news_bot import post_news_to_channel, send_error_to_channel
from handlers.trade_bot import send_signal_to_admin, send_error_to_admin
import config

logger = logging.getLogger(__name__)


async def run_pipeline():
    """
    Pipeline:
    1. RSS feeds se coin-related news fetch karo
    2. Postgres se check karo — sirf naye articles
    3. FreeModel se description generate karo → channel mein post karo
    4. FreeModel se trade signal analyze karo
    5. Best signal → admin trade bot ko bhejo
    """
    logger.info("Pipeline triggered — fetching news...")

    try:
        all_news = await fetch_coingecko_news()
    except Exception as e:
        error = f"News fetch failed: {e}"
        logger.error(error)
        await send_error_to_admin(error)
        return

    # Sirf naye articles — DB se check
    new_articles = []
    for article in all_news:
        if not await is_seen(article["id"]):
            new_articles.append(article)

    if not new_articles:
        logger.info("No new articles.")
        return

    logger.info(f"{len(new_articles)} new articles found.")

    signals = []

    for article in new_articles:
        # Turant seen mark karo
        await mark_seen(article["id"])

        logger.info(f"Processing [{article['coin']}]: {article['title'][:55]}")

        # Step 1: AI description generate karo
        try:
            description = await generate_description(article)
        except Exception as e:
            logger.error(f"Description error: {e}")
            description = article.get("description", article["title"])

        # Step 2: Channel mein post karo
        await post_news_to_channel(article, description)
        await asyncio.sleep(1.5)

        # Step 3: Trade signal analyze karo
        try:
            signal = await analyze_news(article)
        except Exception as e:
            logger.error(f"Signal analysis error: {e}")
            continue

        if not signal:
            continue

        if signal.get("tradeable"):
            logger.info(
                f"Signal: {signal.get('coin')} {signal.get('direction')} "
                f"@ {signal.get('confidence')}%"
            )
            signals.append(signal)
        else:
            logger.info(f"Not tradeable: {signal.get('reason', '—')}")

    # Best signal admin ko bhejo
    best = pick_best_signal(signals)
    if best:
        logger.info(f"Best → {best['coin']} {best['direction']} @ {best['confidence']}%")
        await send_signal_to_admin(best)
    else:
        logger.info(f"No signal above {config.MIN_CONFIDENCE}% threshold.")
