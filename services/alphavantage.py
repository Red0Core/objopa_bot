from curl_cffi.requests import AsyncSession
from config import ALPHAVANTAGE_API_KEY
from datetime import datetime

async def fetch_currency_data(symbol: str, market: str = "RUB"):
    """
    Загружает данные валютного курса с Alpha Vantage.
    :param symbol: Базовая валюта (например, "USD").
    :param market: Котируемая валюта (например, "RUB").
    :return: Данные о валютном курсе.
    """
    url = f"https://www.alphavantage.co/query"
    params = {
        "function": "FX_DAILY",
        "from_symbol": symbol,
        "to_symbol": market,
        "apikey": ALPHAVANTAGE_API_KEY,
    }

    async with AsyncSession() as session:
        response = await session.get(url, params=params)
        response.raise_for_status()

    return response.json()

def parse_currency_data(data):
    """
    Извлекает сегодняшнюю, вчерашнюю, недельную и месячную цену.
    :param data: JSON-ответ Alpha Vantage.
    :return: Кортеж (сегодняшняя цена, вчерашняя цена, цена 7 дней назад, цена 30 дней назад).
    """
    time_series = data.get("Time Series FX (Daily)", {})
    dates = sorted(time_series.keys(), reverse=True)  # Сортируем даты

    today = float(time_series[dates[0]]["4. close"]) if len(dates) > 0 else None
    yesterday = float(time_series[dates[1]]["4. close"]) if len(dates) > 1 else None

    # Ищем цены 7 и 30 дней назад
    price_7d = None
    price_30d = None
    today_date = datetime.strptime(dates[0], "%Y-%m-%d")
    for date in dates:
        current_date = datetime.strptime(date, "%Y-%m-%d")
        if price_7d is None and (today_date - current_date).days >= 7:
            price_7d = float(time_series[date]["4. close"])
        if price_30d is None and (today_date - current_date).days >= 30:
            price_30d = float(time_series[date]["4. close"])
        if price_7d and price_30d:
            break

    return today, yesterday, price_7d, price_30d

def calculate_change(today, yesterday):
    """
    Рассчитывает абсолютное и процентное изменение.
    :param today: Сегодняшняя цена.
    :param yesterday: Вчерашняя цена.
    :return: Кортеж (абсолютное изменение, процентное изменение).
    """
    absolute_change = today - yesterday
    percent_change = (absolute_change / yesterday) * 100
    return absolute_change, percent_change
