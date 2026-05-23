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


async def _send_transaction(tx: dict) -> dict:
    """POST a signed transaction to Bulk.trade /order endpoint."""
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{config.BULK_API_URL}/order",
            json=tx,
            headers={"Content-Type": "application/json"},
        )
        return resp.status_code, resp.json()


async def execute_trade(signal: dict) -> dict:
    """
    Execute a futures trade on Bulk.trade after admin approval.

    Uses Bulk.trade's unified Transaction model with compact action tags:
      - updateUserSettings : leverage set karo
      - m                  : market order (entry)
      - st                 : stop-loss conditional order
      - tp                 : take-profit conditional order

    Returns {"success": True/False, "message": "..."}
    """
    coin      = signal["coin"]
    direction = signal["direction"]   # "LONG" or "SHORT"
    leverage  = int(signal["leverage"])
    entry     = float(signal["entry"])
    tp_price  = float(signal["tp"])
    sl_price  = float(signal["sl"])
    symbol    = _coin_to_symbol(coin)

    is_buy = direction == "LONG"

    # Position size: USDT / entry price = coin units
    usdt_amount = config.TRADE_SIZE_USDT
    size = round(usdt_amount / entry, 6) if entry > 0 else 0.01

    try:
        signer = _get_signer()
    except ValueError as e:
        return {"success": False, "message": str(e)}

    results = []

    # ── Step 1: Leverage set karo ─────────────────────────────
    # updateUserSettings action — m field mein symbol: leverage map
    try:
        leverage_tx = signer.sign({
            "actions": [
                {
                    "updateUserSettings": {
                        "m": {symbol: float(leverage)}
                    }
                }
            ]
        })
        status_code, data = await _send_transaction(leverage_tx)
        if status_code in (200, 201):
            logger.info(f"Leverage set to {leverage}x for {symbol}")
        else:
            logger.warning(f"Leverage set failed: {status_code} {data}")
    except Exception as e:
        logger.error(f"Leverage set exception: {e}")

    # ── Step 2: Market entry order ────────────────────────────
    # Action tag: "m" — fields: c, b, sz, r, i
    # NOTE: Market order mein price field NAHI hoti (API docs ke mutabiq)
    try:
        entry_tx = signer.sign({
            "actions": [
                {
                    "m": {
                        "c": symbol,
                        "b": is_buy,
                        "sz": size,
                        "r": False,
                        "i": False,
                    }
                }
            ]
        })
        status_code, data = await _send_transaction(entry_tx)
        if status_code in (200, 201) and data.get("status") == "ok":
            results.append("✅ Entry order placed")
            logger.info(f"Entry order placed: {symbol} {direction} x{leverage}")
        else:
            msg = str(data)
            logger.error(f"Entry order failed: {msg}")
            return {"success": False, "message": f"Entry order failed: {msg}"}
    except Exception as e:
        logger.error(f"Entry order exception: {e}")
        return {"success": False, "message": f"Entry order exception: {e}"}

    # ── Step 3: Stop-loss order ───────────────────────────────
    # Action tag: "st" — fields: c, d, sz, tr, lim, i
    # d (direction): LONG ke liye SL neeche hota hai → trigger below → d=False
    #                SHORT ke liye SL upar hota hai  → trigger above → d=True
    try:
        sl_direction = not is_buy   # LONG → False (trigger below), SHORT → True (trigger above)
        sl_tx = signer.sign({
            "actions": [
                {
                    "st": {
                        "c": symbol,
                        "d": sl_direction,
                        "sz": size,
                        "tr": sl_price,
                        "lim": sl_price,  # limit = trigger (stop-limit style)
                        "i": False,
                    }
                }
            ]
        })
        status_code, data = await _send_transaction(sl_tx)
        if status_code in (200, 201) and data.get("status") == "ok":
            results.append("✅ Stop-loss placed")
            logger.info(f"SL placed at {sl_price}")
        else:
            results.append(f"⚠️ SL failed: {data}")
            logger.warning(f"SL order failed: {data}")
    except Exception as e:
        results.append(f"⚠️ SL exception: {e}")
        logger.warning(f"SL exception: {e}")

    # ── Step 4: Take-profit order ─────────────────────────────
    # Action tag: "tp" — fields: c, d, sz, tr, lim, i
    # d (direction): LONG ke liye TP upar hota hai  → trigger above → d=True
    #                SHORT ke liye TP neeche hota hai → trigger below → d=False
    try:
        tp_direction = is_buy   # LONG → True (trigger above), SHORT → False (trigger below)
        tp_tx = signer.sign({
            "actions": [
                {
                    "tp": {
                        "c": symbol,
                        "d": tp_direction,
                        "sz": size,
                        "tr": tp_price,
                        "lim": tp_price,  # limit = trigger (take-profit-limit style)
                        "i": False,
                    }
                }
            ]
        })
        status_code, data = await _send_transaction(tp_tx)
        if status_code in (200, 201) and data.get("status") == "ok":
            results.append("✅ Take-profit placed")
            logger.info(f"TP placed at {tp_price}")
        else:
            results.append(f"⚠️ TP failed: {data}")
            logger.warning(f"TP order failed: {data}")
    except Exception as e:
        results.append(f"⚠️ TP exception: {e}")
        logger.warning(f"TP exception: {e}")

    message = "\n".join(results)
    return {"success": True, "message": message}
