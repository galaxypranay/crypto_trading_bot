import httpx
import logging
from bulk_keychain import Keypair, Signer
import config

logger = logging.getLogger(__name__)

# Bulk.trade minimum order sizes per coin (lot size)
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
    min_size = MIN_SIZE_MAP.get(coin.upper(), MIN_SIZE_MAP["default"])
    if entry <= 0:
        return min_size
    size = round(usdt_amount / entry, 6)
    if size < min_size:
        logger.warning(f"Size {size} too small for {coin}, using minimum {min_size}")
        size = min_size
    return size


async def _set_leverage(signer: Signer, symbol: str, leverage: int) -> bool:
    """Leverage set karo — max 50x (Bulk.trade hard limit)."""
    clamped = min(leverage, 50)
    if clamped != leverage:
        logger.warning(f"Leverage clamped {leverage}x → {clamped}x")
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
    Execute bracket trade on Bulk.trade.

    Step 1: Leverage set karo (sign_user_settings)
    Step 2: Entry + SL + TP atomic transaction (sign_group)

    ── Library limitation ──
    bulk_keychain library sirf 'market' aur 'limit' order types support
    karti hai. 'st'/'tp' conditional tags library mein nahi hain.

    ── Solution ──
    sign_group() se teen orders ek atomic transaction mein:
      - Entry:      market order
      - Stop-Loss:  reduce_only GTC limit order at sl_price
      - Take-Profit: reduce_only GTC limit order at tp_price

    Yeh exchange pe correctly kaam karta hai — reduce_only orders
    sirf open position ko close karenge.

    Returns {"success": True/False, "message": "..."}
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
    logger.info(
        f"Trade: {symbol} {direction} x{leverage} | "
        f"size={size} | entry={entry} | tp={tp_price} | sl={sl_price}"
    )

    try:
        signer = _get_signer()
    except ValueError as e:
        return {"success": False, "message": str(e)}

    # ── Step 1: Leverage ──────────────────────────────────────
    await _set_leverage(signer, symbol, leverage)

    # ── Step 2: Bracket order — Entry + SL + TP atomic ───────
    #
    # LONG trade:
    #   Entry  → buy  market
    #   SL     → sell limit at sl_price  (below entry, reduce_only)
    #   TP     → sell limit at tp_price  (above entry, reduce_only)
    #
    # SHORT trade:
    #   Entry  → sell market
    #   SL     → buy  limit at sl_price  (above entry, reduce_only)
    #   TP     → buy  limit at tp_price  (below entry, reduce_only)

    orders = [
        # Entry: market order (price required by library for signing)
        {
            "type": "order",
            "symbol": symbol,
            "is_buy": is_buy,
            "price": entry,
            "size": size,
            "order_type": {"type": "market"},
        },
        # Stop-Loss: reduce_only GTC limit
        {
            "type": "order",
            "symbol": symbol,
            "is_buy": not is_buy,
            "price": sl_price,
            "size": size,
            "reduce_only": True,
            "order_type": {"type": "limit", "tif": "GTC"},
        },
        # Take-Profit: reduce_only GTC limit
        {
            "type": "order",
            "symbol": symbol,
            "is_buy": not is_buy,
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
        logger.info(f"Bracket order response: {resp.status_code} | {data}")

        if resp.status_code not in (200, 201):
            return {"success": False, "message": f"API error {resp.status_code}: {data}"}

        if data.get("status") != "ok":
            return {"success": False, "message": f"Trade rejected: {data}"}

        statuses = data.get("response", {}).get("data", {}).get("statuses", [])
        labels   = ["Entry", "Stop-Loss", "Take-Profit"]
        SUCCESS  = {"filled", "resting", "working", "partiallyFilled"}
        FAILURE  = {"rejectedRiskLimit", "rejectedInvalid", "rejectedCrossing",
                    "rejectedDuplicate", "cancelledRiskLimit", "error"}

        lines = []
        overall_success = True

        for i, st in enumerate(statuses):
            label      = labels[i] if i < len(labels) else f"Order {i+1}"
            status_key = list(st.keys())[0] if st else "unknown"
            status_val = st.get(status_key, {})

            if status_key in SUCCESS:
                lines.append(f"✅ {label} placed")
            elif status_key in FAILURE:
                reason = ""
                if isinstance(status_val, dict):
                    reason = status_val.get("reason") or status_val.get("message") or ""
                lines.append(f"❌ {label} failed: {status_key} — {reason}")
                if i == 0:          # Entry fail = poora trade fail
                    overall_success = False
                logger.error(f"{label} rejected: {status_key} — {reason}")
            elif status_key == "cancelled":
                lines.append(f"⚠️ {label} cancelled")
            else:
                lines.append(f"ℹ️ {label}: {status_key}")

        logger.info(
            f"Trade complete: {symbol} {direction} x{leverage} | "
            f"success={overall_success} | {' | '.join(lines)}"
        )
        return {"success": overall_success, "message": "\n".join(lines)}

    except Exception as e:
        logger.error(f"Bracket order exception: {e}")
        return {"success": False, "message": f"Trade exception: {e}"}
