from datetime import datetime, timezone
import json
import uuid
from typing import Any, List

from aiogram import F, Router
from aiogram.enums.parse_mode import ParseMode
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.config import OBZHORA_CHAT_ID
from core.logger import logger
from core.locks import force_release_hailuo_lock
from core.redis_client import get_redis

video_router = Router()

ANIMATION_PROMPTS_PREFIX = "video_gen:anim_prompts:"
IMAGE_PROMPTS_PREFIX = "image_gen:img_prompts:"
QUEUE_NAME = "hailuo_tasks"


@video_router.message(Command("force_unlock_hailuo"))
async def handle_unlock_hailuo_account(message: Message):
    """
    Ручное удаление блокировки аккаунта Hailuo.
    """
    if message.chat.id != int(OBZHORA_CHAT_ID):  # Белый листик нужен только для админов
        await message.reply("❌ У вас нет прав на выполнение этой команды.")
        return
    is_not_error = await force_release_hailuo_lock()
    if not is_not_error:
        await message.reply("❌ Ошибка при разблокировке аккаунта Hailuo.")
        return

    try:
        redis = await get_redis()
        deleted_count = await redis.delete(QUEUE_NAME)
        if deleted_count > 0:
            logger.info(f"Очередь '{QUEUE_NAME}' успешно очищена (ключ удален).")
        else:
            logger.info(f"Очередь '{QUEUE_NAME}' уже была пуста или не существовала.")
    except (ConnectionError, TimeoutError) as err:
        logger.error(f"Не удалось подключиться к Redis для очистки очереди: {err}")
    except Exception as e:
        logger.exception(
            f"Ошибка при очистке очереди '{QUEUE_NAME}': {e}", exc_info=True
        )

    await message.reply("✅ Аккаунт Hailuo разблокирован и очередь очищена.")


@video_router.message(Command("generate_video"))
async def handle_generate_video_command(message: Message):
    """
    Обработчик команды /generate_video.
    Объясняет пользователю, как начать генерацию видео.
    """
    help_text = (
        "🎬 <b>Генерация видео</b>\n\n"
        "Чтобы создать видео, отправьте мне два файла:\n"
        "1. <code>промпты.img.txt</code> - файл с промптами для изображений (по одному на строку)\n"
        "2. <code>промпты.anim.txt</code> - файл с промптами для анимаций (по одному на строку)\n\n"
        "После загрузки файлов я начну процесс генерации видео. Вам нужно будет "
        "выбрать изображения для каждой сцены, а затем я создам готовое видео.\n\n"
        "<i>Совет: Можно отправить оба файла одновременно и добавить команду /start_generation в описании медиагруппы.</i>"
    )
    await message.reply(help_text, parse_mode=ParseMode.HTML)


@video_router.message(
    F.document.file_name.endswith(".img.txt")
    | F.document.file_name.endswith(".anim.txt")
    | F.document.file_name.starts_with("get")
    | F.document.file_name.contains("prompts")
)
async def handle_prompt_file(message: Message):
    """
    Обработчик файлов .img.txt и .anim.txt с промптами.
    Сохраняет промпты и запускает генерацию видео при наличии обоих файлов.
    """
    if not message.document:
        return

    # Используем комбинацию chat_id и user_id для уникальной идентификации
    chat_id = str(message.chat.id)
    user_id = str(message.from_user.id) if message.from_user else "unknown"
    session_key = f"{chat_id}:{user_id}"

    # Создаем ключи Redis для хранения промптов
    img_prompts_key = f"{IMAGE_PROMPTS_PREFIX}{session_key}"
    anim_prompts_key = f"{ANIMATION_PROMPTS_PREFIX}{session_key}"

    # Скачиваем файл
    if message.bot is None:
        logger.error("Бот не инициализирован. Не удалось скачать файл.")
        return

    file = await message.bot.get_file(message.document.file_id)
    if file is None or file.file_path is None:
        logger.error("Не удалось получить файл.")
        return

    file_path = await message.bot.download_file(file.file_path)
    if file_path is None:
        logger.error("Не удалось скачать файл.")
        return
    # Читаем содержимое файла и разбиваем на строки
    content = file_path.read().decode("utf-8").strip()
    prompts = [line.strip() for line in content.split("\n") if line.strip()]

    # Проверяем, есть ли уже сохраненные промпты
    existing_prompts_json = None
    overwrite_message = ""

    if message.document.file_name is None:
        logger.error("Нет имени файла")
        return
    file_name = message.document.file_name
    redis = await get_redis()
    # Сохраняем промпты в Redis в зависимости от типа файла
    if (
        file_name.endswith(".img.txt")
        or file_name == "get_images.txt"
        or file_name == "image_prompts.txt"
    ):
        # Проверяем, есть ли уже промпты для изображений
        existing_prompts_json = await redis.get(img_prompts_key)
        if existing_prompts_json:
            overwrite_message = "⚠️ Промпты для изображений были перезаписаны.\n\n"

        await redis.set(img_prompts_key, json.dumps(prompts))
        await message.reply(
            f"{overwrite_message}✅ Получено {len(prompts)} промптов для изображений.\n\n"
            f"Теперь отправьте файл с промптами для анимаций (<code>промпты.anim.txt</code>), "
            f"если вы ещё этого не сделали.",
            parse_mode=ParseMode.HTML,
        )
    elif (
        file_name.endswith(".anim.txt")
        or file_name == "get_animations.txt"
        or file_name == "animation_prompts.txt"
    ):
        # Проверяем, есть ли уже промпты для анимаций
        existing_prompts_json = await redis.get(anim_prompts_key)
        if existing_prompts_json:
            overwrite_message = "⚠️ Промпты для анимаций были перезаписаны.\n\n"

        await redis.set(anim_prompts_key, json.dumps(prompts))
        await message.reply(
            f"{overwrite_message}✅ Получено {len(prompts)} промптов для анимаций.\n\n"
            f"Теперь отправьте файл с промптами для изображений (<code>промпты.img.txt</code>), "
            f"если вы ещё этого не сделали.",
            parse_mode=ParseMode.HTML,
        )

    # Проверяем наличие обоих типов промптов
    has_img_prompts = await redis.exists(img_prompts_key)
    has_anim_prompts = await redis.exists(anim_prompts_key)

    # Если есть оба типа промптов и сообщение содержит /start_generation, запускаем генерацию
    if has_img_prompts and has_anim_prompts:
        # Проверяем есть ли команда в caption сообщения
        should_start_generation = False
        if message.caption and "/start_generation" in message.caption:
            should_start_generation = True

        # Получаем промпты
        img_prompts_json = await redis.get(img_prompts_key)
        anim_prompts_json = await redis.get(anim_prompts_key)

        img_prompts = json.loads(img_prompts_json)
        anim_prompts = json.loads(anim_prompts_json)

        # Проверяем, что количество промптов совпадает
        if len(img_prompts) != len(anim_prompts):
            await message.reply(
                "⚠️ <b>Внимание:</b> количество промптов для изображений и анимаций не совпадает!\n\n"
                f"У вас {len(img_prompts)} промптов для изображений и {len(anim_prompts)} промптов для анимаций.\n"
                f"Рекомендуется, чтобы количество было одинаковым. Но если вы уверены, что всё правильно, "
                f"отправьте команду /start_generation для запуска <b>ВСЕГО</b> процесса или /pipeline_menu для запуска "
                f"<b>ОТДЕЛЬНЫХ</b> пайплайнов.",
                parse_mode=ParseMode.HTML,
            )
        else:
            # Если была команда в caption или просто есть оба типа промптов, запускаем генерацию
            if should_start_generation:
                await start_video_generation(message, img_prompts, anim_prompts)
            else:
                await message.reply(
                    "✅ <b>Все файлы получены!</b>\n\n"
                    "Теперь отправьте команду /start_generation для запуска генерации видео. Или /pipeline_menu для запуска отдельных пайплайнов.",
                    parse_mode=ParseMode.HTML,
                )


@video_router.message(Command("start_generation"))
async def handle_start_generation(message: Message):
    """
    Обработчик команды /start_generation.
    Запускает процесс генерации видео на основе сохраненных промптов.
    """
    # Используем комбинацию chat_id и user_id для уникальной идентификации
    chat_id = str(message.chat.id)
    user_id = str(message.from_user.id) if message.from_user else "unknown"
    session_key = f"{chat_id}:{user_id}"

    # Создаем ключи Redis для хранения промптов
    img_prompts_key = f"{IMAGE_PROMPTS_PREFIX}{session_key}"
    anim_prompts_key = f"{ANIMATION_PROMPTS_PREFIX}{session_key}"

    # Проверяем наличие обоих типов промптов
    redis = await get_redis()
    has_img_prompts = await redis.exists(img_prompts_key)
    has_anim_prompts = await redis.exists(anim_prompts_key)

    if not has_img_prompts or not has_anim_prompts:
        missing = []
        if not has_img_prompts:
            missing.append("промптов для изображений")
        if not has_anim_prompts:
            missing.append("промптов для анимаций")

        await message.reply(
            f"❌ Не хватает {' и '.join(missing)}!\n\n"
            f"Пожалуйста, отправьте необходимые файлы с промптами перед началом генерации.",
            parse_mode=ParseMode.HTML,
        )
        return

    # Получаем промпты
    img_prompts_json = await redis.get(img_prompts_key)
    anim_prompts_json = await redis.get(anim_prompts_key)

    img_prompts = json.loads(img_prompts_json)
    anim_prompts = json.loads(anim_prompts_json)

    # Запускаем процесс генерации
    await start_video_generation(message, img_prompts, anim_prompts)


async def start_video_generation(
    message: Message, img_prompts: List[str], anim_prompts: List[str]
):
    """
    Запускает процесс генерации видео.

    Args:
        message: Сообщение пользователя
        img_prompts: Список промптов для изображений
        anim_prompts: Список промптов для анимаций
    """
    # Получаем user_id и chat_id для создания session_key
    chat_id = str(message.chat.id)
    user_id = str(message.from_user.id) if message.from_user else "unknown"
    session_key = f"{chat_id}:{user_id}"

    # Создаем задачу для воркера
    task_id = str(uuid.uuid4())

    task = {
        "task_id": task_id,
        "type": "video_generation",
        "created_at": message.date.isoformat(),
        "data": {
            "user_id": message.chat.id,
            "image_prompts": img_prompts,
            "animation_prompts": anim_prompts,
        },
    }

    # Отправляем сообщение о начале генерации
    await message.reply(
        f"🎬 <b>Генерация видео началась!</b>\n\n"
        f"• Количество сцен: <b>{len(img_prompts)}</b>\n"
        f"• ID задачи: <code>{task_id}</code>\n\n"
        f"Я отправлю вам варианты изображений для выбора. После выбора всех изображений "
        f"начнется создание анимаций и финального видео.",
        parse_mode=ParseMode.HTML,
    )

    # Добавляем задачу в очередь Redis
    redis = await get_redis()
    await redis.rpush(QUEUE_NAME, json.dumps(task))  # type: ignore
    logger.info(f"Задача на генерацию видео {task_id} добавлена в очередь")

    # Очищаем промпты из Redis
    await redis.delete(f"{IMAGE_PROMPTS_PREFIX}{session_key}")
    await redis.delete(f"{ANIMATION_PROMPTS_PREFIX}{session_key}")


PIPELINE_CALLBACK_PREFIX = "run_pipeline:"

# Define pipeline types and their user-friendly names
PIPELINE_BUTTONS_CONFIG = {
    "image_generation": "🖼️ Генерация Изображений",
    "animation_generation": "✨ Генерация Анимаций",
    "concat_animations": "🔗 Объединить Анимации",
    "set_animations_force": "🔄 Принудительно установить анимации",
    "delete_image_folder": "🗑️ Очистить папку изображений воркера",
    "reset_worker_session": "🔄 Сбросить сессию воркера",
}


@video_router.message(Command("pipeline_menu"))
async def show_pipeline_menu(message: Message):
    """
    Displays a menu with buttons to trigger individual worker pipelines.
    """
    builder = InlineKeyboardBuilder()
    for pipeline_type, text in PIPELINE_BUTTONS_CONFIG.items():
        builder.button(
            text=text, callback_data=f"{PIPELINE_CALLBACK_PREFIX}{pipeline_type}"
        )

    builder.adjust(1)  # Display one button per row
    await message.reply(
        "Выберите пайплайн для запуска:", reply_markup=builder.as_markup()
    )


async def enqueue_pipeline_task(
    pipeline_type: str,
    chat_id_for_context: int,
    message_date: datetime,  # Added for created_at
    specific_data: dict[str, Any] = {},
    task_id: str = str(uuid.uuid4()),  # Generate a new task ID if not provided
):
    """
    Helper function to create and enqueue a task for a specific pipeline.
    """
    # Basic data common to all tasks
    task_data_payload = {
        "user_id": chat_id_for_context,  # For notifications from worker
        # worker_id will be defaulted by the worker if not provided
    }
    if specific_data:
        task_data_payload.update(specific_data)

    task = {
        "task_id": task_id,
        "type": pipeline_type,
        "created_at": message_date.isoformat(),  # Use message date
        "data": task_data_payload,
    }

    redis = await get_redis()
    await redis.rpush(QUEUE_NAME, json.dumps(task))  # type: ignore
    logger.info(
        f"Задача для пайплайна '{pipeline_type}' (ID: {task_id}) добавлена в очередь."
    )
    return task_id


@video_router.callback_query(F.data.startswith(PIPELINE_CALLBACK_PREFIX))
async def handle_pipeline_button(callback_query: CallbackQuery):
    """
    Handles button presses from the pipeline menu.
    """
    if (
        callback_query.message is None
        or callback_query.from_user is None
        or callback_query.data is None
    ):
        await callback_query.answer(
            "Ошибка: не удалось обработать запрос.", show_alert=True
        )
        return

    pipeline_type_to_run = callback_query.data.split(PIPELINE_CALLBACK_PREFIX, 1)[1]

    chat_id = callback_query.message.chat.id
    user_id = str(callback_query.from_user.id)
    session_key = f"{str(chat_id)}:{user_id}"  # For fetching prompts if needed

    redis = await get_redis()
    specific_task_data: dict[str, Any] = {}

    # Prepare data based on pipeline type
    if pipeline_type_to_run == "image_generation":
        img_prompts_key = f"{IMAGE_PROMPTS_PREFIX}{session_key}"
        img_prompts_json = await redis.get(img_prompts_key)
        if not img_prompts_json:
            await callback_query.answer(
                "Промпты для изображений не найдены. Загрузите их сначала.",
                show_alert=True,
            )
            return
        specific_task_data["image_prompts"] = json.loads(img_prompts_json)

    elif pipeline_type_to_run in ["animation_generation", "concat_animations"]:
        anim_prompts_key = f"{ANIMATION_PROMPTS_PREFIX}{session_key}"
        anim_prompts_json = await redis.get(anim_prompts_key)
        if not anim_prompts_json:
            await callback_query.answer(
                "Промпты для анимаций не найдены. Загрузите их сначала.",
                show_alert=True,
            )
            return
        specific_task_data["animation_prompts"] = json.loads(anim_prompts_json)
        # The worker's AnimationGenerationPipeline is expected to use WorkerStatusManager
        # to get selected image paths based on its own worker_id.

    # For delete_image_folder, reset_session_worker, no specific data is fetched by the bot here.
    # The worker pipelines will handle their logic (e.g., using WorkerStatusManager).

    curr_date = callback_query.message.date
    if not isinstance(curr_date, datetime):
        curr_date = datetime.now(timezone.utc)
    else:
        curr_date = curr_date.replace(tzinfo=timezone.utc)  # Ensure it's timezone-aware

    try:
        task_id = await enqueue_pipeline_task(
            pipeline_type_to_run,
            chat_id,  # Pass chat_id for worker notifications
            curr_date,  # Pass original message date for created_at
            specific_task_data,
        )

        response_message_text = ""
        if pipeline_type_to_run == "reset_worker_session":
            response_message_text = (
                f"✅ Команда на сброс сессии воркера отправлена.\n"
                f"ID Задачи на сброс: <code>{task_id}</code>\n\n"
                "Теперь вы можете начать новый процесс генерации с чистого листа, используя кнопки."
            )
        elif pipeline_type_to_run == "set_animations_force":
            response_message_text = (
                f"✅ Команда на принудительное подтверждение наличия анимаций отправлена.\n"
                f"ID Задачи на сброс: <code>{task_id}</code>\n\n"
                "Теперь вы можете конкатенировать видео, используя кнопки."
            )
        else:
            response_message_text = (
                f"✅ Запущен пайплайн: <b>{PIPELINE_BUTTONS_CONFIG.get(pipeline_type_to_run, pipeline_type_to_run)}</b>\n"
                f"ID Задачи: <code>{task_id}</code>"
            )
        await callback_query.message.answer(
            response_message_text, parse_mode=ParseMode.HTML
        )
        await callback_query.answer(
            f"Пайплайн '{PIPELINE_BUTTONS_CONFIG.get(pipeline_type_to_run, pipeline_type_to_run)}' запущен!"
        )
    except Exception as e:
        logger.error(
            f"Ошибка при запуске пайплайна '{pipeline_type_to_run}': {e}", exc_info=True
        )
        await callback_query.answer(
            f"Ошибка при запуске пайплайна: {e}", show_alert=True
        )
