import logging
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError
import config

logger = logging.getLogger(__name__)

_news_bot: Bot = None


def get_news_bot() -> Bot:
    global _news_bot
    if _news_bot is None:
        _news_bot = Bot(token=config.NEWS_BOT_TOKEN)
    return _news_bot


def format_news_message(news_item: dict, ai_description: str) -> str:
    """
    Format news for Telegram channel.
    AI-generated description use hoti hai — generic nahi.
    """
    coin    = news_item["coin"]
    title   = news_item["title"]
    url     = news_item["url"]
    source  = news_item.get("source", "Unknown")

    return (
        f"#{coin} 📰 *{title}*\n\n"
        f"{ai_description}\n\n"
        f"🔗 [Read more]({url})\n"
        f"📡 _{source}_"
    )


async def post_news_to_channel(news_item: dict, ai_description: str) -> bool:
    """Post a single coin-related news item to the Telegram channel."""
    bot = get_news_bot()
    message = format_news_message(news_item, ai_description)

    try:
        await bot.send_message(
            chat_id=config.TELEGRAM_CHANNEL_ID,
            text=message,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=False,
        )
        logger.info(f"Channel post: [{news_item['coin']}] {news_item['title'][:50]}")
        return True
    except TelegramError as e:
        logger.error(f"Channel post failed: {e}")
        return False


async def send_error_to_channel(error_msg: str):
    """Post system error to channel."""
    bot = get_news_bot()
    try:
        await bot.send_message(
            chat_id=config.TELEGRAM_CHANNEL_ID,
            text=f"⚠️ *System Alert*\n\n`{error_msg}`",
            parse_mode=ParseMode.MARKDOWN,
        )
    except TelegramError as e:
        logger.error(f"Channel error post failed: {e}")
