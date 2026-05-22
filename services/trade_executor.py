import httpx
import logging
import config

logger = logging.getLogger(__name__)


async def execute_trade(signal: dict) -> dict:
    """
    Execute a futures trade on Bulk.trade after admin approval.
    Returns {"success": True/False, "message": "..."}
    """
    headers = {
        "Authorization": f"Bearer {config.BULK_API_KEY}",
        "Content-Type": "application/json",
    }

    side = "buy" if signal["direction"] == "LONG" else "sell"

    payload = {
        "symbol": f"{signal['coin']}USDT",
        "side": side,
        "type": "market",
        "leverage": signal["leverage"],
        "quantity": None,        # Bulk.trade may use USD amount instead
        "takeProfit": signal["tp"],
        "stopLoss": signal["sl"],
    }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{config.BULK_API_URL}/v1/futures/order",
                headers=headers,
                json=payload,
            )
            data = response.json()

        if response.status_code in (200, 201):
            logger.info(f"Trade executed: {signal['coin']} {signal['direction']}")
            return {"success": True, "message": "Trade opened successfully", "data": data}
        else:
            error_msg = data.get("message", str(data))
            logger.error(f"Bulk.trade error {response.status_code}: {error_msg}")
            return {"success": False, "message": f"Bulk.trade error: {error_msg}"}

    except Exception as e:
        logger.error(f"Trade execution exception: {e}")
        return {"success": False, "message": f"Exception: {str(e)}"}
