import json
import logging
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, CallbackQueryHandler, ContextTypes
import config
from services.trade_executor import execute_trade

logger = logging.getLogger(__name__)

_trade_app: Application = None

# In-memory store: callback_data_id → signal dict
pending_signals: dict[str, dict] = {}


def get_trade_app() -> Application:
    global _trade_app
    if _trade_app is None:
        _trade_app = (
            Application.builder()
            .token(config.TRADE_BOT_TOKEN)
            .build()
        )
        _trade_app.add_handler(CallbackQueryHandler(handle_approval_callback))
    return _trade_app


def format_signal_message(signal: dict) -> str:
    """Format the trade signal card sent to admin."""
    direction_emoji = "🟢" if signal["direction"] == "LONG" else "🔴"
    direction = signal["direction"]
    coin = signal["coin"]
    confidence = signal["confidence"]
    leverage = signal["leverage"]
    entry = signal["entry"]
    tp = signal["tp"]
    sl = signal["sl"]
    reason = signal.get("reason", "N/A")
    news_title = signal.get("news_title", "N/A")
    news_source = signal.get("news_source", "N/A")

    return (
        f"{direction_emoji} *{coin} {direction}*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 Confidence: `{confidence}%`\n"
        f"⚡ Leverage: `{leverage}x`\n"
        f"🎯 Entry: `{entry}`\n"
        f"✅ Take Profit: `{tp}`\n"
        f"❌ Stop Loss: `{sl}`\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📰 *News:* {news_title}\n"
        f"📡 *Source:* {news_source}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💡 *Reason:* _{reason}_\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ Risk Mode: `{config.RISK_MODE}`"
    )


async def send_signal_to_admin(signal: dict) -> bool:
    """Send trade signal with Approve/Reject buttons to admin."""
    app = get_trade_app()
    bot: Bot = app.bot

    # Store signal for later retrieval on callback
    signal_id = signal.get("news_title", "")[:20].replace(" ", "_")
    import time
    unique_id = f"{signal_id}_{int(time.time())}"
    pending_signals[unique_id] = signal

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ APPROVE", callback_data=f"approve|{unique_id}"),
            InlineKeyboardButton("❌ REJECT", callback_data=f"reject|{unique_id}"),
        ]
    ])

    message = format_signal_message(signal)

    try:
        await bot.send_message(
            chat_id=config.TELEGRAM_ADMIN_CHAT_ID,
            text=message,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )
        logger.info(f"Signal sent to admin: {signal['coin']} {signal['direction']}")
        return True

    except TelegramError as e:
        logger.error(f"Failed to send signal to admin: {e}")
        return False


async def handle_approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin pressing Approve or Reject."""
    query = update.callback_query
    await query.answer()

    # Security: only admin can interact
    if query.from_user.id != config.TELEGRAM_ADMIN_CHAT_ID:
        await query.answer("⛔ Unauthorized", show_alert=True)
        return

    data = query.data  # "approve|unique_id" or "reject|unique_id"
    action, unique_id = data.split("|", 1)
    signal = pending_signals.get(unique_id)

    if not signal:
        await query.edit_message_text("⚠️ Signal expired or not found.")
        return

    if action == "approve":
        await query.edit_message_text(
            text=f"⏳ Executing trade: {signal['coin']} {signal['direction']}...",
            parse_mode=ParseMode.MARKDOWN,
        )
        result = await execute_trade(signal)

        if result["success"]:
            await query.edit_message_text(
                text=(
                    f"✅ *Trade Executed!*\n\n"
                    f"*{signal['coin']} {signal['direction']}* @ `{signal['entry']}`\n"
                    f"Leverage: `{signal['leverage']}x` | Confidence: `{signal['confidence']}%`\n\n"
                    f"TP: `{signal['tp']}` | SL: `{signal['sl']}`\n\n"
                    f"_{result['message']}_"
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await query.edit_message_text(
                text=f"❌ *Trade Failed!*\n\n`{result['message']}`",
                parse_mode=ParseMode.MARKDOWN,
            )

    elif action == "reject":
        await query.edit_message_text(
            text=(
                f"🚫 *Trade Rejected*\n\n"
                f"{signal['coin']} {signal['direction']} signal dropped.\n"
                f"Confidence was: `{signal['confidence']}%`"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )

    # Remove from pending after action
    pending_signals.pop(unique_id, None)


async def send_error_to_admin(error_msg: str):
    """Send a system error message to admin."""
    app = get_trade_app()
    bot: Bot = app.bot
    try:
        await bot.send_message(
            chat_id=config.TELEGRAM_ADMIN_CHAT_ID,
            text=f"🚨 *System Error*\n\n`{error_msg}`",
            parse_mode=ParseMode.MARKDOWN,
        )
    except TelegramError as e:
        logger.error(f"Failed to send error to admin: {e}")
