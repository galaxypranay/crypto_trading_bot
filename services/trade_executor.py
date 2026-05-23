import httpx
import logging
from bulk_keychain import Keypair, Signer
import config

logger = logging.getLogger(__name__)

# Bulk.trade minimum order sizes per coin
MIN_SIZE_MAP = {
    "BTC":  0.001,
    "ETH":  0.01,
    "SOL":  0.1,
    "BNB":  0.01,
    "XRP":  10.0,
    "ADA":  10.0,
    "DOGE": 100.0,
    "default": 0.01,
}


def _get_signer() -> Signer:
    private_key_b58 = config.BULK_PRIVATE_KEY
    if not private_key_b58:
        raise ValueError("BULK_PRIVATE_KEY is not set.")
    return Signer(Keypair.from_base58(private_key_b58))


def _coin_to_symbol(coin: str) -> str:
    return f"{coin.upper()}-USD"


def _calculate_size(coin: str, entry: float, usdt_amount: float) -> float:
    """USDT amount se coin size calculate karo with minimum size enforcement."""
    if entry <= 0:
        return MIN_SIZE_MAP.get(coin.upper(), MIN_SIZE_MAP["default"])
    size = round(usdt_amount / entry, 6)
    min_size = MIN_SIZE_MAP.get(coin.upper(), MIN_SIZE_MAP["default"])
    if size < min_size:
        logger.warning(f"Size {size} too small for {coin}, using minimum {min_size}")
        size = min_size
    return size


async def _set_leverage(signer: Signer, symbol: str, leverage: int) -> bool:
    """
    Leverage set karo — docs ke exact format ke saath:
    {"updateUserSettings": {"m": {"BTC-USD": 20.0}}}
    Max 50x (Bulk.trade limit)
    """
    # Bulk.trade max leverage 50x hai
    clamped = min(leverage, 50)
    if clamped != leverage:
        logger.warning(f"Leverage clamped {leverage}x → {clamped}x (Bulk.trade max is 50x)")

    try:
        tx = signer.sign_user_settings([(symbol, float(clamped))])
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{config.BULK_API_URL}/order",
                json=tx,
                headers={"Content-Type": "application/json"},
            )
        data = resp.json()
        if resp.status_code in (200, 201) and data.get("status") == "ok":
            logger.info(f"Leverage set: {symbol} → {clamped}x")
            return True
        else:
            logger.warning(f"Leverage set failed: {resp.status_code} | {data}")
            return False
    except Exception as e:
        logger.error(f"Leverage set exception: {e}")
        return False


async def execute_trade(signal: dict) -> dict:
    """
    Execute trade on Bulk.trade using correct API format from docs:

    Step 1: Set leverage via updateUserSettings
    Step 2: Market entry order (with required 'i' field)
    Step 3: Stop-loss using 'st' order type
    Step 4: Take-profit using 'tp' order type

    Docs reference:
    - Market order: {"m": {"c": "BTC-USD", "b": true, "sz": 0.1, "r": false, "i": false}}
    - Stop order:   {"st": {"c": "BTC-USD", "d": false, "sz": 0.1, "tr": 98000, "lim": 97950, "i": false}}
    - TP order:     {"tp": {"c": "BTC-USD", "d": true, "sz": 0.1, "tr": 104000, "lim": 103950, "i": false}}
    """
    coin      = signal["coin"]
    direction = signal["direction"]   # "LONG" or "SHORT"
    leverage  = int(signal["leverage"])
    entry     = float(signal["entry"])
    tp_price  = float(signal["tp"])
    sl_price  = float(signal["sl"])
    symbol    = _coin_to_symbol(coin)
    is_buy    = direction == "LONG"

    size = _calculate_size(coin, entry, config.TRADE_SIZE_USDT)
    logger.info(f"Trade: {symbol} {direction} | size={size} | entry={entry} | tp={tp_price} | sl={sl_price} | lev={leverage}x")

    try:
        signer = _get_signer()
    except ValueError as e:
        return {"success": False, "message": str(e)}

    # ── Step 1: Leverage set ──────────────────────────────────
    await _set_leverage(signer, symbol, leverage)

    results = []

    # ── Step 2: Market entry order ────────────────────────────
    # Docs: {"m": {"c": "BTC-USD", "b": true, "sz": 0.1, "r": false, "i": false}}
    entry_order = {
        "type": "order",
        "symbol": symbol,
        "is_buy": is_buy,
        "size": size,
        "order_type": {"type": "market"},
    }

    try:
        tx = signer.sign(entry_order)
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{config.BULK_API_URL}/order",
                json=tx,
                headers={"Content-Type": "application/json"},
            )
        data = resp.json()
        logger.info(f"Entry order response: {resp.status_code} | {data}")

        if resp.status_code not in (200, 201) or data.get("status") != "ok":
            return {"success": False, "message": f"Entry failed: {data}"}

        statuses = data.get("response", {}).get("data", {}).get("statuses", [])
        st = statuses[0] if statuses else {}
        if "rejectedRiskLimit" in st:
            reason = st["rejectedRiskLimit"].get("reason", "Risk limit exceeded")
            return {"success": False, "message": f"Entry rejected: {reason}"}

        results.append("✅ Entry placed")

    except Exception as e:
        logger.error(f"Entry order exception: {e}")
        return {"success": False, "message": f"Entry exception: {e}"}

    # ── Step 3: Stop-Loss ─────────────────────────────────────
    # Docs st order: d=false means trigger when price goes below/equal tr
    # LONG  → SL triggers below entry (d=false)
    # SHORT → SL triggers above entry (d=true)
    sl_direction = False if is_buy else True

    sl_order = {
        "type": "order",
        "symbol": symbol,
        "is_buy": not is_buy,          # opposite direction (reduce position)
        "size": size,
        "reduce_only": True,
        "order_type": {
            "type": "stop",
            "trigger": sl_price,
        },
    }

    try:
        tx = signer.sign(sl_order)
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{config.BULK_API_URL}/order",
                json=tx,
                headers={"Content-Type": "application/json"},
            )
        data = resp.json()
        if resp.status_code in (200, 201) and data.get("status") == "ok":
            results.append("✅ Stop-Loss placed")
            logger.info(f"SL placed at {sl_price}")
        else:
            results.append(f"⚠️ SL failed: {data}")
            logger.warning(f"SL failed: {data}")
    except Exception as e:
        results.append(f"⚠️ SL exception: {e}")
        logger.warning(f"SL exception: {e}")

    # ── Step 4: Take-Profit ───────────────────────────────────
    # Docs tp order: d=true means trigger when price goes above/equal tr
    # LONG  → TP triggers above entry (d=true)
    # SHORT → TP triggers below entry (d=false)
    tp_direction = True if is_buy else False

    tp_order = {
        "type": "order",
        "symbol": symbol,
        "is_buy": not is_buy,          # opposite direction (reduce position)
        "size": size,
        "reduce_only": True,
        "order_type": {
            "type": "take_profit",
            "trigger": tp_price,
        },
    }

    try:
        tx = signer.sign(tp_order)
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{config.BULK_API_URL}/order",
                json=tx,
                headers={"Content-Type": "application/json"},
            )
        data = resp.json()
        if resp.status_code in (200, 201) and data.get("status") == "ok":
            results.append("✅ Take-Profit placed")
            logger.info(f"TP placed at {tp_price}")
        else:
            results.append(f"⚠️ TP failed: {data}")
            logger.warning(f"TP failed: {data}")
    except Exception as e:
        results.append(f"⚠️ TP exception: {e}")
        logger.warning(f"TP exception: {e}")

    logger.info(f"Trade complete: {symbol} {direction} x{leverage} | {' | '.join(results)}")
    return {"success": True, "message": "\n".join(results)}
