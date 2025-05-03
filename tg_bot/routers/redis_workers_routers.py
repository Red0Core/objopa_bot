from aiogram import Router
from aiogram.types import CallbackQuery

from core.logger import logger
from core.redis_client import get_redis

router = Router()

@router.callback_query(lambda c: c.data.startswith("select_image:"))
async def handle_selection(callback: CallbackQuery):
    logger.info(f"Получен callback: {callback.data}")
    if not callback.data or not callback.message:
        await callback.answer("Ошибка: данные не найдены", show_alert=True)
        return

    _, task_id, index = callback.data.split(":") # select_image:task_id:index
    selected_index = int(index) + 1
    
    redis = await get_redis()
    try:
        while True:
            msg_to_delete = await redis.lpop(f"delete:tg_messages_id:{callback.message.chat.id}:{task_id}") # type: ignore
            if not msg_to_delete:
                break
            await callback.bot.delete_message(chat_id=callback.message.chat.id, message_id=msg_to_delete) # type: ignore
        await callback.message.delete() # type: ignore
        logger.info(f"Сообщение с выбором изображений удалено {task_id}")
    except Exception as e:
        logger.error(f"Не удалось удалить сообщение: {e}")
    
    
    # Отправляем результат выбора в Redis
    await redis.set(f"result:image_selection:{task_id}", index)
    logger.info(f"Пользователь выбрал изображение №{selected_index} для задачи {task_id}")
