import httpx
import json
import logging
from typing import Optional
import config

logger = logging.getLogger(__name__)

# ── Price Sources ─────────────────────────────────────────────

# CoinGecko coin ID map
COIN_GECKO_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
    "XRP": "ripple", "ADA": "cardano", "DOGE": "dogecoin",
    "BNB": "binancecoin", "AVAX": "avalanche-2", "DOT": "polkadot",
    "LINK": "chainlink", "MATIC": "matic-network", "SHIB": "shiba-inu",
    "LTC": "litecoin", "TRX": "tron", "PEPE": "pepe", "SUI": "sui",
    "APT": "aptos", "ARB": "arbitrum", "OP": "optimism",
    "INJ": "injective-protocol", "NEAR": "near", "XLM": "stellar",
    "ATOM": "cosmos", "UNI": "uniswap", "RNDR": "render-token",
}


async def get_real_price(coin: str) -> Optional[float]:
    """
    Real-time price fetch karo — 3 sources try karo in order:
    1. Bulk.trade ticker API (already allowed, no key needed)
    2. CoinGecko simple price API (free, no key)
    3. Binance public ticker API (free, no key)
    """
    symbol = f"{coin.upper()}-USD"

    # Source 1: Bulk.trade ticker (staging)
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                f"{config.BULK_API_URL}/ticker",
                params={"symbol": symbol},
                headers={"accept": "application/json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                # Bulk returns markPx or lastPx
                price = data.get("markPx") or data.get("lastPx") or data.get("price")
                if price:
                    logger.info(f"[Price] Bulk.trade [{coin}]: ${float(price):,.2f}")
                    return float(price)
    except Exception as e:
        logger.debug(f"Bulk.trade price fetch failed [{coin}]: {e}")

    # Source 2: CoinGecko
    coin_id = COIN_GECKO_IDS.get(coin.upper())
    if coin_id:
        try:
            async with httpx.AsyncClient(timeout=8, headers={
                "accept": "application/json",
                "User-Agent": "Mozilla/5.0 CryptoBot/1.0",
            }) as client:
                resp = await client.get(
                    "https://api.coingecko.com/api/v3/simple/price",
                    params={"ids": coin_id, "vs_currencies": "usd"},
                )
                if resp.status_code == 200:
                    price = resp.json().get(coin_id, {}).get("usd")
                    if price:
                        logger.info(f"[Price] CoinGecko [{coin}]: ${float(price):,.2f}")
                        return float(price)
        except Exception as e:
            logger.debug(f"CoinGecko price fetch failed [{coin}]: {e}")

    # Source 3: Binance public API
    binance_symbol = f"{coin.upper()}USDT"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": binance_symbol},
            )
            if resp.status_code == 200:
                price = resp.json().get("price")
                if price:
                    logger.info(f"[Price] Binance [{coin}]: ${float(price):,.2f}")
                    return float(price)
    except Exception as e:
        logger.debug(f"Binance price fetch failed [{coin}]: {e}")

    logger.warning(f"Could not fetch real price for {coin} from any source")
    return None


# ═══════════════════════════════════════════════════════════════
#  OPENROUTER — News Description
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
            content = response.json()["choices"][0]["message"]["content"].strip()
            return content if content else None
    except httpx.HTTPStatusError as e:
        logger.error(f"OpenRouter HTTP error: {e.response.status_code} — {e.response.text[:200]}")
        return None
    except Exception as e:
        logger.error(f"OpenRouter API error: {e}")
        return None


# ═══════════════════════════════════════════════════════════════
#  FREEMODEL — Trade Signal
# ═══════════════════════════════════════════════════════════════

SIGNAL_SYSTEM = """You are an expert crypto futures trader and market analyst with 10 years of experience.

Analyze the given crypto news and return a structured JSON trading signal.

STRICT RULES:
- Return ONLY valid JSON — no markdown, no backticks, no extra text
- If the news is NOT actionable for futures trading, return: {"tradeable": false, "reason": "brief reason"}
- direction MUST be exactly "LONG" or "SHORT"
- confidence is an integer 0-100 (be realistic, not always high)
- leverage MUST be within the given risk mode range (do not exceed max)
- For entry/tp/sl: use PERCENTAGE values only, not absolute prices
  entry_pct: 0 (always 0, means current price)
  tp_pct: positive % for LONG (e.g. 1.5 means +1.5%), negative for SHORT
  sl_pct: negative % for LONG (e.g. -1.0 means -1.0%), positive for SHORT
- reason must explain WHY this trade makes sense in one sentence

JSON format when tradeable:
{
  "tradeable": true,
  "coin": "BTC",
  "direction": "LONG",
  "confidence": 82,
  "leverage": 15,
  "entry_pct": 0,
  "tp_pct": 1.5,
  "sl_pct": -1.0,
  "reason": "ETF approval news drives strong institutional buying pressure"
}

JSON format when not tradeable:
{
  "tradeable": false,
  "reason": "General ecosystem update with no clear price catalyst"
}"""


async def _call_freemodel(user_prompt: str, max_tokens: int = 350) -> Optional[str]:
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
        "temperature": 0.2,
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
            content = response.json()["choices"][0]["message"]["content"].strip()
            return content if content else None
    except httpx.HTTPStatusError as e:
        logger.error(f"FreeModel HTTP error: {e.response.status_code} — {e.response.text[:200]}")
        return None
    except Exception as e:
        logger.error(f"FreeModel API error: {e}")
        return None


# ═══════════════════════════════════════════════════════════════
#  Public Functions
# ═══════════════════════════════════════════════════════════════

async def generate_description(news_item: dict) -> str:
    """
    OpenRouter se AI description generate karo.
    Fail/rate-limit hone pe simple fallback use karo.
    """
    if not config.OPENROUTER_API_KEY:
        return _simple_description(news_item)

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

    logger.warning(f"OpenRouter unavailable [{news_item['coin']}] — using simple description")
    return _simple_description(news_item)


def _simple_description(news_item: dict) -> str:
    """OpenRouter ke bina simple channel description."""
    coin  = news_item["coin"]
    desc  = news_item.get("description", "").strip()
    body  = (desc[:280] + "...") if len(desc) > 280 else desc
    if not body:
        body = news_item["title"]
    source = news_item.get("source", "")
    lines = [f"#{coin} update 📌", "", body]
    if source:
        lines.append(f"\n📡 {source}")
    return "\n".join(lines)


async def analyze_news(news_item: dict) -> Optional[dict]:
    """
    FreeModel se trade signal generate karo.
    AI percentage values deta hai — real price se actual levels calculate karo.
    """
    leverage_range = config.LEVERAGE_MAP.get(config.RISK_MODE, config.LEVERAGE_MAP["HIGH"])

    # Pehle real price fetch karo
    coin = news_item["coin"]
    real_price = await get_real_price(coin)

    user_msg = (
        f"Risk Mode: {config.RISK_MODE}\n"
        f"Allowed Leverage: {leverage_range['min']}x to {leverage_range['max']}x (max {leverage_range['max']}x)\n"
        f"Current {coin} Price: ${real_price:,.2f}\n\n" if real_price else
        f"Risk Mode: {config.RISK_MODE}\n"
        f"Allowed Leverage: {leverage_range['min']}x to {leverage_range['max']}x\n\n"
    ) + (
        f"Coin: {coin}\n"
        f"News Title: {news_item['title']}\n"
        f"Details: {news_item.get('description', 'N/A')}\n"
        f"Source: {news_item.get('source', 'Unknown')}\n"
        f"Published: {news_item['published_at'].strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        f"Return tp_pct and sl_pct as percentage values (e.g. tp_pct=1.5 means +1.5%)."
    )

    raw = await _call_freemodel(user_msg, max_tokens=350)
    if not raw:
        return None

    # JSON extract karo
    raw = raw.replace("```json", "").replace("```", "").strip()
    start = raw.find("{")
    end   = raw.rfind("}") + 1
    if start == -1 or end == 0:
        logger.error(f"FreeModel: no JSON found: {raw[:100]}")
        return None
    raw = raw[start:end]

    try:
        signal = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"FreeModel invalid JSON: {e} | raw: {raw[:200]}")
        return None

    if not signal.get("tradeable"):
        signal["news_title"]  = news_item["title"]
        signal["news_url"]    = news_item["url"]
        signal["news_source"] = news_item.get("source", "Unknown")
        signal["coin"]        = coin
        return signal

    # Leverage clamp — max 50x (Bulk.trade hard limit)
    if "leverage" in signal:
        lev = int(signal["leverage"])
        clamped = max(leverage_range["min"], min(lev, 50))
        signal["leverage"] = clamped
        if lev != clamped:
            logger.warning(f"Leverage clamped: {lev}x → {clamped}x")

    # ── Real price se entry/tp/sl calculate karo ──────────────
    if real_price:
        tp_pct = float(signal.get("tp_pct", 1.5)) / 100
        sl_pct = float(signal.get("sl_pct", -1.0)) / 100

        # Direction validate karo
        direction = signal.get("direction", "LONG")
        if direction == "LONG":
            if tp_pct <= 0:
                tp_pct = abs(tp_pct)   # LONG mein TP positive hona chahiye
            if sl_pct >= 0:
                sl_pct = -abs(sl_pct)  # LONG mein SL negative hona chahiye
        else:  # SHORT
            if tp_pct >= 0:
                tp_pct = -abs(tp_pct)  # SHORT mein TP negative hona chahiye
            if sl_pct <= 0:
                sl_pct = abs(sl_pct)   # SHORT mein SL positive hona chahiye

        signal["entry"] = real_price
        signal["tp"]    = round(real_price * (1 + tp_pct), 2)
        signal["sl"]    = round(real_price * (1 + sl_pct), 2)

        logger.info(
            f"Signal [{coin}] {direction} | Price=${real_price:,.2f} | "
            f"TP={signal['tp']} (+{tp_pct*100:.2f}%) | SL={signal['sl']} ({sl_pct*100:.2f}%)"
        )
    else:
        # Fallback: AI ke old-style absolute prices use karo (agar diye hain)
        logger.warning(f"Using AI absolute prices for {coin} — real price unavailable")
        if not signal.get("entry"):
            signal["entry"] = signal.get("entry", 0)

    # News metadata
    signal["news_title"]  = news_item["title"]
    signal["news_url"]    = news_item["url"]
    signal["news_source"] = news_item.get("source", "Unknown")
    signal["coin"]        = signal.get("coin") or coin

    return signal


def is_signal_valid(signal: dict) -> bool:
    """Signal minimum requirements check karo."""
    if not signal or not signal.get("tradeable"):
        return False
    if signal.get("confidence", 0) < config.MIN_CONFIDENCE:
        return False
    if signal.get("direction") not in ("LONG", "SHORT"):
        return False
    required = ["coin", "direction", "confidence", "leverage", "entry", "tp", "sl"]
    if not all(signal.get(f) for f in required):
        return False
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
