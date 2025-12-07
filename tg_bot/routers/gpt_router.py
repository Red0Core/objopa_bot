import tempfile
from pathlib import Path
from typing import cast

import aiofiles
import ujson
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from core.config import GEMINI_API_KEY, STORAGE_DIR
from core.logger import logger
from tg_bot.services.gpt import (
    AIModelError,
    APIKeyError,
    GeminiChatModel,
    GeminiFile,
    GeminiModel,
    ModelOverloadedError,
    QuotaExceededError,
    RateLimitError,
    UnexpectedResponseError,
    get_gpt_formatted_chunks,
)
from tg_bot.services.gptchat_manager import ChatSessionManager
from tg_bot.services.pastebin import upload_to_pastebin

router = Router()

WHITELIST_PATH = STORAGE_DIR / "whitelist_gpt.json"


def load_whitelist(file_path=WHITELIST_PATH):
    try:
        with open(file_path, "r") as file:
            return ujson.load(file)
    except FileNotFoundError:
        return {}


async def save_whitelist(data, file_path=WHITELIST_PATH):
    async with aiofiles.open(file_path, "w") as file:
        await file.write(ujson.dumps(data, indent=4))


whitelist = load_whitelist()

# --- Поддерживаемые MIME-типы (вынесены для удобства) ---
SUPPORTED_MIMES = {
    "document": ["text/plain", "application/pdf"],
    "photo": ["image/jpeg", "image/png", "image/webp"],
    "audio": [
        "audio/aac",
        "audio/flac",
        "audio/mp3",
        "audio/m4a",
        "audio/mpeg",
        "audio/mpga",
        "audio/mp4",
        "audio/opus",
        "audio/pcm",
        "audio/wav",
        "audio/webm",
    ],
    "video": [
        "video/x-flv",
        "video/quicktime",
        "video/mpeg",
        "video/mpegps",
        "video/mpg",
        "video/mp4",
        "video/webm",
        "video/wmv",
        "video/3gpp",
        "video/avi",
    ],
}


# --- Хендлер для команды /ask ---
@router.message(Command("ask"))
async def handle_ask_gpt(message: Message):
    # Список для хранения объектов GeminiFile, которые будут скачаны и добавлены в модель.
    # Файлы будут удалены локально методом get_response после попытки загрузки в Gemini File API.
    gemini_files_to_add: list[GeminiFile] = []
    ask_command_text: str = ""
    AI_CLIENT = GeminiModel(api_key=GEMINI_API_KEY)

    try:
        # 1. Извлекаем текстовый промпт из аргумента команды /ask
        if message.text and len(message.text.split(maxsplit=1)) > 1:
            ask_command_text = message.text.split(maxsplit=1)[1].strip()

        # 2. Определяем, откуда брать файл: из ответа на сообщение или из самого сообщения
        source_message = message.reply_to_message if message.reply_to_message else message

        # 2.1. Если есть ответ на сообщение, добавляем его текст в контекст
        replied_text = ""
        if message.reply_to_message:
            if message.reply_to_message.text:
                replied_text = message.reply_to_message.text
            elif message.reply_to_message.caption:
                replied_text = message.reply_to_message.caption

        # Формируем итоговый промпт
        final_prompt_text = ""
        if replied_text:
            final_prompt_text += f"Контекст из предыдущего сообщения:\n---\n{replied_text}\n---\n\n"
        final_prompt_text += ask_command_text

        file_obj_to_download = None
        current_file_mime_type: str | None = None

        # 3. Проверяем наличие и тип прикрепленного файла в source_message
        if source_message.document:
            file_obj_to_download = source_message.document
            current_file_mime_type = file_obj_to_download.mime_type
            if current_file_mime_type not in SUPPORTED_MIMES["document"]:
                await message.answer(
                    f"Извините, для /ask я поддерживаю только текстовые (.txt) и PDF (.pdf) файлы. Тип файла: {current_file_mime_type}."
                )
                return
        elif source_message.photo:
            file_obj_to_download = source_message.photo[-1]  # Берем самую большую версию фото
            current_file_mime_type = "image/jpeg"  # Примерный MIME-тип для фото
        elif source_message.audio:
            file_obj_to_download = source_message.audio
            current_file_mime_type = file_obj_to_download.mime_type
            if current_file_mime_type not in SUPPORTED_MIMES["audio"]:
                await message.answer(
                    f"Извините, для /ask я поддерживаю только определенные аудиоформаты. Тип файла: {current_file_mime_type}."
                )
                return
        elif source_message.video:
            file_obj_to_download = source_message.video
            current_file_mime_type = file_obj_to_download.mime_type
            if current_file_mime_type not in SUPPORTED_MIMES["video"]:
                await message.answer(
                    f"Извините, для /ask я поддерживаю только определенные видеоформаты. Тип файла: {current_file_mime_type}."
                )
                return

        # 4. Если файл найден, скачиваем его и подготавливаем для Gemini
        if file_obj_to_download:
            # Создаем временный файл для сохранения документа
            with tempfile.NamedTemporaryFile(delete=False, dir=STORAGE_DIR) as temp_file:
                temp_file_path = Path(temp_file.name)

            await message.bot.download(file_obj_to_download, destination=temp_file_path)
            gemini_files_to_add.append(GeminiFile(file_path=temp_file_path, mime_type=current_file_mime_type))
            logger.info(f"Downloaded file for /ask: {temp_file_path} (MIME: {current_file_mime_type})")

            # Если явный текстовый промпт не был задан, формируем его по умолчанию
            if not ask_command_text:
                default_prompt = ""
                if source_message.photo:
                    default_prompt = "Проанализируй это изображение."
                elif source_message.audio:
                    default_prompt = "Проанализируй этот аудиофайл."
                elif source_message.video:
                    default_prompt = "Проанализируй это видео."
                elif source_message.document:
                    default_prompt = "Проанализируй этот документ."
                else:  # Fallback
                    default_prompt = "Проанализируй прикрепленный файл."
                final_prompt_text += default_prompt

        # 5. Финальная проверка: есть ли что-либо для отправки в модель?
        if not final_prompt_text.strip() and not gemini_files_to_add:
            await message.answer(
                "Использование: /ask <ваш вопрос> или ответьте на документ/фото/аудио/видео с /ask <ваш вопрос>"
            )
            return

        # 6. Добавляем все собранные файлы в AI_CLIENT (GeminiModel instance)
        for g_file in gemini_files_to_add:
            AI_CLIENT.add_file(g_file)

        # 7. Получаем ответ от AI_CLIENT
        edit_message = await message.answer("Анализирую... Пожалуйста, подождите.")
        response_text = await AI_CLIENT.get_response(final_prompt_text.strip())

        # 8. Отправляем ответ пользователю
        prev_message = message
        chunks = get_gpt_formatted_chunks(response_text)
        if chunks:
            await edit_message.edit_text(chunks[0], parse_mode="MarkdownV2")
            for chunk in chunks[1:]:
                prev_message = await prev_message.reply(chunk, parse_mode="MarkdownV2")
        else:
            await edit_message.edit_text("Модель вернула пустой ответ.")

    except APIKeyError:
        await message.answer("Ошибка: Неверный API-ключ. Обратитесь к администратору.")
    except RateLimitError:
        await message.answer("Ошибка: Превышен лимит запросов. Попробуйте позже.")
    except QuotaExceededError:
        await message.answer("Ошибка: Превышена квота использования API.")
    except UnexpectedResponseError:
        await message.answer("Ошибка: Непредвиденный ответ от модели. Попробуйте позже.")
    except AIModelError as e:
        await message.answer(f"Ошибка: {str(e)}")
    except Exception as e:
        logger.exception("Произошла непредвиденная ошибка в handle_ask_gpt:")
        await message.answer(f"Произошла непредвиденная ошибка: {e}")
    # ! Блок `finally` для удаления файлов здесь не нужен, так как удаление происходит в GeminiModel.get_response !


# --- Хендлер для команды /chat ---
@router.message(Command("chat"))
async def start_session(message: Message):
    chat_id = message.chat.id
    chat_manager = ChatSessionManager()

    if not await chat_manager.get_chat(chat_id):
        chat_model = GeminiChatModel(api_key=GEMINI_API_KEY)
        text_input = message.text.split(maxsplit=1) if message.text else ""
        system_prompt = text_input[1] if len(text_input) > 1 else ""

        chat_model.new_chat(system_prompt)
        await chat_manager.create_chat(chat_id, chat_model)

        await message.answer(
            "Чат создан! Можете начать общение.\n"
            "Сессия автоматически удаляется через час после безыспользования\n"
            "Вы можете добавить системный промпт после '/chat'"
        )
    else:
        await message.answer("Вы уже в чате. Продолжайте диалог.")


# --- Хендлер для команды /stopchat ---
@router.message(Command("stopchat"))
async def stop_session(message: Message):
    chat_id = message.chat.id
    chat_manager = ChatSessionManager()

    if await chat_manager.get_chat(chat_id):
        await chat_manager.remove_chat(chat_id)
        await message.answer("Чат остановлен!")


# --- Хендлер для команды /add_me_as ---
@router.message(Command("add_me_as"))
async def add_user_to_whitelist(message: Message):
    text_input = message.text.split(maxsplit=1) if message.text else ""
    if len(text_input) < 2 or not message.from_user:
        await message.reply("Использование: /add_me_as <имя>")
        return
    custom_name = text_input[1]
    if message.chat.id not in whitelist:
        whitelist[message.chat.id] = {}
    whitelist[message.chat.id][message.from_user.id] = custom_name
    await save_whitelist(whitelist)
    await message.reply(f"Пользователь '{custom_name}' (ID: {message.from_user.id}) добавлен в белый список.")


# --- Хендлер для обработки сообщений в чате (текст, документы, фото, аудио, видео) ---
@router.message(
    (F.text & ~F.text.startswith("/"))
    | (F.caption & ~F.caption.startswith("/"))
    | F.document
    | F.photo
    | F.audio
    | F.video
)
async def handle_gpt_chat(message: Message):
    chat_id = message.chat.id
    from_user = message.from_user

    # Ignore commands in text or caption
    if (message.text and message.text.startswith("/")) or (message.caption and message.caption.startswith("/")):
        return

    text_content: str | None = message.text or message.caption

    chat_manager = ChatSessionManager()
    chat_session = await chat_manager.get_chat(chat_id)
    chat_session = cast(GeminiChatModel, chat_session)

    if not chat_session:
        if message.chat.type == "private":
            await message.answer("Пожалуйста, начните чат с помощью команды /chat.")
        return

    # Список для хранения объектов GeminiFile, скачанных из ТЕКУЩЕГО сообщения Telegram.
    # Эти файлы будут добавлены в files_to_upload модели, а затем удалены самой моделью.
    current_message_gemini_files: list[GeminiFile] = []

    try:
        file_obj_to_download = None
        current_file_mime_type: str | None = None

        # Определяем и обрабатываем прикрепленные файлы из текущего сообщения
        if message.document:
            file_obj_to_download = message.document
            current_file_mime_type = file_obj_to_download.mime_type
            if current_file_mime_type not in SUPPORTED_MIMES["document"]:
                await message.answer(
                    f"Извините, я поддерживаю только текстовые (.txt) и PDF (.pdf) файлы для чата. Тип файла: {current_file_mime_type}."
                )
                return
        elif message.photo:
            file_obj_to_download = message.photo[-1]
            current_file_mime_type = "image/jpeg"
        elif message.audio:
            file_obj_to_download = message.audio
            current_file_mime_type = file_obj_to_download.mime_type
            if current_file_mime_type not in SUPPORTED_MIMES["audio"]:
                await message.answer(
                    f"Извините, я поддерживаю только определенные аудиоформаты для чата. Тип файла: {current_file_mime_type}."
                )
                return
        elif message.video:
            file_obj_to_download = message.video
            current_file_mime_type = file_obj_to_download.mime_type
            if current_file_mime_type not in SUPPORTED_MIMES["video"]:
                await message.answer(
                    f"Извините, я поддерживаю только определенные видеоформаты для чата. Тип файла: {current_file_mime_type}."
                )
                return

        # Если файл обнаружен, скачиваем его
        if file_obj_to_download:
            with tempfile.NamedTemporaryFile(delete=False, dir=STORAGE_DIR) as temp_file:
                temp_file_path = Path(temp_file.name)

            await message.bot.download(file_obj_to_download, destination=temp_file_path)
            current_message_gemini_files.append(GeminiFile(file_path=temp_file_path, mime_type=current_file_mime_type))
            logger.info(f"Downloaded file for chat: {temp_file_path} (MIME: {current_file_mime_type})")

        # Если нет ни текста, ни прикрепленных файлов в текущем сообщении, пропускаем
        if not text_content and not current_message_gemini_files:
            logger.debug(f"No text or files to process for chat_id {chat_id}. Skipping send.")
            return

        # Добавляем файлы из текущего сообщения в сессию чата.
        # Они будут "ждать" отправки в Gemini, пока не придет текстовое сообщение.
        for g_file in current_message_gemini_files:
            chat_session.add_file(g_file)

        # --- Ключевая логика для медиагрупп: отправляем сообщение в Gemini ТОЛЬКО при наличии текста/подписи ---
        if text_content:
            ids = whitelist.get(chat_id, {})
            user_name = ids.get(
                from_user.id,
                f"{from_user.first_name} {from_user.last_name or ''}",
            )

            logger.info(f"GPT запрос от {user_name} в чате {chat_id} ({from_user.username})")

            # 1. Извлекаем текст из сообщения, на которое отвечают
            replied_text = ""
            if message.reply_to_message:
                if message.reply_to_message.text:
                    replied_text = message.reply_to_message.text
                elif message.reply_to_message.caption:
                    replied_text = message.reply_to_message.caption

            # 2. Формируем итоговый промпт с учетом контекста из ответа
            prompt_text_for_model = ""
            if replied_text:
                prompt_text_for_model += f"Контекст из предыдущего сообщения:\n---\n{replied_text}\n---\n\n"

            prompt_text_for_model += text_content.strip()

            # Если после всех обработок промпт пуст, но есть накопленные файлы в сессии,
            # генерируем промпт по умолчанию
            if not prompt_text_for_model.strip() and chat_session.files_to_upload:
                if message.photo:
                    prompt_text_for_model = "Проанализируй это изображение."
                elif message.audio:
                    prompt_text_for_model = "Проанализируй этот аудиофайл."
                elif message.video:
                    prompt_text_for_model = "Проанализируй это видео."
                elif message.document:
                    prompt_text_for_model = "Проанализируй этот документ."
                else:
                    prompt_text_for_model = "Проанализируй прикрепленные файлы."

            # Финальная проверка
            if not prompt_text_for_model.strip() and not chat_session.files_to_upload:
                await message.answer("Пожалуйста, введите текст или прикрепите файл для отправки.")
                return

            is_pastebin = prompt_text_for_model.endswith("pastebin")
            if is_pastebin:
                prompt_text_for_model = prompt_text_for_model[: -len("pastebin")].strip()

            final_prompt_for_model = f"{user_name}: {prompt_text_for_model}"

            edit_message = await message.answer("Обрабатываю... Пожалуйста, подождите.")
            response = await chat_session.send_message(final_prompt_for_model)

            if is_pastebin:
                paste_link = await upload_to_pastebin(response)
                await edit_message.edit_text(f"Ответ загружен: {paste_link}")
            else:
                prev_message = message
                chunks = get_gpt_formatted_chunks(response)
                if chunks:
                    await edit_message.edit_text(chunks[0], parse_mode="MarkdownV2")
                    for chunk in chunks[1:]:
                        prev_message = await prev_message.reply(chunk, parse_mode="MarkdownV2")
                else:
                    await edit_message.edit_text("Модель вернула пустой ответ.")
        else:
            if current_message_gemini_files:
                await message.reply("Файл(ы) получены. Отправьте текстовое сообщение, чтобы обработать их.")
            return

    except APIKeyError:
        await message.answer("Ошибка: Неверный API-ключ. Обратитесь к администратору.")
    except RateLimitError:
        await message.answer("Ошибка: Превышен лимит запросов. Попробуйте позже.")
    except QuotaExceededError:
        await message.answer("Ошибка: Превышена квота использования API.")
    except UnexpectedResponseError:
        await message.answer("Ошибка: Непредвиденный ответ от модели. Попробуйте позже.")
    except ModelOverloadedError:
        await message.answer("Ошибка: Модель временно перегружена. Пожалуйста, попробуйте позже.")
    except AIModelError as e:
        await message.answer(f"Ошибка: {str(e)}")
    except Exception as e:
        logger.exception("Произошла непредвиденная ошибка при отправке сообщения в GPT чат:")
        await message.answer(f"Произошла непредвиденная ошибка: {e}")
    # ! Блок `finally` для удаления файлов здесь не нужен, так как удаление происходит в GeminiChatModel.send_message !
