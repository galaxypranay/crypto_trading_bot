import time
import asyncio
import logging
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler,
    ContextTypes, MessageHandler, filters,
)
import config
from services.trade_executor import execute_trade
from services.database import (
    log_trade, save_pending_signal, get_pending_signal,
    delete_pending_signal, load_all_pending_signals,
)

logger = logging.getLogger(__name__)

_trade_app: Application = None

# In-memory: pending signals + amount-awaiting state
pending_signals: dict[str, dict] = {}

# Signals jinpe admin ne Approve kiya lekin amount abhi choose nahi kiya
# { unique_id: {"signal": ..., "message_id": ..., "expire_at": float} }
awaiting_amount: dict[str, dict] = {}

AMOUNT_TIMEOUT_SECS = 300  # 5 minutes

PRESET_AMOUNTS = [20, 50, 100, 200, 500]

TEST_SIGNAL = {
    "tradeable":   True,
    "coin":        "BTC",
    "direction":   "LONG",
    "confidence":  95,
    "leverage":    15,
    "entry":       0,
    "tp":          0,
    "sl":          0,
    "reason":      "TEST MODE — system check, koi real trade nahi hoga.",
    "news_title":  "Test Signal",
    "news_source": "Manual /test command",
    "is_test":     True,
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
        _trade_app.add_handler(CommandHandler("test",    handle_test_command))
        _trade_app.add_handler(CommandHandler("start",   handle_start_command))
        _trade_app.add_handler(CommandHandler("status",  handle_status_command))
        _trade_app.add_handler(CommandHandler("balance", handle_balance_command))
        # Custom amount text handler — sirf admin ke messages
        _trade_app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.User(user_id=config.TELEGRAM_ADMIN_CHAT_ID),
            handle_custom_amount_message,
        ))
    return _trade_app


async def restore_pending_signals():
    global pending_signals
    restored = await load_all_pending_signals()
    pending_signals.update(restored)
    if restored:
        logger.info(f"Restored {len(restored)} pending signal(s) from DB.")


# ── Signal message format ─────────────────────────────────────

def format_signal_message(signal: dict) -> str:
    direction_emoji = "🟢" if signal["direction"] == "LONG" else "🔴"
    test_badge      = "🧪 *TEST SIGNAL*\n" if signal.get("is_test") else ""
    risk            = config.RISK_MODE

    try:
        entry = float(signal["entry"])
        tp    = float(signal["tp"])
        sl    = float(signal["sl"])
        if entry > 0:
            rr = abs(tp - entry) / abs(entry - sl) if signal["direction"] == "LONG" \
                 else abs(entry - tp) / abs(sl - entry)
            rr_str = f"`{rr:.1f}:1`"
        else:
            rr_str = "N/A"
    except Exception:
        rr_str = "N/A"

    entry_str = f"`{signal['entry']}`" if signal.get("entry") else "Market Price"
    tp_str    = f"`{signal['tp']}`"    if signal.get("tp")    else "N/A"
    sl_str    = f"`{signal['sl']}`"    if signal.get("sl")    else "N/A"

    return (
        f"{test_badge}"
        f"{direction_emoji} *{signal['coin']} {signal['direction']}*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 Confidence: `{signal['confidence']}%`\n"
        f"⚡ Leverage: `{signal['leverage']}x` _(Risk: {risk})_\n"
        f"🎯 Entry: {entry_str}\n"
        f"✅ Take Profit: {tp_str}\n"
        f"❌ Stop Loss: {sl_str}\n"
        f"📐 Risk/Reward: {rr_str}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📰 *News:* {signal.get('news_title', 'N/A')}\n"
        f"📡 *Source:* {signal.get('news_source', 'N/A')}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💡 *Reason:* _{signal.get('reason', 'N/A')}_\n"
        f"━━━━━━━━━━━━━━━━━━"
    )


def _amount_keyboard(unique_id: str) -> InlineKeyboardMarkup:
    """Preset amount buttons + Cancel."""
    buttons = [
        InlineKeyboardButton(f"${a}", callback_data=f"amount|{unique_id}|{a}")
        for a in PRESET_AMOUNTS
    ]
    # 3 buttons first row, 2 buttons second row
    rows = [buttons[:3], buttons[3:], [
        InlineKeyboardButton("✏️ Custom amount type karo", callback_data=f"amount_custom|{unique_id}"),
        InlineKeyboardButton("🚫 Cancel",                  callback_data=f"amount_cancel|{unique_id}"),
    ]]
    return InlineKeyboardMarkup(rows)


# ── Send signal to admin ──────────────────────────────────────

async def send_signal_to_admin(signal: dict) -> bool:
    app = get_trade_app()
    bot: Bot = app.bot

    unique_id = f"{signal.get('coin', 'X')}_{signal.get('direction', 'X')}_{int(time.time())}"
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
        logger.info(f"Signal sent: {signal['coin']} {signal['direction']} @ {signal.get('confidence')}%")
        return True
    except TelegramError as e:
        logger.error(f"Failed to send signal: {e}")
        pending_signals.pop(unique_id, None)
        await delete_pending_signal(unique_id)
        return False


# ── Timeout checker ───────────────────────────────────────────

async def _check_amount_timeouts():
    """
    Background task — har 30 sec mein check karo.
    Agar 5 min mein amount nahi choose kiya toh trade cancel.
    """
    while True:
        await asyncio.sleep(30)
        now     = time.time()
        expired = [uid for uid, v in awaiting_amount.items() if now > v["expire_at"]]

        for uid in expired:
            entry = awaiting_amount.pop(uid, None)
            if not entry:
                continue

            signal = entry["signal"]
            msg_id = entry["message_id"]

            # Signal cleanup
            pending_signals.pop(uid, None)
            await delete_pending_signal(uid)
            await log_trade(signal, "timeout")

            # Edit message — amount timeout
            app = get_trade_app()
            try:
                await app.bot.edit_message_text(
                    chat_id=config.TELEGRAM_ADMIN_CHAT_ID,
                    message_id=msg_id,
                    text=(
                        f"⏰ *Trade Cancelled — Timeout*\n\n"
                        f"{signal['coin']} {signal['direction']}\n"
                        f"_5 minute mein amount select nahi kiya gaya._"
                    ),
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception as e:
                logger.error(f"Timeout edit failed: {e}")

            logger.info(f"Trade timeout: {uid}")


def start_timeout_checker():
    """main.py se startup mein call karo."""
    asyncio.create_task(_check_amount_timeouts())


# ── Callback handler ──────────────────────────────────────────

async def handle_approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != config.TELEGRAM_ADMIN_CHAT_ID:
        await query.answer("⛔ Unauthorized", show_alert=True)
        return

    data = query.data

    # ── APPROVE ───────────────────────────────────────────────
    if data.startswith("approve|"):
        _, unique_id = data.split("|", 1)
        signal = pending_signals.get(unique_id) or await get_pending_signal(unique_id)

        if not signal:
            await query.edit_message_text(
                "⚠️ Signal expired or not found.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        if signal.get("is_test"):
            await query.edit_message_text(
                "✅ *Test Approved!*\n\nBot sahi kaam kar raha hai 🎉\n"
                "_(Test mode — koi real trade nahi hua)_",
                parse_mode=ParseMode.MARKDOWN,
            )
            pending_signals.pop(unique_id, None)
            await delete_pending_signal(unique_id)
            return

        # Amount selection step
        expire_at = time.time() + AMOUNT_TIMEOUT_SECS
        awaiting_amount[unique_id] = {
            "signal":     signal,
            "message_id": query.message.message_id,
            "expire_at":  expire_at,
        }

        await query.edit_message_text(
            text=(
                f"{format_signal_message(signal)}\n\n"
                f"💰 *Kitne USDT ka trade karna hai?*\n"
                f"_Preset choose karo ya custom amount type karo._\n"
                f"⏳ _5 minute mein select nahi kiya toh cancel ho jayega._"
            ),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_amount_keyboard(unique_id),
        )

    # ── REJECT ────────────────────────────────────────────────
    elif data.startswith("reject|"):
        _, unique_id = data.split("|", 1)
        signal = pending_signals.get(unique_id) or await get_pending_signal(unique_id)

        if signal and not signal.get("is_test"):
            await log_trade(signal, "rejected")

        label = "Test Rejected" if (signal and signal.get("is_test")) else "Trade Rejected"
        await query.edit_message_text(
            f"🚫 *{label}*\n\n"
            f"{signal['coin'] if signal else '?'} signal dropped.\n"
            f"Confidence was: `{signal.get('confidence', 'N/A') if signal else 'N/A'}%`",
            parse_mode=ParseMode.MARKDOWN,
        )
        pending_signals.pop(unique_id, None)
        await delete_pending_signal(unique_id)

    # ── PRESET AMOUNT ─────────────────────────────────────────
    elif data.startswith("amount|"):
        _, unique_id, amount_str = data.split("|", 2)
        await _execute_with_amount(query, unique_id, float(amount_str))

    # ── CUSTOM AMOUNT (button press) ──────────────────────────
    elif data.startswith("amount_custom|"):
        _, unique_id = data.split("|", 1)
        entry = awaiting_amount.get(unique_id)
        if not entry:
            await query.edit_message_text("⚠️ Signal expired.")
            return

        signal = entry["signal"]
        # unique_id context mein save karo taaki text message match ho sake
        context.user_data["awaiting_custom_amount_id"] = unique_id

        await query.edit_message_text(
            text=(
                f"{format_signal_message(signal)}\n\n"
                f"✏️ *Custom amount type karo (USDT):*\n"
                f"_Example: 150 ya 750_\n"
                f"⏳ _5 minute mein nahi bheja toh cancel ho jayega._"
            ),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data=f"amount_back|{unique_id}"),
                InlineKeyboardButton("🚫 Cancel", callback_data=f"amount_cancel|{unique_id}"),
            ]]),
        )

    # ── BACK to amount selection ───────────────────────────────
    elif data.startswith("amount_back|"):
        _, unique_id = data.split("|", 1)
        entry = awaiting_amount.get(unique_id)
        if not entry:
            await query.edit_message_text("⚠️ Signal expired.")
            return

        signal = entry["signal"]
        context.user_data.pop("awaiting_custom_amount_id", None)

        await query.edit_message_text(
            text=(
                f"{format_signal_message(signal)}\n\n"
                f"💰 *Kitne USDT ka trade karna hai?*\n"
                f"_Preset choose karo ya custom amount type karo._\n"
                f"⏳ _5 minute mein select nahi kiya toh cancel ho jayega._"
            ),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_amount_keyboard(unique_id),
        )

    # ── CANCEL amount ─────────────────────────────────────────
    elif data.startswith("amount_cancel|"):
        _, unique_id = data.split("|", 1)
        entry = awaiting_amount.pop(unique_id, None)
        signal = (entry["signal"] if entry else None) or \
                 pending_signals.get(unique_id) or await get_pending_signal(unique_id)

        if signal:
            await log_trade(signal, "cancelled")

        pending_signals.pop(unique_id, None)
        await delete_pending_signal(unique_id)
        context.user_data.pop("awaiting_custom_amount_id", None)

        await query.edit_message_text(
            f"🚫 *Trade Cancelled*\n\n"
            f"{signal['coin'] if signal else '?'} signal cancelled by admin.",
            parse_mode=ParseMode.MARKDOWN,
        )


# ── Custom amount via text message ────────────────────────────

async def handle_custom_amount_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin ne text mein custom amount type kiya."""
    unique_id = context.user_data.get("awaiting_custom_amount_id")
    if not unique_id:
        return  # Koi pending custom amount nahi — ignore

    text = update.message.text.strip().replace("$", "").replace(",", "")

    try:
        amount = float(text)
        if amount <= 0:
            raise ValueError("Amount must be positive")
    except ValueError:
        await update.message.reply_text(
            "⚠️ Invalid amount। Sirf number type karo, jaise: `150` ya `500`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Confirm karo
    context.user_data.pop("awaiting_custom_amount_id", None)

    # amount_callback simulate
    class FakeQuery:
        def __init__(self, unique_id, msg):
            self.data       = f"amount|{unique_id}|{amount}"
            self.message    = msg
            self.from_user  = update.effective_user
        async def answer(self): pass
        async def edit_message_text(self, **kwargs):
            await update.message.reply_text(**kwargs)

    await _execute_with_amount(FakeQuery(unique_id, update.message), unique_id, amount)


# ── Core: execute trade with chosen amount ────────────────────

async def _execute_with_amount(query, unique_id: str, amount_usdt: float):
    """Amount confirm ho gayi — trade execute karo."""
    entry = awaiting_amount.pop(unique_id, None)
    signal = (entry["signal"] if entry else None) or \
             pending_signals.get(unique_id) or await get_pending_signal(unique_id)

    if not signal:
        try:
            await query.edit_message_text("⚠️ Signal expired or not found.")
        except Exception:
            pass
        return

    # Cleanup
    pending_signals.pop(unique_id, None)
    await delete_pending_signal(unique_id)

    # Signal mein trade size inject karo
    signal["trade_size_usdt"] = amount_usdt

    try:
        await query.edit_message_text(
            text=(
                f"⏳ *Executing trade...*\n\n"
                f"{signal['coin']} {signal['direction']} @ `{signal['entry']}`\n"
                f"Leverage: `{signal['leverage']}x`\n"
                f"💰 Size: `${amount_usdt} USDT`"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        pass

    result = await execute_trade(signal)
    status = "approved" if result["success"] else "failed"
    await log_trade(signal, status)

    if result["success"]:
        try:
            await query.edit_message_text(
                text=(
                    f"✅ *Trade Executed!*\n\n"
                    f"*{signal['coin']} {signal['direction']}* @ `{signal['entry']}`\n"
                    f"Leverage: `{signal['leverage']}x` | Confidence: `{signal['confidence']}%`\n"
                    f"💰 Size: `${amount_usdt} USDT`\n\n"
                    f"🎯 TP: `{signal['tp']}`\n"
                    f"🛑 SL: `{signal['sl']}`\n\n"
                    f"_{result['message']}_"
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass
    else:
        try:
            await query.edit_message_text(
                text=(
                    f"❌ *Trade Failed!*\n\n"
                    f"`{result['message']}`\n\n"
                    f"_Check logs for details._"
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass


# ── Commands ──────────────────────────────────────────────────

async def handle_start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != config.TELEGRAM_ADMIN_CHAT_ID:
        return
    await update.message.reply_text(
        "🤖 *Trade Bot Active!*\n\n"
        "Commands:\n"
        "📌 `/test` — dummy signal bhejo\n"
        "📊 `/status` — bot status dekho\n"
        "💰 `/balance` — account balance check karo\n\n"
        f"Settings:\n"
        f"• Risk Mode: `{config.RISK_MODE}`\n"
        f"• Min Confidence: `{config.MIN_CONFIDENCE}%`\n"
        f"• AI Model: `{config.FREEMODEL_MODEL}`",
        parse_mode=ParseMode.MARKDOWN,
    )


async def handle_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != config.TELEGRAM_ADMIN_CHAT_ID:
        return
    await update.message.reply_text(
        f"📊 *Bot Status*\n\n"
        f"• Pending signals: `{len(pending_signals)}`\n"
        f"• Awaiting amount: `{len(awaiting_amount)}`\n"
        f"• Risk Mode: `{config.RISK_MODE}`\n"
        f"• Min Confidence: `{config.MIN_CONFIDENCE}%`\n"
        f"• API URL: `{config.BULK_API_URL}`",
        parse_mode=ParseMode.MARKDOWN,
    )


async def handle_balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != config.TELEGRAM_ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ Unauthorized")
        return

    await update.message.reply_text("🔍 Balance check kar raha hoon...")

    import httpx
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{config.BULK_API_URL}/account",
                json={"type": "fullAccount", "user": config.BULK_WALLET_ADDRESS},
                headers={"Content-Type": "application/json"},
            )
        if resp.status_code != 200:
            await update.message.reply_text(
                f"❌ API error: {resp.status_code}\n`{resp.text[:200]}`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        data   = resp.json()
        margin = {}
        for item in data:
            if "fullAccount" in item:
                margin = item["fullAccount"].get("margin", {})
                break

        if margin:
            await update.message.reply_text(
                f"💰 *Account Balance*\n\n"
                f"• Total: `${margin.get('totalBalance', 0):,.2f}`\n"
                f"• Available: `${margin.get('availableBalance', 0):,.2f}`\n"
                f"• Margin Used: `${margin.get('marginUsed', 0):,.2f}`\n"
                f"• Unrealized PnL: `${margin.get('unrealizedPnl', 0):,.2f}`",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await update.message.reply_text("⚠️ Balance data nahi mila.")
    except Exception as e:
        await update.message.reply_text(
            f"❌ Balance check failed: `{e}`",
            parse_mode=ParseMode.MARKDOWN,
        )


async def handle_test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != config.TELEGRAM_ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ Unauthorized")
        return
    await update.message.reply_text("🧪 Test signal bhej raha hoon...")
    await send_signal_to_admin(TEST_SIGNAL.copy())


async def send_error_to_admin(error_msg: str):
    app = get_trade_app()
    try:
        await app.bot.send_message(
            chat_id=config.TELEGRAM_ADMIN_CHAT_ID,
            text=f"🚨 *System Error*\n\n`{error_msg}`",
            parse_mode=ParseMode.MARKDOWN,
        )
    except TelegramError as e:
        logger.error(f"Failed to send error: {e}")
