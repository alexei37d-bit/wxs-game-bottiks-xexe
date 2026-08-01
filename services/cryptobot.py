import aiohttp
from config import CRYPTOBOT_TOKEN

CRYPTOBOT_BASE_URL = "https://pay.crypt.bot/api"


async def create_cryptobot_invoice(amount: float):
    url = f"{CRYPTOBOT_BASE_URL}/createInvoice"
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}
    payload = {
        "asset": "USDT",
        "amount": amount
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json=payload, headers=headers) as resp:
                res = await resp.json()
                if resp.ok and res.get("ok"):
                    return res.get("result")
                else:
                    print(f"❌ Ошибка CryptoBot Create [{resp.status}]: {res}")
        except Exception as e:
            print(f"⚠️ Исключение CryptoBot Create: {e}")

    return None


async def check_cryptobot_invoice(invoice_id: str):
    url = f"{CRYPTOBOT_BASE_URL}/getInvoices"
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}
    params = {"invoice_ids": invoice_id}

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, params=params, headers=headers) as resp:
                res = await resp.json()
                if resp.ok and res.get("ok"):
                    items = res.get("result", {}).get("items", [])
                    if items:
                        return items[0]
                else:
                    print(f"❌ Ошибка CryptoBot Check [{resp.status}]: {res}")
        except Exception as e:
            print(f"⚠️ Исключение CryptoBot Check: {e}")

    return None