from aiogram import Router, F
from aiogram.types import CallbackQuery
from tg_bot.redis_workers.base_notifications import r
from core.logger import logger

router = Router()

@router.callback_query(lambda c: "select_image" in c.data)
async def handle_selection(callback: CallbackQuery):
    logger.info(f"Получен callback: {callback.data}")
    if not callback.data or not callback.message:
        await callback.answer("Ошибка: данные не найдены", show_alert=True)
        return

    _, task_id, index = callback.data.split(":")
    selected_index = int(index) + 1
    
    # Короткое уведомление
    await callback.answer(f"Вы выбрали изображение №{selected_index}", show_alert=True)
    
    # Обновляем текст сообщения и убираем кнопки
    if not callback.message.text and not callback.message.caption:
        await callback.answer("Ошибка: текст сообщения не найден", show_alert=True)
        return
    else:
        current_text = callback.message.text or callback.message.caption or "Выберите изображение:"
        new_text = f"{current_text}\n\n✅ <b>Выбрано изображение №{selected_index}</b>"
        
        # Обновляем сообщение с новым текстом и без кнопок
        try:
            # Если есть фото, нужно редактировать caption
            if callback.message.photo:
                await callback.message.edit_caption(
                    caption=new_text,
                    reply_markup=None,
                    parse_mode="HTML"
                )
            else:
                # Для обычных текстовых сообщений
                await callback.message.edit_text(
                    text=new_text,
                    reply_markup=None,
                    parse_mode="HTML"
                )
        except Exception as e:
            logger.error(f"Ошибка при обновлении сообщения: {e}")
    
    # Отправляем результат выбора в Redis
    await r.set(f"result:image_selection:{task_id}", index)
    logger.info(f"Пользователь выбрал изображение №{selected_index} для задачи {task_id}")