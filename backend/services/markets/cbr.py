from typing import Any, TypedDict, cast

from httpx import AsyncClient, Response

from backend.models.markets import RateItem
from core.logger import logger


class ValuteItem(TypedDict):
    Value: float
    Previous: float


async def get_cbr_exchange_rate() -> dict[str, RateItem | str]:
    url = "https://www.cbr-xml-daily.ru/daily_json.js"
    try:
        async with AsyncClient() as session:
            response: Response = await session.get(url)

            if response.status_code == 200:
                data: dict[str, Any] = response.json()

                valute = data.get("Valute")
                if not isinstance(valute, dict):
                    raise ValueError("Некорректный формат ответа ЦБ")
                valute = cast(dict[str, Any], valute)

                if not valute.get("USD") or not valute.get("EUR"):
                    raise ValueError("Недоступны курсы USD или EUR")

                usd_info = cast(ValuteItem, valute.get("USD"))
                eur_info = cast(ValuteItem, valute.get("EUR"))

                usd_rate = float(usd_info["Value"])
                eur_rate = float(eur_info["Value"])
                usd_previous = float(usd_info["Previous"])
                eur_previous = float(eur_info["Previous"])

                usd_diff = round(usd_rate - usd_previous, 2)
                eur_diff = round(eur_rate - eur_previous, 2)

                logger.info(
                    "Успешно получили курсы валют: USD={USD}, EUR={EUR}", USD=usd_rate, EUR=eur_rate
                )

                return {
                    "USD": RateItem(rate=round(usd_rate, 2), diff=usd_diff),
                    "EUR": RateItem(rate=round(eur_rate, 2), diff=eur_diff),
                }

            logger.error(f"Ошибка HTTP {response.status_code} при запросе курсов валют")
            return {"error": f"HTTP Error {response.status_code}"}

    except Exception as e:
        logger.exception("Ошибка при запросе курсов валют")
        return {"error": str(e)}


async def generate_html_output(rates: dict[str, RateItem | str]) -> str:
    """Генерирует HTML-вывод для курсов валют."""
    usd_data = rates.get("USD")
    eur_data = rates.get("EUR")
    if (
        usd_data is None
        or isinstance(usd_data, str)
        or eur_data is None
        or isinstance(eur_data, str)
    ):
        return "Ошибка получения данных о курсах валют."

    return (
        f"Курсы валют ЦБ РФ на сегодня:\n"
        f"💵 Доллар США: <code>{usd_data.rate} ₽ ({'+' if usd_data.diff > 0 else ''}{usd_data.diff})</code>\n"
        f"💶 Евро: <code>{eur_data.rate} ₽ ({'+' if eur_data.diff > 0 else ''}{eur_data.diff})</code>\n"
    )
