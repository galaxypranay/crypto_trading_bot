import time
import logging
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes
import config
from services.trade_executor import execute_trade
from services.database import (
    log_trade, save_pending_signal, get_pending_signal,
    delete_pending_signal, load_all_pending_signals
)

logger = logging.getLogger(__name__)

_trade_app: Application = None

# In-memory cache (fast lookup) + DB backup (restart-safe)
pending_signals: dict[str, dict] = {}

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
    "news_title": "Test Signal",
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
        _trade_app.add_handler(CommandHandler("test",   handle_test_command))
        _trade_app.add_handler(CommandHandler("start",  handle_start_command))
        _trade_app.add_handler(CommandHandler("status", handle_status_command))
    return _trade_app


async def restore_pending_signals():
    """Startup pe DB se pending signals load karo."""
    global pending_signals
    restored = await load_all_pending_signals()
    pending_signals.update(restored)
    if restored:
        logger.info(f"Restored {len(restored)} pending signal(s) from DB.")


def format_signal_message(signal: dict) -> str:
    direction_emoji = "🟢" if signal["direction"] == "LONG" else "🔴"
    test_badge      = "🧪 *TEST SIGNAL*\n" if signal.get("is_test") else ""
    risk            = config.RISK_MODE
    leverage_range  = config.LEVERAGE_MAP.get(risk, {})

    # Risk/reward ratio
    try:
        entry = float(signal["entry"])
        tp    = float(signal["tp"])
        sl    = float(signal["sl"])
        if signal["direction"] == "LONG":
            rr = abs(tp - entry) / abs(entry - sl)
        else:
            rr = abs(entry - tp) / abs(sl - entry)
        rr_str = f"`{rr:.1f}:1`"
    except Exception:
        rr_str = "N/A"

    return (
        f"{test_badge}"
        f"{direction_emoji} *{signal['coin']} {signal['direction']}*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 Confidence: `{signal['confidence']}%`\n"
        f"⚡ Leverage: `{signal['leverage']}x` _(Risk: {risk})_\n"
        f"🎯 Entry: `{signal['entry']}`\n"
        f"✅ Take Profit: `{signal['tp']}`\n"
        f"❌ Stop Loss: `{signal['sl']}`\n"
        f"📐 Risk/Reward: {rr_str}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📰 *News:* {signal.get('news_title', 'N/A')}\n"
        f"📡 *Source:* {signal.get('news_source', 'N/A')}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💡 *Reason:* _{signal.get('reason', 'N/A')}_\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ Trade size: `${config.TRADE_SIZE_USDT} USDT`"
    )


async def send_signal_to_admin(signal: dict) -> bool:
    app = get_trade_app()
    bot: Bot = app.bot

    unique_id = f"{signal.get('coin', 'X')}_{signal.get('direction', 'X')}_{int(time.time())}"

    # Memory + DB dono mein save karo
    pending_signals[unique_id] = signal
    await save_pending_signal(unique_id, signal)

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
        logger.info(f"Signal sent to admin: {signal['coin']} {signal['direction']} @ {signal.get('confidence')}%")
        return True
    except TelegramError as e:
        logger.error(f"Failed to send signal: {e}")
        # Cleanup agar send fail ho
        pending_signals.pop(unique_id, None)
        await delete_pending_signal(unique_id)
        return False


async def handle_start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != config.TELEGRAM_ADMIN_CHAT_ID:
        return
    await update.message.reply_text(
        "🤖 *Trade Bot Active!*\n\n"
        "Commands:\n"
        "📌 `/test` — dummy signal bhejo (real trade nahi hoga)\n"
        "📊 `/status` — bot ki current status dekho\n\n"
        f"Current Settings:\n"
        f"• Risk Mode: `{config.RISK_MODE}`\n"
        f"• Min Confidence: `{config.MIN_CONFIDENCE}%`\n"
        f"• Trade Size: `${config.TRADE_SIZE_USDT} USDT`\n"
        f"• AI Model: `{config.FREEMODEL_MODEL}`\n\n"
        "Jab AI koi achha trade dhundega, yahan Approve/Reject milega.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def handle_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != config.TELEGRAM_ADMIN_CHAT_ID:
        return
    count = len(pending_signals)
    await update.message.reply_text(
        f"📊 *Bot Status*\n\n"
        f"• Pending signals: `{count}`\n"
        f"• Risk Mode: `{config.RISK_MODE}`\n"
        f"• Min Confidence: `{config.MIN_CONFIDENCE}%`\n"
        f"• Trade Size: `${config.TRADE_SIZE_USDT} USDT`",
        parse_mode=ParseMode.MARKDOWN,
    )


async def handle_test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != config.TELEGRAM_ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ Unauthorized")
        return
    await update.message.reply_text("🧪 Test signal bhej raha hoon...")
    await send_signal_to_admin(TEST_SIGNAL.copy())


async def handle_approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != config.TELEGRAM_ADMIN_CHAT_ID:
        await query.answer("⛔ Unauthorized", show_alert=True)
        return

    try:
        action, unique_id = query.data.split("|", 1)
    except ValueError:
        await query.edit_message_text("⚠️ Invalid callback data.")
        return

    # Memory se pehle try karo, phir DB se
    signal = pending_signals.get(unique_id) or await get_pending_signal(unique_id)

    if not signal:
        await query.edit_message_text(
            "⚠️ Signal expired or not found.\n"
            "_Bot restart ke baad purane signals expire ho jaate hain._",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if action == "approve":
        if signal.get("is_test"):
            await query.edit_message_text(
                "✅ *Test Approved!*\n\nBot sahi kaam kar raha hai 🎉\n"
                "_(Test mode — koi real trade nahi hua)_",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await query.edit_message_text(
                f"⏳ *Executing trade...*\n\n"
                f"{signal['coin']} {signal['direction']} @ `{signal['entry']}`\n"
                f"Leverage: `{signal['leverage']}x`",
                parse_mode=ParseMode.MARKDOWN,
            )
            result = await execute_trade(signal)
            status = "approved" if result["success"] else "failed"
            await log_trade(signal, status)

            if result["success"]:
                await query.edit_message_text(
                    f"✅ *Trade Executed!*\n\n"
                    f"*{signal['coin']} {signal['direction']}* @ `{signal['entry']}`\n"
                    f"Leverage: `{signal['leverage']}x` | Confidence: `{signal['confidence']}%`\n\n"
                    f"🎯 TP: `{signal['tp']}`\n"
                    f"🛑 SL: `{signal['sl']}`\n\n"
                    f"_{result['message']}_",
                    parse_mode=ParseMode.MARKDOWN,
                )
            else:
                await query.edit_message_text(
                    f"❌ *Trade Failed!*\n\n`{result['message']}`\n\n"
                    f"_Check logs for details._",
                    parse_mode=ParseMode.MARKDOWN,
                )

    elif action == "reject":
        if not signal.get("is_test"):
            await log_trade(signal, "rejected")
        label = "Test Rejected" if signal.get("is_test") else "Trade Rejected"
        await query.edit_message_text(
            f"🚫 *{label}*\n\n"
            f"{signal['coin']} {signal['direction']} signal dropped.\n"
            f"Confidence was: `{signal.get('confidence', 'N/A')}%`",
            parse_mode=ParseMode.MARKDOWN,
        )

    # Cleanup dono jagah se
    pending_signals.pop(unique_id, None)
    await delete_pending_signal(unique_id)


async def send_error_to_admin(error_msg: str):
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
