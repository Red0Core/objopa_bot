import asyncio
from datetime import datetime, timedelta

import httpx

import tg_bot.redis_workers.base_notifications as base_notifications
import tg_bot.routers.day_tracker as day_tracker
from core.config import BACKEND_ROUTE, DOWNLOADS_DIR, MAIN_ACC, OBZHORA_CHAT_ID
from core.logger import logger
from tg_bot.redis_workers import image_selection
from tg_bot.services.horoscope_mail_ru import get_horoscope_mail_ru


async def scheduled_message(bot):
    await bot.send_message(MAIN_ACC, text="Бот стартовал и готов к работе!")


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
    try:
        async with httpx.AsyncClient() as session:
            response = await session.get(f"{BACKEND_ROUTE}/markets/cbr/rates")
            response.raise_for_status()
            data = response.json()
            await bot.send_message(chat_id=chat_id, text=data["html_output"], parse_mode="html")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400:
            await bot.send_message(chat_id=chat_id, text="Ошибка: неверный запрос")
        else:
            await bot.send_message(chat_id=chat_id, text="Ошибка: не удалось получить данные")
        logger.info(f"Отправляем курсы валют в чат {chat_id}")
    except Exception as e:
        await bot.send_message(chat_id=chat_id, text="Ошибка: не удалось получить данные")
        logger.error(f"Ошибка при отправке курсов валют в чат {chat_id}: {e}")


@daily_schedule(hour=6, minute=0)
async def send_daily_horoscope_for_brothers(bot):
    zodiac_map = {"taurus": "телец", "pisces": "рыбы", "libra": "весы"}
    # Для каждого знака получаем ежедневный гороскоп и рейтинг финансов из страницы prediction
    try:
        for zodiac_eng, zodiac_ru in zodiac_map.items():
            message = await get_horoscope_mail_ru(zodiac_eng)
            await bot.send_message(OBZHORA_CHAT_ID, message)
            logger.info(f"Отправляем еждедневные гороскопы в чат {OBZHORA_CHAT_ID} для {zodiac_ru}")
            await asyncio.sleep(2)
    except Exception as e:
        logger.error(f"Ошибка при отправке ежедневного гороскопа в чат {OBZHORA_CHAT_ID}: {e}")


@daily_schedule(hour=8, minute=0)
async def send_daily_tracker_messages(bot):
    await day_tracker.send_daily_message(bot)


@daily_schedule(hour=3, minute=0)
async def cleanup_downloads(bot):
    removed = 0
    for file in DOWNLOADS_DIR.glob("*"):
        if file.is_file():
            try:
                file.unlink()
                removed += 1
            except Exception as e:  # noqa: BLE001
                logger.error(f"Failed to delete {file}: {e}")
    if removed:
        logger.info(f"Cleaned {removed} files from downloads")


async def on_startup(bot):
    for coro in (
        scheduled_message(bot),
        send_daily_cbr_rates(bot, OBZHORA_CHAT_ID),
        send_daily_horoscope_for_brothers(bot),
        send_daily_tracker_messages(bot),
        cleanup_downloads(bot),
        base_notifications.poll_redis(bot),
        image_selection.poll_image_selection(bot),
    ):
        asyncio.create_task(coro)
