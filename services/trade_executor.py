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
    return f"{coin.upper()}-USD"


async def execute_trade(signal: dict) -> dict:
    """
    Execute a bracket trade on Bulk.trade:
      Step 1 — Leverage set karo (sign_user_settings)
      Step 2 — Entry + SL + TP ek atomic transaction mein (sign_group)

    Library supported order types:
      - market : {'type': 'order', ..., 'order_type': {'type': 'market'}}
      - limit  : {'type': 'order', ..., 'order_type': {'type': 'limit', 'tif': 'GTC'}}

    SL aur TP limit orders hain (reduce_only=True) — jab price SL/TP tak aaye
    toh automatically fill ho jayenge (GTC resting limit orders).
    """
    coin      = signal["coin"]
    direction = signal["direction"]   # "LONG" or "SHORT"
    leverage  = int(signal["leverage"])
    entry     = float(signal["entry"])
    tp_price  = float(signal["tp"])
    sl_price  = float(signal["sl"])
    symbol    = _coin_to_symbol(coin)

    is_buy = direction == "LONG"

    usdt_amount = config.TRADE_SIZE_USDT
    size = round(usdt_amount / entry, 6) if entry > 0 else 0.01

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
        # Leverage fail hone pe bhi trade try karte hain

    # ── Step 2: Bracket order — Entry + SL + TP atomic ───────
    # LONG  → entry buy,  SL sell limit neeche, TP sell limit upar
    # SHORT → entry sell, SL buy limit upar,    TP buy limit neeche
    sl_is_buy = not is_buy   # LONG mein SL sell hai, SHORT mein buy
    tp_is_buy = not is_buy   # same direction as SL

    orders = [
        # Entry: market order
        {
            "type": "order",
            "symbol": symbol,
            "is_buy": is_buy,
            "price": entry,
            "size": size,
            "order_type": {"type": "market"},
        },
        # Stop-Loss: reduce-only limit order
        {
            "type": "order",
            "symbol": symbol,
            "is_buy": sl_is_buy,
            "price": sl_price,
            "size": size,
            "reduce_only": True,
            "order_type": {"type": "limit", "tif": "GTC"},
        },
        # Take-Profit: reduce-only limit order
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

        # Response parse karo
        statuses = data.get("response", {}).get("data", {}).get("statuses", [])
        results = []
        for i, st in enumerate(statuses):
            label = ["Entry", "Stop-Loss", "Take-Profit"][i] if i < 3 else f"Order {i+1}"
            if "filled" in st or "resting" in st or "working" in st:
                results.append(f"✅ {label} placed")
            elif "error" in st:
                results.append(f"⚠️ {label} error: {st['error'].get('message', '?')}")
            else:
                results.append(f"✅ {label}: {list(st.keys())[0]}")

        logger.info(f"Trade executed: {symbol} {direction} x{leverage} | {' | '.join(results)}")
        return {"success": True, "message": "\n".join(results)}

    except Exception as e:
        logger.error(f"Bracket order exception: {e}")
        return {"success": False, "message": f"Trade exception: {e}"}
