import logging
import asyncio

from services.news_fetcher import fetch_coingecko_news
from services.ai_analyzer import generate_description, analyze_news, pick_best_signal
from services.database import is_seen, mark_seen, is_too_old, log_news
from handlers.news_bot import post_news_to_channel
from handlers.trade_bot import send_signal_to_admin, send_error_to_admin
import config

logger = logging.getLogger(__name__)

# Global lock — ek waqt mein sirf ek piopeline run karega
_pipeline_lock = asyncio.Lock()


async def run_pipeline():
    """
    Main pipeline — har 2 minute mein chalta hai:

    1. CoinGecko + RSS se fresh coin news fetch karo
    2. Purani news (6 ghante se zyada) skip karo
    3. DB se duplicate check karo
    4. OpenRouter se AI description banao → channel pe post karo
    5. FreeModel se trade signal analyze karo
    6. Best signal (highest confidence) → admin ko bhejo
    """
    # Agar pehle se chal raha hai toh skip karo (duplicate post se bachao)
    if _pipeline_lock.locked():
        logger.info("Pipeline already running — skipping this trigger.")
        return

    async with _pipeline_lock:
        await _run_pipeline_inner()


async def _run_pipeline_inner():
    logger.info("Pipeline triggered — fetching news...")

    try:
        all_news = await fetch_coingecko_news()
    except Exception as e:
        error = f"News fetch failed: {e}"
        logger.error(error)
        await send_error_to_admin(error)
        return

    if not all_news:
        logger.info("No articles fetched from any source.")
        return

    # ── Filter: sirf naye aur fresh articles ─────────────────
    new_articles = []
    for article in all_news:
        # Purani news skip (6 ghante se zyada) — mark bhi karo future ke liye
        if is_too_old(article["published_at"]):
            await mark_seen(article["id"])
            continue

        # Duplicate check
        if await is_seen(article["id"]):
            continue

        # Turant mark karo — is loop ke andar hi, dusra concurrent run na utha le
        await mark_seen(article["id"])
        new_articles.append(article)

    if not new_articles:
        logger.info("No new fresh articles found.")
        return

    logger.info(f"{len(new_articles)} new fresh article(s) found.")

    signals = []

    for article in new_articles:
        coin   = article["coin"]
        title  = article["title"][:60]
        source = article.get("from_source", "unknown")
        logger.info(f"Processing [{coin}] ({source}): {title}")

        # ── Step 1: OpenRouter se AI description generate karo ─
        try:
            description = await generate_description(article)
        except Exception as e:
            logger.error(f"Description error [{coin}]: {e}")
            description = article.get("description") or article["title"]

        # ── Step 2: Telegram channel mein post karo ───────────
        try:
            posted = await post_news_to_channel(article, description)
            if posted:
                await log_news(article)
                logger.info(f"Channel post success: [{coin}] {title}")
            else:
                logger.warning(f"Channel post failed: [{coin}] {title}")
        except Exception as e:
            logger.error(f"Channel post exception [{coin}]: {e}")

        # Telegram rate limit
        await asyncio.sleep(2)

        # ── Step 3: FreeModel se trade signal analyze karo ────
        try:
            signal = await analyze_news(article)
        except Exception as e:
            logger.error(f"Signal analysis error [{coin}]: {e}")
            continue

        if not signal:
            logger.info(f"No signal returned for [{coin}]")
            continue

        if signal.get("tradeable"):
            logger.info(
                f"Signal: {signal.get('coin')} {signal.get('direction')} "
                f"@ {signal.get('confidence')}% | lev={signal.get('leverage')}x"
            )
            signals.append(signal)
        else:
            logger.info(f"Not tradeable [{coin}]: {signal.get('reason', '—')}")

        await asyncio.sleep(1)

    # ── Step 4: Best signal admin ko bhejo ───────────────────
    if signals:
        best = pick_best_signal(signals)
        if best:
            logger.info(
                f"Best signal → {best['coin']} {best['direction']} "
                f"@ {best['confidence']}% | lev={best['leverage']}x"
            )
            await send_signal_to_admin(best)
        else:
            logger.info(
                f"No valid signal above {config.MIN_CONFIDENCE}% threshold. "
                f"({len(signals)} signal(s) analyzed)"
            )
    else:
        logger.info("No tradeable signals found in this batch.")
