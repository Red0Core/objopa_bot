from datetime import datetime
from typing import Any, cast

from curl_cffi.requests import AsyncSession, Response

from core.config import ALPHAVANTAGE_API_KEY


async def fetch_currency_data(symbol: str, market: str = "RUB") -> dict[str, Any]:
    """
    Загружает данные валютного курса с Alpha Vantage.
    :param symbol: Базовая валюта (например, "USD").
    :param market: Котируемая валюта (например, "RUB").
    :return: Данные о валютном курсе.
    """
    url = "https://www.alphavantage.co/query"
    params: dict[str, str] = {
        "function": "FX_DAILY",
        "from_symbol": symbol,
        "to_symbol": market,
        "apikey": ALPHAVANTAGE_API_KEY,
    }

    async with AsyncSession() as session:  # type: ignore
        response: Response = await session.get(url, params=params)  # type: ignore
        response.raise_for_status()
        return response.json()  # type: ignore


def parse_currency_data(
    data: dict[str, Any],
) -> tuple[float | None, float | None, float | None, float | None]:
    """
    Извлекает сегодняшнюю, вчерашнюю, недельную и месячную цену.
    """
    raw_series = data.get("Time Series FX (Daily)")
    if not isinstance(raw_series, dict):
        return None, None, None, None

    time_series = cast(dict[str, dict[str, str]], raw_series)
    dates = sorted(time_series.keys(), reverse=True)

    today = float(time_series[dates[0]]["4. close"]) if len(dates) > 0 else None
    yesterday = float(time_series[dates[1]]["4. close"]) if len(dates) > 1 else None

    price_7d: float | None = None
    price_30d: float | None = None

    if not dates:
        return today, yesterday, None, None

    today_date = datetime.strptime(dates[0], "%Y-%m-%d")

    for date in dates:
        current_date = datetime.strptime(date, "%Y-%m-%d")
        days_diff = (today_date - current_date).days

        if price_7d is None and days_diff >= 7:
            price_7d = float(time_series[date]["4. close"])
        if price_30d is None and days_diff >= 30:
            price_30d = float(time_series[date]["4. close"])
        if price_7d is not None and price_30d is not None:
            break

    return today, yesterday, price_7d, price_30d


def calculate_change(today: float, yesterday: float) -> tuple[float, float]:
    """
    Рассчитывает абсолютное и процентное изменение.
    """
    absolute_change = today - yesterday
    percent_change = (absolute_change / yesterday) * 100
    return absolute_change, percent_change
