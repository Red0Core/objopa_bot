import asyncio
from config import ZA_IDEU_CHAT_ID, OBZHORA_CHAT_ID, MAIN_ACC
from services.cbr import get_cbr_exchange_rate
from services.horoscope_mail_ru import get_horoscope_mail_ru
from datetime import datetime, timedelta
from logger import logger
import routers.day_tracker as day_tracker
from tg_bot import redis_worker

async def scheduled_message(bot):
    await bot.send_message(
        MAIN_ACC, 
        text="Бот стартовал и готов к работе!"
    )

def daily_schedule(hour=13, minute=0):
    def decorator(func):
        async def wrapper(bot, *args, **kwargs):
            while True:
                now = datetime.now()
                target_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

                if now > target_time:
                    target_time += timedelta(days=1)

                wait_time = (target_time - now).total_seconds()
                await asyncio.sleep(wait_time)
                
                await func(bot, *args, **kwargs)
        return wrapper
    return decorator

@daily_schedule(hour=16, minute=0)
async def send_daily_cbr_rates(bot, chat_id):
    """
    Ежедневно отправляет курсы валют в указанный чат.
    """
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

@daily_schedule(hour=6, minute=0)
async def send_daily_horoscope_for_brothers(bot):
    zodiac_map = {
        "taurus": "телец",
        "pisces": "рыбы",
        "libra": "весы"
    }
    # Для каждого знака получаем ежедневный гороскоп и рейтинг финансов из страницы prediction
    for zodiac_eng, zodiac_ru in zodiac_map.items():
        message = await get_horoscope_mail_ru(zodiac_eng, zodiac_ru)
        await bot.send_message(OBZHORA_CHAT_ID, message)
        logger.info(f"Отправляем еждедневные гороскопы в чат {OBZHORA_CHAT_ID} для {zodiac_ru}")
        await asyncio.sleep(2)

@daily_schedule(hour=8, minute=0)
async def send_daily_tracker_messages(bot):
    await day_tracker.send_daily_message(bot)

async def on_startup(bot):
    for coro in (
                    scheduled_message(bot),
                    send_daily_cbr_rates(bot, OBZHORA_CHAT_ID),
                    send_daily_horoscope_for_brothers(bot),
                    send_daily_tracker_messages(bot),
                    redis_worker.poll_redis(bot),
    ):
        asyncio.create_task(coro)
