from aiogram import Router
from aiogram.types import CallbackQuery
from tg_bot.redis_workers.base_notifications import r
from core.logger import logger

router = Router()

@router.callback_query(lambda c: c.data.startswith("select_image:"))
async def handle_selection(callback: CallbackQuery):
    logger.info(f"Получен callback: {callback.data}")
    if not callback.data or not callback.message:
        await callback.answer("Ошибка: данные не найдены", show_alert=True)
        return

    _, task_id, index = callback.data.split(":") # select_image:task_id:index
    selected_index = int(index) + 1
    
    # Короткое уведомление
    await callback.answer(f"Вы выбрали изображение №{selected_index}", show_alert=True)
    
     # Удаляем сообщение полностью
    try:
        await callback.message.delete() # type: ignore
        logger.info(f"Сообщение с выбором изображений удалено {task_id}")
    except Exception as e:
        logger.error(f"Не удалось удалить сообщение: {e}")
    
    
    # Отправляем результат выбора в Redis
    await r.set(f"result:image_selection:{task_id}", index)
    logger.info(f"Пользователь выбрал изображение №{selected_index} для задачи {task_id}")