import asyncio
import ujson
from aiogram import Bot
from core.logger import logger
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from tg_bot.redis_workers.base_notifications import r

async def poll_image_selection(bot: Bot):
    logger.info("Запущен слушатель задач image_selection...")
    while True:
        # Получаем все ключи задач
        keys = await r.keys("notifications:image_selection:*")
        for key in keys:
            task_raw: str = await r.lpop(key) # type: ignore
            if not task_raw:
                continue

            try:
                task_data = ujson.loads(task_raw)
                logger.info(f"Получена задача: {task_data}")
                user_id = task_data["data"]["user_id"]
                task_id = task_data["task_id"]
                images = task_data["data"]["images"]

                callback_data = f"select_image:{task_id}:1"
                if len(callback_data.encode('utf-8')) > 64:
                    logger.warning(f"Callback data слишком длинная: {len(callback_data.encode('utf-8'))} байт")
                    # Возможно, используйте более короткий task_id

                # Создаём инлайн-кнопки
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text=f"Выбрать {i+1}", callback_data=f"select_image:{task_id}:{i}")]
                        for i in range(len(images))
                    ]
                )

                media = [InputMediaPhoto(media=img_url) for img_url in images]

                # Отправляем альбом + кнопки
                msg = await bot.send_media_group(chat_id=user_id, media=media) # type: ignore
                await bot.send_message(chat_id=user_id, text="Выберите одно изображение:", reply_markup=keyboard)

            except Exception as e:
                logger.exception(f"[image_selection_worker] Ошибка обработки задачи: {e}")

        await asyncio.sleep(2)
