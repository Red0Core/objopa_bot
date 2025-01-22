import asyncio
from datetime import datetime, timedelta
from services.cbr import get_cbr_exchange_rate
from logger import logger

async def send_daily_cbr_rates(bot, chat_id):
    """
    Ежедневно отправляет курсы валют в указанный чат.
    """
    while True:
        now = datetime.now()
        target_time = now.replace(hour=13, minute=0, second=0, microsecond=0)  # Отправка в 13:00 UTC

        if now > target_time:
            target_time += timedelta(days=1)

        wait_time = (target_time - now).total_seconds()
        await asyncio.sleep(wait_time)

        rates = await get_cbr_exchange_rate()
        if "error" in rates:
            logger.error(message)
            message = f"Ошибка при получении курсов: {rates['error']}"
        else:
            usd_rate = rates["USD"]["rate"]
            eur_rate = rates["EUR"]["rate"]
            usd_diff = rates["USD"]["diff"]
            eur_diff = rates["EUR"]["diff"]
            message = (
                f"Курсы валют ЦБ РФ на сегодня:\n"
                f"💵 Доллар США: {usd_rate} ₽ ({'+' if usd_diff > 0 else ''}{usd_diff})\n"
                f"💶 Евро: {eur_rate} ₽ ({'+' if eur_diff > 0 else ''}{eur_diff})"
            )

            logger.info(f"Отправляем курсы валют в чат {chat_id}")
            
        await bot.send_message(chat_id=chat_id, text=message)