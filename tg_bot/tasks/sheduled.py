import asyncio
from datetime import datetime, timedelta

import httpx

import tg_bot.redis_workers.base_notifications as base_notifications
import tg_bot.routers.day_tracker as day_tracker
from core.config import BACKEND_ROUTE, DOWNLOADS_DIR, MAIN_ACC, OBZHORA_CHAT_ID
from core.logger import logger
from core.memory import DOWNLOAD_TTL_SEC, trim_memory
from tg_bot.redis_workers import image_selection
from tg_bot.services.horoscope_mail_ru import format_horoscope, get_horoscope_mail_ru


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


@daily_schedule(hour=6, minute=0)
async def send_daily_horoscope_for_brothers(bot):
    zodiac_map = {"taurus": "телец", "pisces": "рыбы", "libra": "весы"}
    # Для каждого знака получаем ежедневный гороскоп и рейтинг финансов из страницы prediction
    try:
        for zodiac_eng, zodiac_ru in zodiac_map.items():
            message = format_horoscope(await get_horoscope_mail_ru(zodiac_eng))
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
    removed = _purge_stale_downloads(max_age_sec=0)
    if removed:
        logger.info(f"Cleaned {removed} files from downloads")
    trim_memory()


def _purge_stale_downloads(max_age_sec: int) -> int:
    import time

    removed = 0
    now = time.time()
    for file in DOWNLOADS_DIR.glob("*"):
        if not file.is_file():
            continue
        try:
            if max_age_sec and (now - file.stat().st_mtime) < max_age_sec:
                continue
            file.unlink()
            removed += 1
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to delete {file}: {e}")
    return removed


async def purge_downloads_loop(bot):
    while True:
        await asyncio.sleep(1800)
        removed = _purge_stale_downloads(DOWNLOAD_TTL_SEC)
        if removed:
            logger.info(f"Purged {removed} stale download files older than {DOWNLOAD_TTL_SEC}s")
        trim_memory()


async def check_cbr_update(bot):
    """
    Проверяет обновление курсов ЦБ РФ:
    - До 17:00 МСК: каждый час
    - С 17:00 до 19:00 МСК: каждые 3 минуты
    При обновлении отправляет уведомление с курсами.
    """
    from datetime import timezone

    from core.redis_client import get_redis
    from tg_bot.routers.currencies import build_cbr_message

    redis_key = "cbr:notified_date"
    moscow_tz = timezone(timedelta(hours=3))  # Moscow UTC+3
    check_interval = 3600

    while True:
        try:
            # Определяем текущее московское время
            moscow_now = datetime.now(moscow_tz)
            current_hour = moscow_now.hour

            # Определяем интервал проверки
            if current_hour < 17:
                # До 17:00 - каждый час
                check_interval = 3600  # 1 час
            elif current_hour < 19:
                # С 17:00 до 19:00 - каждые 3 минуты
                check_interval = 180  # 3 минуты
            else:
                # После 19:00 - каждый час до следующего дня
                check_interval = 3600

            async with httpx.AsyncClient() as session:
                # Получаем последнюю дату
                response = await session.get(f"{BACKEND_ROUTE}/markets/cbr/last-date")
                response.raise_for_status()
                current_date = response.json()["date"]

                # Проверяем Redis - отправляли ли уже уведомление для этой даты
                redis = await get_redis()
                last_notified = await redis.get(redis_key)

                if last_notified != current_date:
                    # Новая дата! Отправляем уведомление
                    logger.info(f"New CBR date detected: {current_date} (was: {last_notified})")

                    # Используем стандартную функцию для формирования сообщения
                    # Показываем только основные валюты: USD, EUR, CNY
                    message = await build_cbr_message(requested_codes=["USD", "EUR", "CNY", "BYN"])

                    # Добавляем заголовок уведомления
                    message = f"🔔 <b>Обновление курсов ЦБ РФ</b>\n\n{message}"

                    # Отправляем в чат
                    await bot.send_message(OBZHORA_CHAT_ID, message, parse_mode="html")

                    # Сохраняем дату в Redis
                    await redis.set(redis_key, current_date)
                    logger.info(f"CBR update notification sent for {current_date}")

        except Exception as e:
            logger.error(f"Error in check_cbr_update: {e}")

        # Ждём до следующей проверки
        await asyncio.sleep(check_interval)


async def on_startup(bot):
    for coro in (
        scheduled_message(bot),
        # send_daily_horoscope_for_brothers(bot),
        send_daily_tracker_messages(bot),
        cleanup_downloads(bot),
        purge_downloads_loop(bot),
        check_cbr_update(bot),
        base_notifications.poll_redis(bot),
        image_selection.poll_image_selection(bot),
    ):
        asyncio.create_task(coro)
