import httpx
import json
import logging
from typing import Optional
import config

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert crypto futures trader and market analyst.

Analyze the given crypto news and return a structured JSON trading signal.

Rules:
- Only return JSON, no extra text, no markdown, no backticks
- If news is not actionable for futures trading, return {"tradeable": false}
- direction must be "LONG" or "SHORT"
- confidence is 0-100 (integer)
- leverage depends on risk mode provided
- entry, tp, sl are approximate price levels based on recent market context
- reason must be 1 short sentence explaining why

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
  "reason": "ETF approval news typically drives strong bullish momentum for BTC"
}

JSON format when not tradeable:
{
  "tradeable": false,
  "reason": "General market news with no clear directional signal"
}
"""


async def analyze_news(news_item: dict) -> Optional[dict]:
    """Send news to OpenRouter AI and get a trade signal."""

    leverage_range = config.LEVERAGE_MAP.get(config.RISK_MODE, config.LEVERAGE_MAP["HIGH"])

    user_message = f"""
Risk Mode: {config.RISK_MODE}
Leverage range for this risk mode: {leverage_range['min']}x to {leverage_range['max']}x

News Title: {news_item['title']}
Source: {news_item['source']}
Summary: {news_item.get('summary', 'N/A')}
Coin mentioned: {news_item.get('coin', 'Unknown')}

Analyze this news and return a trading signal JSON.
"""

    headers = {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://crypto-trading-bot.app",
        "X-Title": "Crypto Trading Bot",
    }

    payload = {
        "model": config.AI_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.3,
        "max_tokens": 300,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        raw_text = data["choices"][0]["message"]["content"].strip()

        # Clean any accidental markdown
        raw_text = raw_text.replace("```json", "").replace("```", "").strip()

        signal = json.loads(raw_text)
        signal["news_title"] = news_item["title"]
        signal["news_url"] = news_item["url"]
        signal["news_source"] = news_item["source"]

        return signal

    except json.JSONDecodeError as e:
        logger.error(f"AI returned invalid JSON: {e} | raw: {raw_text}")
        return None
    except Exception as e:
        logger.error(f"OpenRouter API error: {e}")
        return None


def is_signal_valid(signal: dict) -> bool:
    """Check if signal meets our minimum confidence threshold."""
    if not signal:
        return False
    if not signal.get("tradeable"):
        return False
    if signal.get("confidence", 0) < config.MIN_CONFIDENCE:
        return False
    required_fields = ["coin", "direction", "confidence", "leverage", "entry", "tp", "sl"]
    return all(signal.get(f) for f in required_fields)


def pick_best_signal(signals: list[dict]) -> Optional[dict]:
    """From multiple signals, return the one with highest confidence."""
    valid = [s for s in signals if is_signal_valid(s)]
    if not valid:
        return None
    return max(valid, key=lambda s: s["confidence"])
