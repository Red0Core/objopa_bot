from curl_cffi.requests import AsyncSession
import asyncio

async def fetch_price(session, url):
    """
    Выполняет запрос к API биржи и возвращает результат.
    """
    try:
        response = await session.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        if "msg" in data:  # Проверка на ошибку API
            raise ValueError(data["msg"])
        return {"symbol": data["symbol"], "price": float(data["price"])}
    except Exception as e:
        return {"error": str(e)}

async def get_price_from_exchanges(symbol: str):
    """
    Запрашивает цену у двух бирж (Binance и MEXC) с приоритетом Binance.
    """
    if "USDT" not in symbol:
        symbol = f"{symbol}USDT"

    urls = { # В порядке приоритета
        "binance": f"https://api.binance.com/api/v3/ticker/price?symbol={symbol.upper()}",
        "mexc": f"https://api.mexc.com/api/v3/ticker/price?symbol={symbol.upper()}"
    }

    async with AsyncSession() as session:
        # Параллельные запросы к биржам
        tasks = {name: asyncio.create_task(fetch_price(session, url)) for name, url in urls.items()}
        results = await asyncio.gather(*tasks.values())

    # Приоритетный результат от Binance, затем MEXC
    for result in results:
        if "error" not in result:
            return result

    # Если оба запроса завершились ошибкой
    errors = ", ".join(result["error"] for result in results if "error" in result)
    return {"error": errors}
