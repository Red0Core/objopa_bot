from curl_cffi.requests import AsyncSession
from config import COINMARKETCAP_API_KEY
from logger import logger
import json

def format_crypto_price(data: list[dict], num_of_tokens=0.0):
    """
    Форматирует данные о криптовалюте для красивого вывода.
    """
    output = ""
    for coin_data in data:
        quote = coin_data.get("quote", {}).get("USD", {})
        price = float(quote.get('price', 0))

        if not coin_data or not quote:
            return "Ошибка: данные о криптовалюте отсутствуют."

        # Формируем красиво оформленное сообщение
        message = (
            f"🔹 {coin_data.get('name', 'N/A')} (<code>{coin_data.get('symbol', 'N/A')}</code>)\n"
            f"💵 <b>Цена:</b> ${price:.5f}\n"
            f"📊 <b>Изменения:</b>\n"
            f"  - За 1 час: {quote.get('percent_change_1h', 0):+.2f}%\n"
            f"  - За 24 часа: {quote.get('percent_change_24h', 0):+.2f}%\n"
            f"  - За 7 дней: {quote.get('percent_change_7d', 0):+.2f}%\n"
            f"  - За 30 дней: {quote.get('percent_change_30d', 0):+.2f}%\n"
            f"💹 <b>Капа:</b> ${quote.get('market_cap', 0):,.2f}\n"
            f"🔄 <b>Объем за 24 часа:</b> ${quote.get('volume_24h', 0):,.2f}\n"
        )

        # Выводит сумму баксов по прайсу токена
        if num_of_tokens > 0:
            message = f"{message}{num_of_tokens:.5f} * {price:.5f} = <code>{(num_of_tokens*price):,.5f}</code>💲"

        output = f"{output}\n{message}"

    return output or "Ошибка: данные о криптовалюте отсутствуют."

def filter_tickers(data):
    """
    Фильтрует список тикеров на основе заданных условий.
    
    :param data: Данные из API CoinMarketCap.
    :return: Список тикеров, прошедших фильтр.
    """
    filtered_tickers = []

    for ticker in data:
        market_cap = ticker["quote"]["USD"]["market_cap"]
        try:
            # Пример условий
            if market_cap is None or market_cap < 1:
                continue  # Пропустить, если капитализация меньше заданной
            if ticker["is_active"] != 1:
                continue  # Пропустить, если токен неактивен
            
            # Если тикер прошел все условия, добавляем его в результат
            filtered_tickers.append(ticker)
        except TypeError:
            logger.info(f"Данные тикера: {json.dumps(ticker, indent=5)}")

    return filtered_tickers

async def get_coinmarketcap_data(ticker: str):
    ticker = ticker.upper()
    headers = {
        'Accepts': 'application/json',
        'X-CMC_PRO_API_KEY': COINMARKETCAP_API_KEY,
    }
    parameters = {
        'symbol': ticker
    }
    async with AsyncSession() as session:
        session.headers.update(headers)
        response = await session.get("https://pro-api.coinmarketcap.com/v2/cryptocurrency/quotes/latest", params=parameters)
        
        if response.json().get('data', None) is None:
            logger.error(f"Данные от CMC не поступили: {json.dumps(response.json(), indent=5)}")

        return response.json()
