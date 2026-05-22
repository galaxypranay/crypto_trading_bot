import httpx
import logging
from bulk_keychain import Keypair, Signer
import config

logger = logging.getLogger(__name__)


def _get_signer() -> Signer:
    """Load keypair from env and return a Signer."""
    private_key_b58 = config.BULK_PRIVATE_KEY
    if not private_key_b58:
        raise ValueError("BULK_PRIVATE_KEY is not set in environment variables.")
    keypair = Keypair.from_base58(private_key_b58)
    return Signer(keypair)


def _coin_to_symbol(coin: str) -> str:
    """Convert coin ticker to Bulk.trade symbol format."""
    return f"{coin.upper()}-USD"


async def _set_leverage(signer: Signer, symbol: str, leverage: int) -> bool:
    """Set leverage for a symbol before placing the order."""
    try:
        tx = signer.sign_user_settings([(symbol, float(leverage))])
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{config.BULK_API_URL}/order",
                json=tx,
                headers={"Content-Type": "application/json"},
            )
        if response.status_code in (200, 201):
            logger.info(f"Leverage set to {leverage}x for {symbol}")
            return True
        else:
            logger.warning(f"Leverage set failed: {response.status_code} {response.text}")
            return False
    except Exception as e:
        logger.error(f"Leverage set exception: {e}")
        return False


async def execute_trade(signal: dict) -> dict:
    """
    Execute a futures trade on Bulk.trade after admin approval.

    Steps:
      1. Set leverage for the symbol
      2. Place market order (entry)
      3. Place stop-loss order
      4. Place take-profit order

    Returns {"success": True/False, "message": "..."}
    """
    coin     = signal["coin"]
    direction = signal["direction"]       # "LONG" or "SHORT"
    leverage  = int(signal["leverage"])
    entry     = float(signal["entry"])
    tp_price  = float(signal["tp"])
    sl_price  = float(signal["sl"])
    symbol    = _coin_to_symbol(coin)

    is_buy = direction == "LONG"

    # Position size: use a fixed USDT amount from config, convert to coin units
    # Size in coins = USDT_amount / entry_price
    usdt_amount = config.TRADE_SIZE_USDT
    size = round(usdt_amount / entry, 6) if entry > 0 else 0.01

    try:
        signer = _get_signer()
    except ValueError as e:
        return {"success": False, "message": str(e)}

    # ── Step 1: Set leverage ──────────────────────────────────
    await _set_leverage(signer, symbol, leverage)

    # ── Step 2: Entry market order ────────────────────────────
    entry_order = {
        "type": "order",
        "symbol": symbol,
        "is_buy": is_buy,
        "size": size,
        "order_type": {"type": "market"},
    }

    # ── Step 3: Stop-loss order ───────────────────────────────
    # Stop is opposite direction (reduce only)
    sl_order = {
        "type": "order",
        "symbol": symbol,
        "is_buy": not is_buy,
        "price": sl_price,
        "size": size,
        "reduce_only": True,
        "order_type": {
            "type": "stop",
            "trigger": sl_price,
        },
    }

    # ── Step 4: Take-profit order ─────────────────────────────
    tp_order = {
        "type": "order",
        "symbol": symbol,
        "is_buy": not is_buy,
        "price": tp_price,
        "size": size,
        "reduce_only": True,
        "order_type": {
            "type": "take_profit",
            "trigger": tp_price,
        },
    }

    results = []

    async with httpx.AsyncClient(timeout=20) as client:

        # Send entry order
        try:
            tx_entry = signer.sign(entry_order)
            resp = await client.post(
                f"{config.BULK_API_URL}/order",
                json=tx_entry,
                headers={"Content-Type": "application/json"},
            )
            data = resp.json()
            if resp.status_code in (200, 201):
                results.append("✅ Entry order placed")
                logger.info(f"Entry order placed: {symbol} {direction} x{leverage}")
            else:
                msg = data.get("message", resp.text)
                logger.error(f"Entry order failed: {msg}")
                return {"success": False, "message": f"Entry order failed: {msg}"}
        except Exception as e:
            logger.error(f"Entry order exception: {e}")
            return {"success": False, "message": f"Entry order exception: {e}"}

        # Send stop-loss
        try:
            tx_sl = signer.sign(sl_order)
            resp = await client.post(
                f"{config.BULK_API_URL}/order",
                json=tx_sl,
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code in (200, 201):
                results.append("✅ Stop-loss placed")
                logger.info(f"SL placed at {sl_price}")
            else:
                results.append(f"⚠️ SL failed: {resp.text[:80]}")
                logger.warning(f"SL order failed: {resp.text}")
        except Exception as e:
            results.append(f"⚠️ SL exception: {e}")
            logger.warning(f"SL exception: {e}")

        # Send take-profit
        try:
            tx_tp = signer.sign(tp_order)
            resp = await client.post(
                f"{config.BULK_API_URL}/order",
                json=tx_tp,
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code in (200, 201):
                results.append("✅ Take-profit placed")
                logger.info(f"TP placed at {tp_price}")
            else:
                results.append(f"⚠️ TP failed: {resp.text[:80]}")
                logger.warning(f"TP order failed: {resp.text}")
        except Exception as e:
            results.append(f"⚠️ TP exception: {e}")
            logger.warning(f"TP exception: {e}")

    message = "\n".join(results)
    return {"success": True, "message": message}
