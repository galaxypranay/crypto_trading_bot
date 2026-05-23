import httpx
import json
import logging
from typing import Optional
import config

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
#  OPENROUTER — News Description (channel post ke liye)
# ═══════════════════════════════════════════════════════════════

DESCRIPTION_SYSTEM = """You are a professional crypto news writer for a Telegram trading channel.

Your job: Write a punchy, informative 3-4 line description of the given crypto news article.

Rules:
- Start with the most important fact
- Mention the coin name and what happened
- Add market impact or why traders should care
- Use simple, clear language — no jargon
- Add 2-3 relevant emojis naturally in the text
- NO hashtags, NO "breaking news", NO "stay tuned" clichés
- NO links, NO source mentions
- Max 120 words

Return ONLY the description text, nothing else."""


async def _call_openrouter(user_prompt: str, max_tokens: int = 200) -> Optional[str]:
    """OpenRouter API call — news description generate karne ke liye."""
    headers = {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://crypto-trading-bot.app",
        "X-Title": "Crypto Trading Bot",
    }
    payload = {
        "model": config.OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": DESCRIPTION_SYSTEM},
            {"role": "user",   "content": user_prompt},
        ],
        "temperature": 0.7,
        "max_tokens": max_tokens,
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
            content = data["choices"][0]["message"]["content"].strip()
            return content if content else None
    except httpx.HTTPStatusError as e:
        logger.error(f"OpenRouter HTTP error: {e.response.status_code} — {e.response.text[:200]}")
        return None
    except Exception as e:
        logger.error(f"OpenRouter API error: {e}")
        return None


# ═══════════════════════════════════════════════════════════════
#  FREEMODEL — Trade Signal (admin ke liye)
# ═══════════════════════════════════════════════════════════════

SIGNAL_SYSTEM = """You are an expert crypto futures trader and market analyst with 10 years of experience.

Analyze the given crypto news and return a structured JSON trading signal.

STRICT RULES:
- Return ONLY valid JSON — no markdown, no backticks, no extra text
- If the news is NOT actionable for futures trading, return: {"tradeable": false, "reason": "brief reason"}
- direction MUST be exactly "LONG" or "SHORT"
- confidence is an integer 0-100 (be realistic, not always high)
- leverage MUST be within the given risk mode range (do not exceed max)
- entry, tp, sl must be realistic current market price levels
- tp must be in profit direction, sl must limit loss
- reason must explain WHY this trade makes sense in one sentence
- Consider news sentiment, market context, and risk carefully

JSON format when tradeable:
{
  "tradeable": true,
  "coin": "BTC",
  "direction": "LONG",
  "confidence": 82,
  "leverage": 15,
  "entry": 67500,
  "tp": 69200,
  "sl": 66500,
  "reason": "ETF approval news drives strong institutional buying pressure"
}

JSON format when not tradeable:
{
  "tradeable": false,
  "reason": "General ecosystem update with no clear price catalyst"
}"""


async def _call_freemodel(user_prompt: str, max_tokens: int = 350) -> Optional[str]:
    """FreeModel API call — trade signal generate karne ke liye."""
    headers = {
        "Authorization": f"Bearer {config.FREEMODEL_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.FREEMODEL_MODEL,
        "messages": [
            {"role": "system", "content": SIGNAL_SYSTEM},
            {"role": "user",   "content": user_prompt},
        ],
        "temperature": 0.2,   # Low temperature — consistent structured output
        "max_tokens": max_tokens,
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://api.freemodel.dev/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"].strip()
            return content if content else None
    except httpx.HTTPStatusError as e:
        logger.error(f"FreeModel HTTP error: {e.response.status_code} — {e.response.text[:200]}")
        return None
    except Exception as e:
        logger.error(f"FreeModel API error: {e}")
        return None


# ═══════════════════════════════════════════════════════════════
#  Public functions
# ═══════════════════════════════════════════════════════════════

async def generate_description(news_item: dict) -> str:
    """
    OpenRouter se news ka punchy channel description generate karo.
    Fallback: original title + description use karo.
    """
    user_msg = (
        f"Coin: {news_item['coin']}\n"
        f"News Title: {news_item['title']}\n"
        f"Details: {news_item.get('description', 'No details available')}\n"
        f"Source: {news_item.get('source', 'Unknown')}"
    )
    result = await _call_openrouter(user_msg, max_tokens=200)
    if result:
        logger.info(f"OpenRouter description generated for [{news_item['coin']}]")
        return result

    # Fallback — original content use karo
    logger.warning(f"OpenRouter failed, using fallback for [{news_item['coin']}]")
    desc = news_item.get("description", "")
    return desc if desc else news_item["title"]


async def analyze_news(news_item: dict) -> Optional[dict]:
    """
    FreeModel se trade signal generate karo.
    Returns: signal dict (with tradeable flag) ya None on error.
    """
    leverage_range = config.LEVERAGE_MAP.get(config.RISK_MODE, config.LEVERAGE_MAP["HIGH"])

    user_msg = (
        f"Risk Mode: {config.RISK_MODE}\n"
        f"Allowed Leverage: {leverage_range['min']}x to {leverage_range['max']}x (DO NOT exceed {leverage_range['max']}x)\n\n"
        f"Coin: {news_item['coin']}\n"
        f"News Title: {news_item['title']}\n"
        f"Details: {news_item.get('description', 'N/A')}\n"
        f"Source: {news_item.get('source', 'Unknown')}\n"
        f"Published: {news_item['published_at'].strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        f"Analyze this news and return a trading signal JSON."
    )

    raw = await _call_freemodel(user_msg, max_tokens=350)
    if not raw:
        return None

    # Markdown cleanup (kuch models backticks add kar dete hain)
    raw = raw.replace("```json", "").replace("```", "").strip()
    # JSON ke pehle/baad extra text remove karo
    start = raw.find("{")
    end   = raw.rfind("}") + 1
    if start == -1 or end == 0:
        logger.error(f"FreeModel: no JSON found in response: {raw[:100]}")
        return None
    raw = raw[start:end]

    try:
        signal = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"FreeModel invalid JSON: {e} | raw: {raw[:200]}")
        return None

    # Leverage clamp — AI ne range se bahar value di toh fix karo
    if signal.get("tradeable") and "leverage" in signal:
        lev = int(signal["leverage"])
        signal["leverage"] = max(leverage_range["min"], min(lev, leverage_range["max"]))
        if lev != signal["leverage"]:
            logger.warning(f"Leverage clamped: {lev}x → {signal['leverage']}x")

    # News metadata attach karo
    signal["news_title"]  = news_item["title"]
    signal["news_url"]    = news_item["url"]
    signal["news_source"] = news_item.get("source", "Unknown")
    signal["coin"]        = signal.get("coin") or news_item["coin"]

    return signal


def is_signal_valid(signal: dict) -> bool:
    """Signal minimum requirements check karo."""
    if not signal or not signal.get("tradeable"):
        return False
    if signal.get("confidence", 0) < config.MIN_CONFIDENCE:
        return False
    # Direction valid hai?
    if signal.get("direction") not in ("LONG", "SHORT"):
        return False
    # Required fields present hain?
    required = ["coin", "direction", "confidence", "leverage", "entry", "tp", "sl"]
    if not all(signal.get(f) for f in required):
        return False
    # TP/SL direction check
    entry = float(signal["entry"])
    tp    = float(signal["tp"])
    sl    = float(signal["sl"])
    if signal["direction"] == "LONG":
        if tp <= entry or sl >= entry:
            logger.warning(f"Invalid LONG levels: entry={entry} tp={tp} sl={sl}")
            return False
    else:
        if tp >= entry or sl <= entry:
            logger.warning(f"Invalid SHORT levels: entry={entry} tp={tp} sl={sl}")
            return False
    return True


def pick_best_signal(signals: list[dict]) -> Optional[dict]:
    """Sabse zyada confidence wala valid signal return karo."""
    valid = [s for s in signals if is_signal_valid(s)]
    if not valid:
        return None
    return max(valid, key=lambda s: s["confidence"])
