import httpx
import logging
from bulk_keychain import Keypair, Signer
import config

logger = logging.getLogger(__name__)

# ── early.bulk.trade pe available coins aur unka max leverage ─
SUPPORTED_COINS = {
    "BTC":      {"max_leverage": 50, "min_size": 0.001},
    "ETH":      {"max_leverage": 50, "min_size": 0.01},
    "SOL":      {"max_leverage": 50, "min_size": 0.1},
    "XRP":      {"max_leverage": 50, "min_size": 10.0},
    "SUI":      {"max_leverage": 40, "min_size": 10.0},
    "BNB":      {"max_leverage": 40, "min_size": 0.01},
    "ZEC":      {"max_leverage": 40, "min_size": 0.01},
    "DOGE":     {"max_leverage": 10, "min_size": 100.0},
    "FARTCOIN": {"max_leverage": 25, "min_size": 10.0},
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
    coin_info = SUPPORTED_COINS.get(coin.upper(), {})
    min_size  = coin_info.get("min_size", 0.01)
    if entry <= 0:
        return min_size
    size = round(usdt_amount / entry, 6)
    if size < min_size:
        logger.warning(f"Size {size} too small for {coin}, using minimum {min_size}")
        size = min_size
    return size


def _clamp_leverage(coin: str, leverage: int) -> int:
    """Coin ke max leverage se clamp karo."""
    coin_info   = SUPPORTED_COINS.get(coin.upper(), {})
    max_allowed = coin_info.get("max_leverage", 50)
    clamped     = min(leverage, max_allowed)
    if clamped != leverage:
        logger.warning(f"Leverage clamped {leverage}x → {clamped}x (max for {coin}: {max_allowed}x)")
    return clamped


async def _set_leverage(signer: Signer, symbol: str, leverage: int) -> bool:
    """Leverage set karo."""
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
        else:
            logger.warning(f"Leverage set failed: {resp.status_code} | {data}")
            return False
    except Exception as e:
        logger.error(f"Leverage set exception: {e}")
        return False


async def execute_trade(signal: dict) -> dict:
    """
    Execute bracket trade on early.bulk.trade.

    Step 1: Coin supported hai check karo
    Step 2: Leverage set karo (sign_user_settings)
    Step 3: Entry + SL + TP atomic transaction (sign_group)

    sign_group se 3 orders ek saath:
      - Entry:       market order
      - Stop-Loss:   reduce_only GTC limit order
      - Take-Profit: reduce_only GTC limit order

    Returns {"success": True/False, "message": "..."}
    """
    coin      = signal["coin"].upper()
    direction = signal["direction"]   # "LONG" or "SHORT"
    leverage  = int(signal["leverage"])
    entry     = float(signal["entry"])
    tp_price  = float(signal["tp"])
    sl_price  = float(signal["sl"])
    symbol    = _coin_to_symbol(coin)
    is_buy    = direction == "LONG"

    # ── Coin supported check ──────────────────────────────────
    if coin not in SUPPORTED_COINS:
        msg = f"{coin} early.bulk.trade pe available nahi hai. Supported: {', '.join(SUPPORTED_COINS.keys())}"
        logger.error(msg)
        return {"success": False, "message": msg}

    # ── Leverage clamp (coin-specific max) ────────────────────
    leverage = _clamp_leverage(coin, leverage)

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
    # LONG:  entry buy market | SL sell limit (neeche) | TP sell limit (upar)
    # SHORT: entry sell market | SL buy limit (upar)   | TP buy limit (neeche)

    orders = [
        # Entry: market order (price field library signing ke liye required hai)
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

        lines           = []
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
                if i == 0:
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
