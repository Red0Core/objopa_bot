import asyncio
import time

import ujson
from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from redis.exceptions import BusyLoadingError, ConnectionError, TimeoutError

from core.config import UPLOAD_DIR
from core.logger import logger
from core.redis_client import get_redis


async def poll_image_selection(bot: Bot):
    """
    Слушатель задач image_selection.
    Получает задачи из Redis и отправляет пользователю альбом с изображениями и инлайн-кнопками для выбора.
    """
    logger.info("Запущен слушатель задач image_selection...")
    while True:
        try:
            r = await get_redis()
        except (ConnectionError, TimeoutError, BusyLoadingError):
            logger.warning("[image_selection] Redis сдох временно")
            await asyncio.sleep(2)
            continue
        # Получаем все ключи задач image_selection из Redis
        keys = await r.keys("notifications:image_selection:*")
        for key in keys:
            # JSON в строке
            task_raw: str = await r.lpop(key) # type: ignore
            if not task_raw:
                continue

            try:
                task_data = ujson.loads(task_raw)
                logger.info(f"Получена задача: {task_data}")
                # Данные задачи в backend/models/workers.py
                user_id: str = task_data["data"]["user_id"]
                task_id: str = task_data["task_id"]
                relative_paths: list[str] = task_data["data"]["relative_paths"] # Может быть список ссылок на изображения или файлы

                if len(f"select_image:{task_id}:1".encode('utf-8')) > 64:
                    logger.warning(f"Callback data слишком длинная: {len(f"select_image:{task_id}:1".encode('utf-8'))} байт")
                    # Возможно, используйте более короткий task_id

                # Создаём ряды кнопок для 4 изображений + 1 кнопка пересоздания
                selection_buttons_rows: list[list[InlineKeyboardButton]] = []

                # Ряд с кнопками выбора изображений (1, 2, 3, 4)
                image_buttons_row = []
                for i in range(len(relative_paths)): # Должно быть 4 итерации
                    button = InlineKeyboardButton(text=f"{i+1}", callback_data=f"select_image:{task_id}:{i}")
                    image_buttons_row.append(button)
                selection_buttons_rows.append(image_buttons_row)

                # Ряд с кнопкой "Пересоздать"
                regenerate_button = InlineKeyboardButton(text="🔄 Пересоздать", callback_data=f"select_image:{task_id}:-1")
                selection_buttons_rows.append([regenerate_button]) # Кнопка в своем ряду

                keyboard = InlineKeyboardMarkup(inline_keyboard=selection_buttons_rows)
                media = [InputMediaPhoto(media=FSInputFile(UPLOAD_DIR.joinpath(relative_path))) for relative_path in relative_paths]

                # Отправляем альбом + кнопки
                for retry in range(4, 8):
                    try:
                        start = time.time()
                        msgs = await bot.send_media_group(chat_id=user_id, media=media) # type: ignore
                        for msg in msgs:
                            await r.lpush(f"delete:tg_messages_id:{user_id}:{task_id}", msg.message_id) # type: ignore
                        await bot.send_message(chat_id=user_id, text="Выберите одно изображение:", reply_markup=keyboard, reply_to_message_id=msgs[0].message_id) # type: ignore
                        logger.info(f"Отправлено сообщение с изображениями и кнопками за {time.time() - start} к {user_id}.")
                        break
                    except (TelegramNetworkError, TelegramRetryAfter) as e:
                        logger.warning(f"Ошибка Telegram: {e}. Повтор отправки через {2**retry} секунд...")
                        await asyncio.sleep(2**retry)

            except Exception as e:
                logger.exception(f"[image_selection_worker] Ошибка обработки задачи: {e}")

        await asyncio.sleep(2)
