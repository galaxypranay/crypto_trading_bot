import os
from dotenv import load_dotenv

load_dotenv()

# News Bot
NEWS_BOT_TOKEN = os.getenv("NEWS_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

# Trade Bot
TRADE_BOT_TOKEN = os.getenv("TRADE_BOT_TOKEN")
TELEGRAM_ADMIN_CHAT_ID = int(os.getenv("TELEGRAM_ADMIN_CHAT_ID", "0"))

# OpenRouter
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
AI_MODEL = os.getenv("AI_MODEL", "deepseek/deepseek-chat")

# Bulk.trade
BULK_API_URL     = os.getenv("BULK_API_URL", "https://staging-api.bulk.trade/api/v1")
BULK_WALLET_ADDRESS = os.getenv("BULK_WALLET_ADDRESS")   # Your public key (base58)
BULK_PRIVATE_KEY    = os.getenv("BULK_PRIVATE_KEY")       # Your private key (base58, 64 bytes)
TRADE_SIZE_USDT  = float(os.getenv("TRADE_SIZE_USDT", "50"))  # USD per trade

# Settings
RISK_MODE       = os.getenv("RISK_MODE", "HIGH")
MIN_CONFIDENCE  = int(os.getenv("MIN_CONFIDENCE", "90"))

# Leverage map based on risk mode
LEVERAGE_MAP = {
    "LOW":  {"min": 3,  "max": 5},
    "MID":  {"min": 5,  "max": 10},
    "HIGH": {"min": 10, "max": 25},
}

# Free crypto RSS feeds (no API key needed)
NEWS_RSS_URLS = [
    "https://feeds.feedburner.com/CoinDesk",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
    "https://cryptonews.com/news/feed/",
]


def validate_config():
    required = {
        "NEWS_BOT_TOKEN":         NEWS_BOT_TOKEN,
        "TELEGRAM_CHANNEL_ID":    TELEGRAM_CHANNEL_ID,
        "TRADE_BOT_TOKEN":        TRADE_BOT_TOKEN,
        "TELEGRAM_ADMIN_CHAT_ID": TELEGRAM_ADMIN_CHAT_ID,
        "OPENROUTER_API_KEY":     OPENROUTER_API_KEY,
        "BULK_WALLET_ADDRESS":    BULK_WALLET_ADDRESS,
        "BULK_PRIVATE_KEY":       BULK_PRIVATE_KEY,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise EnvironmentError(f"Missing env variables: {', '.join(missing)}")
