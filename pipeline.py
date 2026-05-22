import logging
import asyncio

from services.news_fetcher import fetch_coingecko_news
from services.ai_analyzer import generate_description, analyze_news, pick_best_signal
from handlers.news_bot import post_news_to_channel, send_error_to_channel
from handlers.trade_bot import send_signal_to_admin, send_error_to_admin
import config

logger = logging.getLogger(__name__)

# Already processed news IDs
seen_news_ids: set[str] = set()


async def run_pipeline():
    """
    Pipeline flow:
    1. CoinGecko se sirf coin-related news fetch karo
    2. Naye articles filter karo
    3. FreeModel se description generate karo → channel me post karo
    4. FreeModel se trade signal analyze karo
    5. Best signal → admin trade bot ko bhejo
    """
    logger.info("Pipeline triggered — fetching CoinGecko news...")

    try:
        all_news = await fetch_coingecko_news()
    except Exception as e:
        error = f"CoinGecko fetch failed: {e}"
        logger.error(error)
        await send_error_to_admin(error)
        return

    new_articles = [n for n in all_news if n["id"] not in seen_news_ids]

    if not new_articles:
        logger.info("No new coin-related articles.")
        return

    logger.info(f"{len(new_articles)} new coin articles found.")

    signals = []

    for article in new_articles:
        seen_news_ids.add(article["id"])

        coin = article["coin"]
        logger.info(f"Processing [{coin}]: {article['title'][:55]}")

        # ── Step 1: AI description generate karo (FreeModel) ──
        try:
            description = await generate_description(article)
        except Exception as e:
            logger.error(f"Description gen error: {e}")
            description = article.get("description", article["title"])

        # ── Step 2: Channel mein post karo ────────────────────
        await post_news_to_channel(article, description)
        await asyncio.sleep(1.5)   # Telegram rate limit

        # ── Step 3: Trade signal analyze karo (FreeModel) ────
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

    # ── Step 4: Best signal admin ko bhejo ───────────────────
    best = pick_best_signal(signals)
    if best:
        logger.info(
            f"Best signal → {best['coin']} {best['direction']} "
            f"@ {best['confidence']}% — sending to admin..."
        )
        await send_signal_to_admin(best)
    else:
        logger.info(f"No signal above {config.MIN_CONFIDENCE}% threshold.")

    # Memory cleanup
    if len(seen_news_ids) > 1000:
        keep = list(seen_news_ids)[-500:]
        seen_news_ids.clear()
        seen_news_ids.update(keep)
