import asyncio
from core.redis_client import redis
import ujson
from aiogram import Bot
from core.config import UPLOAD_DIR
from core.logger import logger
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, FSInputFile
from tg_bot.redis_workers.base_notifications import r

async def poll_image_selection(bot: Bot):
    """
    Слушатель задач image_selection.
    Получает задачи из Redis и отправляет пользователю альбом с изображениями и инлайн-кнопками для выбора.
    """
    logger.info("Запущен слушатель задач image_selection...")
    while True:
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

                # Создаём инлайн-кнопки
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text=f"Выбрать {i+1}", callback_data=f"select_image:{task_id}:{i}")]
                        for i in range(len(relative_paths))
                    ]
                )

                media = [InputMediaPhoto(media=FSInputFile(UPLOAD_DIR.joinpath(relative_path))) for relative_path in relative_paths]

                # Отправляем альбом + кнопки
                msgs = await bot.send_media_group(chat_id=user_id, media=media) # type: ignore
                for msg in msgs:
                    await redis.lpush(f"delete:tg_messages_id:{user_id}:{task_id}", msg.message_id) # type: ignore
                await bot.send_message(chat_id=user_id, text="Выберите одно изображение:", reply_markup=keyboard, reply_to_message_id=msgs[0].message_id) # type: ignore

            except Exception as e:
                logger.exception(f"[image_selection_worker] Ошибка обработки задачи: {e}")

        await asyncio.sleep(2)
