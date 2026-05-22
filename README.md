# 🤖 AI Crypto News Trading Bot

AI-powered crypto futures trading bot that:
- Fetches real-time crypto news from RSS feeds
- Posts every news article to your Telegram channel automatically
- Analyzes news with AI (OpenRouter) to generate trade signals
- Sends best signal (highest confidence) to your private Trade Bot
- You approve or reject — then trade executes on Bulk.trade

---

## 📁 Project Structure

```
crypto_trading_bot/
├── main.py                  # FastAPI app + scheduler entry point
├── pipeline.py              # Main orchestration logic
├── config.py                # All environment variable loading
├── requirements.txt         # Python dependencies
├── Procfile                 # Railway start command
├── railway.json             # Railway config
├── .env.example             # Template for your .env file
│
├── services/
│   ├── news_fetcher.py      # Fetches news from RSS feeds
│   ├── ai_analyzer.py       # OpenRouter AI signal generation
│   └── trade_executor.py    # Bulk.trade API execution
│
└── handlers/
    ├── news_bot.py          # Posts news to Telegram channel
    └── trade_bot.py         # Sends signals + handles Approve/Reject
```

---

## ⚙️ Setup

### Step 1 — Create Two Telegram Bots

Go to [@BotFather](https://t.me/BotFather) on Telegram:

1. Send `/newbot` → create **News Bot** → save token as `NEWS_BOT_TOKEN`
2. Send `/newbot` → create **Trade Bot** → save token as `TRADE_BOT_TOKEN`

### Step 2 — Create Telegram Channel

1. Create a Telegram channel (public or private)
2. Go to channel → Settings → Administrators → Add Admin
3. Search for your **News Bot** → add it → give "Post Messages" permission
4. Your channel ID = `@your_channel_name` (public) or numeric ID (private)

### Step 3 — Get Your Admin Chat ID

1. Open [@userinfobot](https://t.me/userinfobot) on Telegram
2. Send `/start` → it will show your numeric chat ID
3. Save it as `TELEGRAM_ADMIN_CHAT_ID`

### Step 4 — Get OpenRouter API Key

1. Go to [openrouter.ai](https://openrouter.ai)
2. Sign up → API Keys → Create Key
3. Save as `OPENROUTER_API_KEY`

### Step 5 — Set Environment Variables

Copy `.env.example` to `.env` and fill in all values:

```bash
cp .env.example .env
```

```env
NEWS_BOT_TOKEN=111111:AAA...
TELEGRAM_CHANNEL_ID=@your_channel

TRADE_BOT_TOKEN=222222:BBB...
TELEGRAM_ADMIN_CHAT_ID=987654321

OPENROUTER_API_KEY=sk-or-...
AI_MODEL=deepseek/deepseek-chat

BULK_API_KEY=your_bulk_key
BULK_API_URL=https://api.bulk.trade

RISK_MODE=HIGH
MIN_CONFIDENCE=90
```

---

## 🚀 Run Locally

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

---

## 🚂 Deploy on Railway

1. Push this folder to a GitHub repo
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Add all environment variables in Railway dashboard → Variables tab
4. Deploy — bot starts automatically

---

## 🔁 How It Works

```
Every 2 minutes:
  CoinGecko RSS / Crypto RSS feeds
       ↓
  Filter only new articles
       ↓
  ┌─────────────────────────────┐
  │  For each new article:      │
  │  1. Post to News Channel    │
  │  2. AI analyzes for signal  │
  └─────────────────────────────┘
       ↓
  Pick best signal (confidence ≥ 90%)
       ↓
  Send to Trade Bot → Admin sees Approve/Reject
       ↓
  On Approve → Bulk.trade executes trade
  On Reject  → Signal dropped silently
```

---

## 📡 API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | Bot status and config |
| `GET /health` | Health check (for Railway) |
| `GET /status` | Scheduler info |
| `POST /trigger` | Manually trigger pipeline (for testing) |

---

## ⚠️ Risk Modes

| Mode | Leverage Range |
|------|---------------|
| LOW  | 3x – 5x |
| MID  | 5x – 10x |
| HIGH | 10x – 25x |

Change `RISK_MODE` in Railway variables anytime — no code change needed.

---

## 🛡️ Safety Features

- ✅ Manual approval before every trade
- ✅ Minimum 90% confidence filter
- ✅ Duplicate news protection (seen IDs tracked)
- ✅ Only one pipeline runs at a time
- ✅ TP + SL mandatory on every trade
- ✅ Error alerts sent to admin via Trade Bot
- ✅ AI response validation before use

---

## 🤖 Supported AI Models (OpenRouter)

Change `AI_MODEL` in Railway variables:

- `deepseek/deepseek-chat` (recommended, fast & cheap)
- `google/gemini-flash-1.5`
- `qwen/qwen-2.5-72b-instruct`
- `anthropic/claude-3-haiku`
- Any model on [openrouter.ai/models](https://openrouter.ai/models)
