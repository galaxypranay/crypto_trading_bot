import httpx
import logging
from bulk_keychain import Keypair, Signer
import config

logger = logging.getLogger(__name__)

# Bulk.trade minimum order sizes (approximate)
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
        raise ValueError("BULK_PRIVATE_KEY is not set in environment variables.")
    keypair = Keypair.from_base58(private_key_b58)
    return Signer(keypair)


def _coin_to_symbol(coin: str) -> str:
    return f"{coin.upper()}-USD"


def _calculate_size(coin: str, entry: float, usdt_amount: float) -> float:
    """
    USDT amount se coin size calculate karo.
    Minimum size check bhi karo.
    """
    if entry <= 0:
        return MIN_SIZE_MAP.get(coin.upper(), MIN_SIZE_MAP["default"])

    size = round(usdt_amount / entry, 6)

    # Minimum size enforce karo
    min_size = MIN_SIZE_MAP.get(coin.upper(), MIN_SIZE_MAP["default"])
    if size < min_size:
        logger.warning(
            f"Size {size} too small for {coin}, using minimum {min_size}"
        )
        size = min_size

    return size


async def execute_trade(signal: dict) -> dict:
    """
    Execute a bracket trade on Bulk.trade:
      Step 1 — Leverage set karo (sign_user_settings)
      Step 2 — Entry + SL + TP ek atomic transaction mein (sign_group)
    """
    coin      = signal["coin"]
    direction = signal["direction"]
    leverage  = int(signal["leverage"])
    entry     = float(signal["entry"])
    tp_price  = float(signal["tp"])
    sl_price  = float(signal["sl"])
    symbol    = _coin_to_symbol(coin)

    is_buy = direction == "LONG"

    # Size calculate karo — 2500 USDT default
    size = _calculate_size(coin, entry, config.TRADE_SIZE_USDT)
    logger.info(f"Trade size: {size} {coin} ({config.TRADE_SIZE_USDT} USDT @ {entry})")

    try:
        signer = _get_signer()
    except ValueError as e:
        return {"success": False, "message": str(e)}

    # ── Step 1: Leverage set karo ─────────────────────────────
    try:
        leverage_tx = signer.sign_user_settings([(symbol, float(leverage))])
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{config.BULK_API_URL}/order",
                json=leverage_tx,
                headers={"Content-Type": "application/json"},
            )
        data = resp.json()
        if resp.status_code in (200, 201) and data.get("status") == "ok":
            logger.info(f"Leverage set to {leverage}x for {symbol}")
        else:
            logger.warning(f"Leverage set failed: {resp.status_code} {data}")
    except Exception as e:
        logger.error(f"Leverage set exception: {e}")

    # ── Step 2: Bracket order — Entry + SL + TP ──────────────
    sl_is_buy = not is_buy
    tp_is_buy = not is_buy

    orders = [
        {
            "type": "order",
            "symbol": symbol,
            "is_buy": is_buy,
            "price": entry,
            "size": size,
            "order_type": {"type": "market"},
        },
        {
            "type": "order",
            "symbol": symbol,
            "is_buy": sl_is_buy,
            "price": sl_price,
            "size": size,
            "reduce_only": True,
            "order_type": {"type": "limit", "tif": "GTC"},
        },
        {
            "type": "order",
            "symbol": symbol,
            "is_buy": tp_is_buy,
            "price": tp_price,
            "size": size,
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
        data = resp.json()
        logger.info(f"Bracket order response: {resp.status_code} {data}")

        if resp.status_code not in (200, 201) or data.get("status") != "ok":
            msg = str(data)
            logger.error(f"Bracket order failed: {msg}")
            return {"success": False, "message": f"Trade failed: {msg}"}

        statuses = data.get("response", {}).get("data", {}).get("statuses", [])
        results = []
        for i, st in enumerate(statuses):
            label = ["Entry", "Stop-Loss", "Take-Profit"][i] if i < 3 else f"Order {i+1}"
            if "filled" in st or "resting" in st or "working" in st:
                results.append(f"✅ {label} placed")
            elif "error" in st:
                results.append(f"⚠️ {label}: {st['error'].get('message', '?')}")
            elif "rejectedRiskLimit" in st:
                results.append(f"❌ {label}: Risk limit exceeded")
            else:
                results.append(f"✅ {label}: {list(st.keys())[0]}")

        logger.info(f"Trade executed: {symbol} {direction} x{leverage} | size={size} | {' | '.join(results)}")
        return {"success": True, "message": "\n".join(results)}

    except Exception as e:
        logger.error(f"Bracket order exception: {e}")
        return {"success": False, "message": f"Trade exception: {e}"}
