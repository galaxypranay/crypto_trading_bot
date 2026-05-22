import time
import logging
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes
import config
from services.trade_executor import execute_trade

logger = logging.getLogger(__name__)

_trade_app: Application = None

# In-memory store: unique_id → signal dict
pending_signals: dict[str, dict] = {}

# Dummy signal for /test command
TEST_SIGNAL = {
    "tradeable": True,
    "coin": "BTC",
    "direction": "LONG",
    "confidence": 95,
    "leverage": 20,
    "entry": 67000,
    "tp": 68500,
    "sl": 66000,
    "reason": "TEST MODE — system check, koi real trade nahi hoga.",
    "news_title": "🧪 Test Signal",
    "news_source": "Manual /test command",
    "is_test": True,
}


def get_trade_app() -> Application:
    global _trade_app
    if _trade_app is None:
        _trade_app = (
            Application.builder()
            .token(config.TRADE_BOT_TOKEN)
            .build()
        )
        _trade_app.add_handler(CallbackQueryHandler(handle_approval_callback))
        _trade_app.add_handler(CommandHandler("test", handle_test_command))
        _trade_app.add_handler(CommandHandler("start", handle_start_command))
    return _trade_app


def format_signal_message(signal: dict) -> str:
    """Format the trade signal card sent to admin."""
    direction_emoji = "🟢" if signal["direction"] == "LONG" else "🔴"
    test_badge = "🧪 *TEST SIGNAL*\n" if signal.get("is_test") else ""

    return (
        f"{test_badge}"
        f"{direction_emoji} *{signal['coin']} {signal['direction']}*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 Confidence: `{signal['confidence']}%`\n"
        f"⚡ Leverage: `{signal['leverage']}x`\n"
        f"🎯 Entry: `{signal['entry']}`\n"
        f"✅ Take Profit: `{signal['tp']}`\n"
        f"❌ Stop Loss: `{signal['sl']}`\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📰 *News:* {signal.get('news_title', 'N/A')}\n"
        f"📡 *Source:* {signal.get('news_source', 'N/A')}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💡 *Reason:* _{signal.get('reason', 'N/A')}_\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ Risk Mode: `{config.RISK_MODE}`"
    )


async def send_signal_to_admin(signal: dict) -> bool:
    """Send trade signal with Approve/Reject buttons to admin."""
    app = get_trade_app()
    bot: Bot = app.bot

    signal_id = signal.get("news_title", "")[:20].replace(" ", "_")
    unique_id = f"{signal_id}_{int(time.time())}"
    pending_signals[unique_id] = signal

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ APPROVE", callback_data=f"approve|{unique_id}"),
        InlineKeyboardButton("❌ REJECT",  callback_data=f"reject|{unique_id}"),
    ]])

    try:
        await bot.send_message(
            chat_id=config.TELEGRAM_ADMIN_CHAT_ID,
            text=format_signal_message(signal),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )
        logger.info(f"Signal sent to admin: {signal['coin']} {signal['direction']}")
        return True
    except TelegramError as e:
        logger.error(f"Failed to send signal to admin: {e}")
        return False


async def handle_start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start — bot intro message."""
    if update.effective_user.id != config.TELEGRAM_ADMIN_CHAT_ID:
        return
    await update.message.reply_text(
        "🤖 *Crypto Trade Bot active!*\n\n"
        "Commands:\n"
        "📌 `/test` — test signal bhejo (koi real trade nahi)\n\n"
        "Jab AI koi accha trade dhundega, yahan approve/reject milega.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def handle_test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/test — dummy signal bhejo to check bot is working."""
    if update.effective_user.id != config.TELEGRAM_ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ Unauthorized")
        return

    await update.message.reply_text("🧪 Test signal bhej raha hoon...")
    await send_signal_to_admin(TEST_SIGNAL.copy())


async def handle_approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin pressing Approve or Reject."""
    query = update.callback_query
    await query.answer()

    # Security: sirf admin interact kar sakta hai
    if query.from_user.id != config.TELEGRAM_ADMIN_CHAT_ID:
        await query.answer("⛔ Unauthorized", show_alert=True)
        return

    action, unique_id = query.data.split("|", 1)
    signal = pending_signals.get(unique_id)

    if not signal:
        await query.edit_message_text("⚠️ Signal expired or not found.")
        return

    if action == "approve":
        # Test signal ke liye real trade mat karo
        if signal.get("is_test"):
            await query.edit_message_text(
                text=(
                    "✅ *Test Approved!*\n\n"
                    "Bot sahi kaam kar raha hai 🎉\n"
                    "_(Test mode — koi real trade nahi hua)_"
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await query.edit_message_text(
                text=f"⏳ Executing: *{signal['coin']} {signal['direction']}*...",
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
        label = "Test Rejected" if signal.get("is_test") else "Trade Rejected"
        await query.edit_message_text(
            text=(
                f"🚫 *{label}*\n\n"
                f"{signal['coin']} {signal['direction']} signal dropped.\n"
                f"Confidence was: `{signal['confidence']}%`"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )

    pending_signals.pop(unique_id, None)


async def send_error_to_admin(error_msg: str):
    """Send a system error alert to admin."""
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
