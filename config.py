import os
from dotenv import load_dotenv

load_dotenv()

# ── News Bot ──────────────────────────────────────────────────
NEWS_BOT_TOKEN      = os.getenv("NEWS_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

# ── Trade Bot ─────────────────────────────────────────────────
TRADE_BOT_TOKEN        = os.getenv("TRADE_BOT_TOKEN")
TELEGRAM_ADMIN_CHAT_ID = int(os.getenv("TELEGRAM_ADMIN_CHAT_ID", "0"))

# ── OpenRouter (news description) ────────────────────────────
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL   = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-chat")

# ── FreeModel (trade signal) ──────────────────────────────────
FREEMODEL_API_KEY = os.getenv("FREEMODEL_API_KEY")
FREEMODEL_MODEL   = os.getenv("FREEMODEL_MODEL", "gpt-5.5")

# ── CoinGecko ─────────────────────────────────────────────────
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "")

# ── Bulk.trade ────────────────────────────────────────────────
BULK_API_URL        = os.getenv("BULK_API_URL", "https://staging-api.bulk.trade/api/v1")
BULK_WALLET_ADDRESS = os.getenv("BULK_WALLET_ADDRESS")
BULK_PRIVATE_KEY    = os.getenv("BULK_PRIVATE_KEY")

# TRADE_SIZE_USDT — default fallback agar admin amount choose na kare
# Bot mein ab admin khud approve ke waqt amount choose karta hai
# Yeh variable optional hai, sirf fallback ke liye rakha hai
TRADE_SIZE_USDT = float(os.getenv("TRADE_SIZE_USDT", "100"))

# ── Risk Settings ─────────────────────────────────────────────
RISK_MODE      = os.getenv("RISK_MODE", "HIGH")
MIN_CONFIDENCE = int(os.getenv("MIN_CONFIDENCE", "65"))

# ── Leverage map (RISK_MODE ke hisab se) ─────────────────────
#
# Exchange ke coin-specific max leverage:
#   BTC, ETH, SOL, XRP → 50x
#   SUI, BNB, ZEC      → 40x
#   FARTCOIN           → 25x
#   DOGE               → 10x
#
# RISK_MODE sirf AI ko suggest karne ke liye use hota hai —
# final clamp exchange max se hoti hai trade_executor mein.
#
# HIGH  → 80x-100x suggest (exchange max se clamp hoga — effectively 40-50x)
# MID   → 50x-70x suggest  (exchange max se clamp hoga — effectively 40-50x)
# LOW   → 20x-40x suggest  (sahi range, exchange max se match karta hai)

# Leverage suggestion range for AI
# Exchange ke coin-specific max se clamp hogi (BTC=50x, BNB=40x, DOGE=10x etc.)
# HIGH mode mein bhi AI ko 10-50x suggest karna chahiye
LEVERAGE_MAP = {
    "LOW":  {"min": 3,  "max": 10},
    "MID":  {"min": 5,  "max": 25},
    "HIGH": {"min": 10, "max": 50},
}


def validate_config():
    required = {
        "NEWS_BOT_TOKEN":         NEWS_BOT_TOKEN,
        "TELEGRAM_CHANNEL_ID":    TELEGRAM_CHANNEL_ID,
        "TRADE_BOT_TOKEN":        TRADE_BOT_TOKEN,
        "TELEGRAM_ADMIN_CHAT_ID": TELEGRAM_ADMIN_CHAT_ID,
        "OPENROUTER_API_KEY":     OPENROUTER_API_KEY,
        "FREEMODEL_API_KEY":      FREEMODEL_API_KEY,
        "BULK_WALLET_ADDRESS":    BULK_WALLET_ADDRESS,
        "BULK_PRIVATE_KEY":       BULK_PRIVATE_KEY,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise EnvironmentError(f"Missing env variables: {', '.join(missing)}")
