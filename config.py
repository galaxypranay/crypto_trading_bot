import os
from dotenv import load_dotenv

load_dotenv()

# News Bot
NEWS_BOT_TOKEN     = os.getenv("NEWS_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

# Trade Bot
TRADE_BOT_TOKEN        = os.getenv("TRADE_BOT_TOKEN")
TELEGRAM_ADMIN_CHAT_ID = int(os.getenv("TELEGRAM_ADMIN_CHAT_ID", "0"))

# FreeModel AI (trade signal + description)
FREEMODEL_API_KEY = os.getenv("FREEMODEL_API_KEY")
FREEMODEL_MODEL   = os.getenv("FREEMODEL_MODEL", "gpt-5.5")

# Bulk.trade
BULK_API_URL        = os.getenv("BULK_API_URL", "https://staging-api.bulk.trade/api/v1")
BULK_WALLET_ADDRESS = os.getenv("BULK_WALLET_ADDRESS")
BULK_PRIVATE_KEY    = os.getenv("BULK_PRIVATE_KEY")
TRADE_SIZE_USDT     = float(os.getenv("TRADE_SIZE_USDT", "50"))

# Settings
RISK_MODE      = os.getenv("RISK_MODE", "HIGH")
MIN_CONFIDENCE = int(os.getenv("MIN_CONFIDENCE", "90"))

# Leverage map
LEVERAGE_MAP = {
    "LOW":  {"min": 3,  "max": 5},
    "MID":  {"min": 5,  "max": 10},
    "HIGH": {"min": 10, "max": 25},
}


def validate_config():
    required = {
        "NEWS_BOT_TOKEN":         NEWS_BOT_TOKEN,
        "TELEGRAM_CHANNEL_ID":    TELEGRAM_CHANNEL_ID,
        "TRADE_BOT_TOKEN":        TRADE_BOT_TOKEN,
        "TELEGRAM_ADMIN_CHAT_ID": TELEGRAM_ADMIN_CHAT_ID,
        "FREEMODEL_API_KEY":      FREEMODEL_API_KEY,
        "BULK_WALLET_ADDRESS":    BULK_WALLET_ADDRESS,
        "BULK_PRIVATE_KEY":       BULK_PRIVATE_KEY,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise EnvironmentError(f"Missing env variables: {', '.join(missing)}")
