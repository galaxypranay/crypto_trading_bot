import httpx
import logging
from bulk_keychain import Keypair, Signer
import config

logger = logging.getLogger(__name__)

# ── early.bulk.trade supported coins ─────────────────────────
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


def _clamp_leverage(coin: str, leverage: int) -> int:
    """Leverage ko 3 limits se clamp karo."""
    risk_max = config.LEVERAGE_MAP.get(config.RISK_MODE, {}).get("max", 50)
    coin_max = SUPPORTED_COINS.get(coin.upper(), {}).get("max_leverage", 50)
    effective = min(leverage, risk_max, coin_max, 50)
    if effective != leverage:
        logger.warning(
            f"Leverage clamped: {leverage}x → {effective}x "
            f"(risk_max={risk_max}, coin_max={coin_max})"
        )
    return effective


def _calculate_size(coin: str, entry: float, usd_amount: float) -> float:
    """USD amount se coin size calculate karo with minimum size check."""
    min_size = SUPPORTED_COINS.get(coin.upper(), {}).get("min_size", 0.01)
    if entry <= 0:
        return min_size
    size = round(usd_amount / entry, 6)
    if size < min_size:
        logger.warning(f"Size {size} too small for {coin}, using min {min_size}")
        size = min_size
    logger.info(f"Size [{coin}]: ${usd_amount} / {entry} = {size} coins (notional=${size*entry:.2f})")
    return size


def _safe_json(resp: httpx.Response) -> tuple[dict | None, str | None]:
    """
    Response se safely JSON parse karo.
    Returns: (data, error_message)
    - 403: Competition locked — clear message
    - Empty body: clear message
    - Invalid JSON: raw text
    - OK: (data, None)
    """
    # 403 — competition locked
    if resp.status_code == 403:
        raw = resp.text.strip()
        msg = raw[:200] if raw else "Forbidden"
        return None, (
            f"⛔ Exchange access blocked (HTTP 403)\n"
            f"Reason: {msg}\n\n"
            f"Staging competition chal rahi hai — `/order` endpoint locked hai.\n"
            f"Railway mein `BULK_API_URL` ko production URL se update karo:\n"
            f"`https://exchange-api.bulk.trade/api/v1`"
        )

    # Empty body
    raw = resp.text.strip()
    if not raw:
        return None, (
            f"⛔ Exchange ne empty response diya (HTTP {resp.status_code})\n"
            f"Account mein funds nahi hain ya API issue hai."
        )

    # JSON parse
    try:
        return resp.json(), None
    except Exception:
        return None, f"⛔ Invalid response (HTTP {resp.status_code}): {raw[:200]}"


async def _set_leverage(signer: Signer, symbol: str, leverage: int) -> bool:
    """Leverage set karo. 403 pe gracefully fail karo — trade continue karega."""
    try:
        tx = signer.sign_user_settings([(symbol, float(leverage))])
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{config.BULK_API_URL}/order",
                json=tx,
                headers={"Content-Type": "application/json"},
            )

        data, err = _safe_json(resp)
        if err:
            # Leverage set fail hona fatal nahi — warn karke continue karo
            logger.warning(f"Leverage set skipped: {err.splitlines()[0]}")
            return False

        if data.get("status") == "ok":
            logger.info(f"Leverage set: {symbol} → {leverage}x")
            return True

        logger.warning(f"Leverage set failed: {resp.status_code} | {data}")
        return False

    except Exception as e:
        logger.warning(f"Leverage set exception (non-fatal): {e}")
        return False


async def execute_trade(signal: dict) -> dict:
    """
    Execute bracket trade on Bulk.trade.

    Steps:
      1. Coin supported check
      2. Leverage clamp + set
      3. Entry market order
      4. SL + TP limit orders (reduce_only)
    """
    coin      = signal["coin"].upper()
    direction = signal["direction"]
    leverage  = int(signal["leverage"])
    entry     = float(signal["entry"])
    tp_price  = float(signal["tp"])
    sl_price  = float(signal["sl"])
    symbol    = _coin_to_symbol(coin)
    is_buy    = direction == "LONG"

    # Admin chosen amount, fallback to config
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
    size     = _calculate_size(coin, entry, usd_amount)

    logger.info(
        f"Trade: {symbol} {direction} x{leverage} | "
        f"size={size} | entry={entry} | tp={tp_price} | sl={sl_price} | usd=${usd_amount}"
    )

    try:
        signer = _get_signer()
    except ValueError as e:
        return {"success": False, "message": str(e)}

    # ── Leverage set (non-fatal if fails) ─────────────────────
    await _set_leverage(signer, symbol, leverage)

    # ── Entry order ───────────────────────────────────────────
    entry_order = {
        "type":       "order",
        "symbol":     symbol,
        "is_buy":     is_buy,
        "price":      entry,
        "size":       size,
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

        data, err = _safe_json(resp)
        if err:
            logger.error(f"Entry failed: {err.splitlines()[0]}")
            return {"success": False, "message": err}

        statuses = data.get("response", {}).get("data", {}).get("statuses", [])
        st = statuses[0] if statuses else {}
        key = list(st.keys())[0] if st else "unknown"

        if key == "rejectedRiskLimit":
            reason = st[key].get("reason", "risk limit exceeded") if isinstance(st[key], dict) else str(st[key])
            return {"success": False, "message": f"❌ Entry rejected — risk limit:\n{reason}"}

        if key not in {"filled", "resting", "working", "partiallyFilled"}:
            return {"success": False, "message": f"❌ Entry unexpected status: {key} | {st}"}

        logger.info(f"Entry placed: {key}")

    except Exception as e:
        logger.error(f"Entry exception: {e}")
        return {"success": False, "message": f"❌ Entry exception: {e}"}

    results = ["✅ Entry placed"]

    # ── SL order ──────────────────────────────────────────────
    sl_order = {
        "type":        "order",
        "symbol":      symbol,
        "is_buy":      not is_buy,
        "price":       sl_price,
        "size":        size,
        "reduce_only": True,
        "order_type":  {"type": "limit", "tif": "GTC"},
    }
    try:
        tx = signer.sign(sl_order)
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{config.BULK_API_URL}/order",
                json=tx,
                headers={"Content-Type": "application/json"},
            )
        data, err = _safe_json(resp)
        if err:
            results.append(f"⚠️ SL skipped: {err.splitlines()[0]}")
        elif data.get("status") == "ok":
            results.append("✅ Stop-Loss placed")
        else:
            results.append(f"⚠️ SL failed: {data}")
    except Exception as e:
        results.append(f"⚠️ SL exception: {e}")

    # ── TP order ──────────────────────────────────────────────
    tp_order = {
        "type":        "order",
        "symbol":      symbol,
        "is_buy":      not is_buy,
        "price":       tp_price,
        "size":        size,
        "reduce_only": True,
        "order_type":  {"type": "limit", "tif": "GTC"},
    }
    try:
        tx = signer.sign(tp_order)
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{config.BULK_API_URL}/order",
                json=tx,
                headers={"Content-Type": "application/json"},
            )
        data, err = _safe_json(resp)
        if err:
            results.append(f"⚠️ TP skipped: {err.splitlines()[0]}")
        elif data.get("status") == "ok":
            results.append("✅ Take-Profit placed")
        else:
            results.append(f"⚠️ TP failed: {data}")
    except Exception as e:
        results.append(f"⚠️ TP exception: {e}")

    logger.info(f"Trade complete: {symbol} {direction} x{leverage} | {' | '.join(results)}")
    return {"success": True, "message": "\n".join(results)}
