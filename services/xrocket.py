import aiohttp
from config import XROCKET_API_KEY

XROCKET_BASE_URL = "https://pay.xrocket.tg"


async def create_xrocket_invoice(amount: float):
    url = f"{XROCKET_BASE_URL}/tg-invoices"

    headers = {
        "Rocket-Pay-Key": XROCKET_API_KEY,
        "Content-Type": "application/json"
    }

    payload = {
        "amount": amount,
        "currency": "USDT",
        "numPayments": 1
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json=payload, headers=headers) as resp:
                res = await resp.json()
                # ✅ Использование resp.ok или проверки статусов [200, 201]
                if resp.ok and res.get("success"):
                    return res.get("data")
                else:
                    print(f"❌ Ошибка xRocket Create [{resp.status}]: {res}")
        except Exception as e:
            print(f"⚠️ Исключение при запросе к xRocket: {e}")

    return None


async def check_xrocket_invoice(invoice_id: str):
    url = f"{XROCKET_BASE_URL}/tg-invoices/{invoice_id}"

    headers = {
        "Rocket-Pay-Key": XROCKET_API_KEY
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=headers) as resp:
                res = await resp.json()
                if resp.ok and res.get("success"):
                    return res.get("data")
                else:
                    print(f"❌ Ошибка xRocket Check [{resp.status}]: {res}")
        except Exception as e:
            print(f"⚠️ Исключение при проверке xRocket: {e}")

    return None