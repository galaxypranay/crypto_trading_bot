import httpx
import json
import logging
from typing import Optional
import config

logger = logging.getLogger(__name__)

# ── Prompt 1: Channel description writer ─────────────────────
DESCRIPTION_SYSTEM = """You are a crypto news summarizer for a Telegram trading channel.

Write a short, punchy 2-3 line description of the news for traders.
- Mention the coin and what happened
- Use simple language, no jargon
- Add 1-2 relevant emojis
- NO hashtags, NO links, NO "breaking news" clichés
- Max 100 words

Return only the description text, nothing else."""


# ── Prompt 2: Trade signal generator ─────────────────────────
SIGNAL_SYSTEM = """You are an expert crypto futures trader and market analyst.

Analyze the given crypto news and return a structured JSON trading signal.

Rules:
- Only return JSON, no extra text, no markdown, no backticks
- If news is not actionable for futures trading, return {"tradeable": false, "reason": "..."}
- direction must be "LONG" or "SHORT"
- confidence is 0-100 (integer)
- leverage must be within the given risk mode range
- entry, tp, sl are realistic price levels based on market context
- reason must be 1 short sentence

JSON format when tradeable:
{
  "tradeable": true,
  "coin": "BTC",
  "direction": "LONG",
  "confidence": 91,
  "leverage": 20,
  "entry": 77819,
  "tp": 78500,
  "sl": 77200,
  "reason": "ETF approval drives strong BTC bullish momentum"
}

JSON format when not tradeable:
{
  "tradeable": false,
  "reason": "General market news with no clear directional signal"
}"""


async def _call_freemodel(system: str, user: str, max_tokens: int = 300) -> Optional[str]:
    """Call FreeModel API (OpenAI-compatible format)."""
    headers = {
        "Authorization": f"Bearer {config.FREEMODEL_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.FREEMODEL_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        "temperature": 0.3,
        "max_tokens": max_tokens,
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://freemodel.dev/api/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"FreeModel API error: {e}")
        return None


async def generate_description(news_item: dict) -> str:
    """
    OpenRouter → FreeModel se short channel description generate karo.
    Fallback: original title use karo.
    """
    user_msg = (
        f"Coin: {news_item['coin']}\n"
        f"Title: {news_item['title']}\n"
        f"Details: {news_item.get('description', '')}"
    )
    result = await _call_freemodel(DESCRIPTION_SYSTEM, user_msg, max_tokens=150)
    if result:
        return result
    # Fallback
    return news_item.get("description", news_item["title"])


async def analyze_news(news_item: dict) -> Optional[dict]:
    """
    FreeModel se trade signal generate karo.
    Returns signal dict with tradeable flag.
    """
    leverage_range = config.LEVERAGE_MAP.get(config.RISK_MODE, config.LEVERAGE_MAP["HIGH"])

    user_msg = (
        f"Risk Mode: {config.RISK_MODE}\n"
        f"Leverage range: {leverage_range['min']}x to {leverage_range['max']}x\n\n"
        f"Coin: {news_item['coin']}\n"
        f"News Title: {news_item['title']}\n"
        f"Details: {news_item.get('description', 'N/A')}\n"
        f"Source: {news_item['source']}\n\n"
        f"Analyze and return trading signal JSON."
    )

    raw = await _call_freemodel(SIGNAL_SYSTEM, user_msg, max_tokens=300)
    if not raw:
        return None

    # Clean accidental markdown
    raw = raw.replace("```json", "").replace("```", "").strip()

    try:
        signal = json.loads(raw)
        signal["news_title"]  = news_item["title"]
        signal["news_url"]    = news_item["url"]
        signal["news_source"] = news_item["source"]
        return signal
    except json.JSONDecodeError as e:
        logger.error(f"FreeModel invalid JSON: {e} | raw: {raw}")
        return None


def is_signal_valid(signal: dict) -> bool:
    """Check signal meets minimum confidence threshold."""
    if not signal or not signal.get("tradeable"):
        return False
    if signal.get("confidence", 0) < config.MIN_CONFIDENCE:
        return False
    required = ["coin", "direction", "confidence", "leverage", "entry", "tp", "sl"]
    return all(signal.get(f) for f in required)


def pick_best_signal(signals: list[dict]) -> Optional[dict]:
    """Return highest-confidence valid signal."""
    valid = [s for s in signals if is_signal_valid(s)]
    if not valid:
        return None
    return max(valid, key=lambda s: s["confidence"])
