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


def format_news_message(news_item: dict) -> str:
    """Format a news item for the Telegram channel."""
    coin = news_item.get("coin")
    coin_tag = f"#{coin} " if coin else ""

    title = news_item["title"]
    source = news_item.get("source", "Unknown")
    url = news_item["url"]
    summary = news_item.get("summary", "")

    lines = [
        f"📰 *{title}*",
        "",
    ]

    if summary:
        # Trim summary to 200 chars
        short = summary[:200] + ("..." if len(summary) > 200 else "")
        lines.append(f"_{short}_")
        lines.append("")

    lines.append(f"🔗 [Read full article]({url})")
    lines.append(f"📡 Source: {source}")

    if coin_tag:
        lines.append(f"\n{coin_tag}#crypto #news")

    return "\n".join(lines)


async def post_news_to_channel(news_item: dict) -> bool:
    """Post a single news item to the Telegram news channel."""
    bot = get_news_bot()
    message = format_news_message(news_item)

    try:
        await bot.send_message(
            chat_id=config.TELEGRAM_CHANNEL_ID,
            text=message,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=False,
        )
        logger.info(f"Posted to channel: {news_item['title'][:60]}")
        return True

    except TelegramError as e:
        logger.error(f"Failed to post news to channel: {e}")
        return False


async def send_error_to_channel(error_msg: str):
    """Post a system error alert to the channel."""
    bot = get_news_bot()
    try:
        await bot.send_message(
            chat_id=config.TELEGRAM_CHANNEL_ID,
            text=f"⚠️ *System Alert*\n\n`{error_msg}`",
            parse_mode=ParseMode.MARKDOWN,
        )
    except TelegramError as e:
        logger.error(f"Failed to send error to channel: {e}")
