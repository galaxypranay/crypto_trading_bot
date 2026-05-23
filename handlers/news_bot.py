import logging
from telegram import Bot, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes
import config
from services.news_fetcher import fetch_coingecko_news
from services.ai_analyzer import generate_description

logger = logging.getLogger(__name__)

_news_app: Application = None


def get_news_app() -> Application:
    global _news_app
    if _news_app is None:
        _news_app = (
            Application.builder()
            .token(config.NEWS_BOT_TOKEN)
            .build()
        )
        _news_app.add_handler(CommandHandler("postnews", handle_postnews_command))
        _news_app.add_handler(CommandHandler("start",    handle_start_command))
    return _news_app


def format_news_message(news_item: dict, ai_description: str) -> str:
    coin   = news_item["coin"]
    title  = news_item["title"]
    url    = news_item["url"]
    source = news_item.get("source", "Unknown")

    return (
        f"#{coin} 📰 *{title}*\n\n"
        f"{ai_description}\n\n"
        f"🔗 [Read more]({url})\n"
        f"📡 _{source}_"
    )


async def post_news_to_channel(news_item: dict, ai_description: str) -> bool:
    """Post a single coin-related news to Telegram channel."""
    app = get_news_app()
    bot: Bot = app.bot
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
    app = get_news_app()
    bot: Bot = app.bot
    try:
        await bot.send_message(
            chat_id=config.TELEGRAM_CHANNEL_ID,
            text=f"⚠️ *System Alert*\n\n`{error_msg}`",
            parse_mode=ParseMode.MARKDOWN,
        )
    except TelegramError as e:
        logger.error(f"Channel error post failed: {e}")


async def handle_start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != config.TELEGRAM_ADMIN_CHAT_ID:
        return
    await update.message.reply_text(
        "📰 *News Bot active!*\n\n"
        "Commands:\n"
        "📌 `/postnews` — latest coin news fetch karke channel mein post karo\n",
        parse_mode=ParseMode.MARKDOWN,
    )


async def handle_postnews_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/postnews — latest coin news fetch karke channel mein post karo."""
    if update.effective_user.id != config.TELEGRAM_ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ Unauthorized")
        return

    await update.message.reply_text("🔍 CoinGecko se latest news fetch kar raha hoon...")

    try:
        articles = await fetch_coingecko_news()
    except Exception as e:
        await update.message.reply_text(f"❌ News fetch failed: {e}")
        return

    if not articles:
        await update.message.reply_text("⚠️ Koi coin-related news nahi mili.")
        return

    # Sirf pehla (latest) article channel mein post karo
    article = articles[0]

    try:
        description = await generate_description(article)
    except Exception as e:
        logger.error(f"Description error: {e}")
        description = article.get("description", article["title"])

    posted = await post_news_to_channel(article, description)

    if posted:
        await update.message.reply_text(
            f"✅ News channel mein post ho gayi!\n\n"
            f"*{article['title']}*\n"
            f"Coin: #{article['coin']}",
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        await update.message.reply_text("❌ Channel post failed — NEWS_BOT_TOKEN check karo.")
