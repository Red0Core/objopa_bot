from curl_cffi.requests import AsyncSession

async def get_binance_price(symbol: str):
    if "USDT" not in symbol:
        symbol = f"{symbol}USDT"

    url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol.upper()}"
    async with AsyncSession() as client:
        response = await client.get(url)
        if response.status_code == 200:
            data = response.json()
            return float(data.get("price"))
        else:
            return None
