import asyncio

from httpx import AsyncClient

from backend.models.markets import PriceResponse


async def fetch_price(session: AsyncClient, url: str) -> PriceResponse:
    """
    Выполняет запрос к API биржи и возвращает результат.
    """
    try:
        response = await session.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        if "msg" in data:  # Проверка на ошибку API
            raise ValueError(data["msg"])
        return PriceResponse(symbol=data["symbol"], price=float(data["price"]))
    except Exception as e:
        return PriceResponse(error=str(e))  # Возвращаем ошибку в формате PriceResponse


async def get_price_from_exchanges(symbol: str) -> PriceResponse:
    """
    Запрашивает цену у двух бирж (Binance и MEXC) с приоритетом Binance.
    """
    if "USDT" not in symbol:
        symbol = f"{symbol}USDT"

    urls = {  # В порядке приоритета
        "binance": f"https://api.binance.com/api/v3/ticker/price?symbol={symbol.upper()}",
        "mexc": f"https://api.mexc.com/api/v3/ticker/price?symbol={symbol.upper()}",
    }

    async with AsyncClient() as session:
        # Параллельные запросы к биржам с timeout
        tasks = [fetch_price(session, url) for url in urls.values()]
        results = await asyncio.gather(*tasks)

    # Приоритетный результат от Binance, затем MEXC
    for result in results:
        if result.error is not None:
            return result

    return results[0]  # Возвращаем ошибку от первой биржи (Binance)
