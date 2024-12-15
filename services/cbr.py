from curl_cffi.requests import AsyncSession
from logger import logger

async def get_cbr_exchange_rate():
    url = "https://www.cbr-xml-daily.ru/daily_json.js"
    try:
        async with AsyncSession() as session:
            response = await session.get(url)
            if response.status_code == 200:
                data = response.json()

                usd_rate = data["Valute"]["USD"]["Value"]
                eur_rate = data["Valute"]["EUR"]["Value"]

                usd_previous = data["Valute"]["USD"]["Previous"]
                eur_previous = data["Valute"]["EUR"]["Previous"]

                usd_diff = round(usd_rate - usd_previous, 2)
                eur_diff = round(eur_rate - eur_previous, 2)

                logger.info("Успешно получили курсы валют: USD={USD}, EUR={EUR}", USD=usd_rate, EUR=eur_rate)

                return {
                    "USD": {"rate": round(usd_rate, 2), "diff": usd_diff},
                    "EUR": {"rate": round(eur_rate, 2), "diff": eur_diff},
                }

            else:
                logger.error(f"Ошибка HTTP {response.status_code} при запросе курсов валют")
                return {"error": f"HTTP Error {response.status_code}"}
    except Exception as e:
        logger.exception("Ошибка при запросе курсов валют")
        return {"error": str(e)}
