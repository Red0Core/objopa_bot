from httpx import AsyncClient
import asyncio

async def fetch_price(session: AsyncClient, url: str) -> dict[str, float | str]:
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

async def get_price_from_exchanges(symbol: str) -> dict[str, float | str]:
    """
    Запрашивает цену у двух бирж (Binance и MEXC) с приоритетом Binance.
    """
    if "USDT" not in symbol:
        symbol = f"{symbol}USDT"

    urls = { # В порядке приоритета
        "binance": f"https://api.binance.com/api/v3/ticker/price?symbol={symbol.upper()}",
        "mexc": f"https://api.mexc.com/api/v3/ticker/price?symbol={symbol.upper()}"
    }

    async with AsyncClient() as session:
        # Параллельные запросы к биржам с timeout
        tasks = [fetch_price(session, url) for url in urls.values()]
        results = await asyncio.gather(*tasks)

    # Приоритетный результат от Binance, затем MEXC
    for result in results:
        if "error" not in result:
            return result

    # Если оба запроса завершились ошибкой
    errors = ", ".join(str(result["error"]) for result in results if "error" in result)
    return {"error": errors}
