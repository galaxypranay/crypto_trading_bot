import httpx
import logging
from bulk_keychain import Keypair, Signer
import config

logger = logging.getLogger(__name__)

# ── early.bulk.trade supported coins ─────────────────────────
# max_leverage = exchange ka hard limit per coin
SUPPORTED_COINS = {
    "BTC":      {"max_leverage": 50,  "min_size": 0.001},
    "ETH":      {"max_leverage": 50,  "min_size": 0.01},
    "SOL":      {"max_leverage": 50,  "min_size": 0.1},
    "XRP":      {"max_leverage": 50,  "min_size": 10.0},
    "SUI":      {"max_leverage": 40,  "min_size": 10.0},
    "BNB":      {"max_leverage": 40,  "min_size": 0.01},
    "ZEC":      {"max_leverage": 40,  "min_size": 0.01},
    "DOGE":     {"max_leverage": 10,  "min_size": 100.0},
    "FARTCOIN": {"max_leverage": 25,  "min_size": 10.0},
}


def _get_signer() -> Signer:
    pk = config.BULK_PRIVATE_KEY
    if not pk:
        raise ValueError("BULK_PRIVATE_KEY is not set.")
    return Signer(Keypair.from_base58(pk))


def _coin_to_symbol(coin: str) -> str:
    return f"{coin.upper()}-USD"


def _calculate_size(coin: str, entry: float, usd_amount: float, leverage: int) -> float:
    """
    USD amount se coin size calculate karo.

    Effective leverage check:
      notional_value = size * entry_price
      effective_leverage = notional_value / usd_amount

    Ye effective_leverage coin max leverage se zyada nahi honi chahiye.
    Agar ho toh size reduce karo.
    """
    min_size = SUPPORTED_COINS.get(coin.upper(), {}).get("min_size", 0.01)
    coin_max_lev = SUPPORTED_COINS.get(coin.upper(), {}).get("max_leverage", 50)

    if entry <= 0:
        return min_size

    # Basic size from USD amount
    size = round(usd_amount / entry, 6)

    # Effective leverage check karo
    # notional = size * entry
    # margin_required = notional / leverage
    # effective_lev = notional / usd_amount
    notional = size * entry
    effective_lev = notional / usd_amount if usd_amount > 0 else leverage

    # Agar effective leverage coin max se zyada ho toh size ghata do
    if effective_lev > coin_max_lev:
        max_notional = usd_amount * coin_max_lev * 0.9  # 10% safety buffer
        size = round(max_notional / entry, 6)
        logger.warning(
            f"Size reduced: effective_lev={effective_lev:.1f}x > coin_max={coin_max_lev}x | "
            f"new size={size} ({coin})"
        )

    if size < min_size:
        logger.warning(f"Size {size} too small for {coin}, using min {min_size}")
        size = min_size

    logger.info(f"Size calc [{coin}]: $usd_amount USD / {entry} = {size} coins | notional=${size*entry:.2f}")
    return size


def _clamp_leverage(coin: str, leverage: int) -> int:
    """
    Leverage 3 jagah se clamp karo:
    1. RISK_MODE ka max (config.py mein)
    2. Coin ka exchange max (SUPPORTED_COINS mein)
    3. Hard limit 50x
    """
    risk_max  = config.LEVERAGE_MAP.get(config.RISK_MODE, {}).get("max", 50)
    coin_max  = SUPPORTED_COINS.get(coin.upper(), {}).get("max_leverage", 50)
    effective = min(leverage, risk_max, coin_max, 50)
    if effective != leverage:
        logger.warning(
            f"Leverage clamped: {leverage}x → {effective}x "
            f"(risk_max={risk_max}, coin_max={coin_max})"
        )
    return effective


async def _set_leverage(signer: Signer, symbol: str, leverage: int) -> bool:
    try:
        tx = signer.sign_user_settings([(symbol, float(leverage))])
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{config.BULK_API_URL}/order",
                json=tx,
                headers={"Content-Type": "application/json"},
            )
        data = resp.json()
        if resp.status_code in (200, 201) and data.get("status") == "ok":
            logger.info(f"Leverage set: {symbol} → {leverage}x")
            return True
        logger.warning(f"Leverage set failed: {resp.status_code} | {data}")
        return False
    except Exception as e:
        logger.error(f"Leverage set exception: {e}")
        return False


async def execute_trade(signal: dict) -> dict:
    """
    Execute bracket trade on early.bulk.trade.

    Trade size:
      - signal['trade_size_usd'] — admin ne approve ke waqt choose kiya
      - fallback: config.TRADE_SIZE_USD (Railway variable)

    Steps:
      1. Coin supported check
      2. Leverage clamp (risk mode + coin max)
      3. Leverage set (sign_user_settings)
      4. Bracket order atomic (sign_group):
         - Entry:  market order
         - SL:     reduce_only GTC limit
         - TP:     reduce_only GTC limit
    """
    coin      = signal["coin"].upper()
    direction = signal["direction"]
    leverage  = int(signal["leverage"])
    entry     = float(signal["entry"])
    tp_price  = float(signal["tp"])
    sl_price  = float(signal["sl"])
    symbol    = _coin_to_symbol(coin)
    is_buy    = direction == "LONG"

    # Admin ka chosen amount, fallback to config
    usd_amount = float(signal.get("trade_size_usd") or config.TRADE_SIZE_USD)

    # ── Coin supported check ──────────────────────────────────
    if coin not in SUPPORTED_COINS:
        msg = (
            f"{coin} early.bulk.trade pe available nahi.\n"
            f"Supported: {', '.join(SUPPORTED_COINS.keys())}"
        )
        logger.error(msg)
        return {"success": False, "message": msg}

    leverage = _clamp_leverage(coin, leverage)
    size     = _calculate_size(coin, entry, usd_amount, leverage)

    logger.info(
        f"Trade: {symbol} {direction} x{leverage} | "
        f"size={size} | entry={entry} | tp={tp_price} | sl={sl_price} | "
        f"usd=${usd_amount}"
    )

    try:
        signer = _get_signer()
    except ValueError as e:
        return {"success": False, "message": str(e)}

    # ── Leverage set ──────────────────────────────────────────
    await _set_leverage(signer, symbol, leverage)

    # ── Bracket order ─────────────────────────────────────────
    orders = [
        {
            "type":       "order",
            "symbol":     symbol,
            "is_buy":     is_buy,
            "price":      entry,
            "size":       size,
            "order_type": {"type": "market"},
        },
        {
            "type":       "order",
            "symbol":     symbol,
            "is_buy":     not is_buy,
            "price":      sl_price,
            "size":       size,
            "reduce_only": True,
            "order_type": {"type": "limit", "tif": "GTC"},
        },
        {
            "type":       "order",
            "symbol":     symbol,
            "is_buy":     not is_buy,
            "price":      tp_price,
            "size":       size,
            "reduce_only": True,
            "order_type": {"type": "limit", "tif": "GTC"},
        },
    ]

    try:
        bracket_tx = signer.sign_group(orders)
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{config.BULK_API_URL}/order",
                json=bracket_tx,
                headers={"Content-Type": "application/json"},
            )

        # Empty response body handle karo
        raw_text = resp.text.strip()
        logger.info(f"Bracket response: {resp.status_code} | body={raw_text[:300]}")

        if not raw_text:
            return {
                "success": False,
                "message": f"Exchange returned empty response (HTTP {resp.status_code}). Account mein funds nahi hain ya staging issue hai."
            }

        try:
            data = resp.json()
        except Exception as json_err:
            return {
                "success": False,
                "message": f"Invalid JSON response (HTTP {resp.status_code}): {raw_text[:200]}"
            }

        if resp.status_code not in (200, 201):
            return {"success": False, "message": f"API error {resp.status_code}: {data}"}

        if data.get("status") != "ok":
            return {"success": False, "message": f"Trade rejected: {data}"}

        statuses = data.get("response", {}).get("data", {}).get("statuses", [])
        labels   = ["Entry", "Stop-Loss", "Take-Profit"]
        SUCCESS  = {"filled", "resting", "working", "partiallyFilled"}
        FAILURE  = {"rejectedRiskLimit", "rejectedInvalid", "rejectedCrossing",
                    "rejectedDuplicate", "cancelledRiskLimit", "error"}

        lines           = []
        overall_success = True

        for i, st in enumerate(statuses):
            label      = labels[i] if i < len(labels) else f"Order {i+1}"
            status_key = list(st.keys())[0] if st else "unknown"
            status_val = st.get(status_key, {})

            if status_key in SUCCESS:
                lines.append(f"✅ {label} placed")
            elif status_key in FAILURE:
                reason = (status_val.get("reason") or status_val.get("message") or "") \
                         if isinstance(status_val, dict) else ""
                lines.append(f"❌ {label} failed: {status_key} — {reason}")
                if i == 0:
                    overall_success = False
                logger.error(f"{label} rejected: {status_key} — {reason}")
            elif status_key == "cancelled":
                lines.append(f"⚠️ {label} cancelled")
            else:
                lines.append(f"ℹ️ {label}: {status_key}")

        logger.info(
            f"Trade done: {symbol} {direction} x{leverage} ${usd_amount} | "
            f"success={overall_success}"
        )
        return {"success": overall_success, "message": "\n".join(lines)}

    except Exception as e:
        logger.error(f"Bracket order exception: {e}")
        return {"success": False, "message": f"Trade exception: {e}"}
