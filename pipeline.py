import logging
import asyncio

from services.news_fetcher import fetch_coingecko_news
from services.ai_analyzer import generate_description, analyze_news, pick_best_signal
from services.database import is_seen, mark_seen, is_too_old, log_news
from handlers.news_bot import post_news_to_channel, send_error_to_channel
from handlers.trade_bot import send_signal_to_admin, send_error_to_admin
import config

logger = logging.getLogger(__name__)


async def run_pipeline():
    """
    Pipeline:
    1. RSS feeds se coin-related news fetch karo
    2. Purani news (6 ghante se zyada) skip karo
    3. DB se check — duplicate skip karo
    4. FreeModel se description → channel post
    5. FreeModel se trade signal
    6. Best signal → admin trade bot
    """
    logger.info("Pipeline triggered — fetching news...")

    try:
        all_news = await fetch_coingecko_news()
    except Exception as e:
        error = f"News fetch failed: {e}"
        logger.error(error)
        await send_error_to_admin(error)
        return

    # Filter: naye + fresh articles sirf
    new_articles = []
    for article in all_news:

        # 1. Purani news skip karo
        if is_too_old(article["published_at"]):
            logger.info(f"Too old — skip: {article['title'][:50]}")
            await mark_seen(article["id"])  # future mein bhi skip ho
            continue

        # 2. Duplicate check
        if await is_seen(article["id"]):
            continue

        new_articles.append(article)

    if not new_articles:
        logger.info("No new fresh articles.")
        return

    logger.info(f"{len(new_articles)} new fresh articles found.")

    signals = []

    for article in new_articles:
        # Turant mark karo — reprocessing rokne ke liye
        await mark_seen(article["id"])

        logger.info(f"Processing [{article['coin']}]: {article['title'][:55]}")

        # Step 1: AI description generate karo
        try:
            description = await generate_description(article)
        except Exception as e:
            logger.error(f"Description error: {e}")
            description = article.get("description", article["title"])

        # Step 2: Channel mein post karo + DB mein log karo
        posted = await post_news_to_channel(article, description)
        if posted:
            await log_news(article)

        await asyncio.sleep(1.5)  # Telegram rate limit

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
        logger.info(
            f"Best → {best['coin']} {best['direction']} @ {best['confidence']}%"
        )
        await send_signal_to_admin(best)
    else:
        logger.info(f"No signal above {config.MIN_CONFIDENCE}% threshold.")
