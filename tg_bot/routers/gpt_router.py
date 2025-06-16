import ujson
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from core.config import GEMINI_API_KEY, STORAGE_PATH
from core.logger import logger
from tg_bot.services.gpt import (
    AIModelError,
    APIKeyError,
    GeminiChatModel,
    QuotaExceededError,
    RateLimitError,
    UnexpectedResponseError,
    get_gpt_formatted_chunks,
)
from tg_bot.services.gptchat_manager import ChatSessionManager
from tg_bot.services.pastebin import upload_to_pastebin

from .mention_dice import AI_CLIENT

router = Router()

WHITELIST_PATH = STORAGE_PATH / "whitelist_gpt.json"


def load_whitelist(file_path=WHITELIST_PATH):
    try:
        with open(file_path, "r") as file:
            return ujson.load(file)
    except FileNotFoundError:
        return {}


def save_whitelist(data, file_path=WHITELIST_PATH):
    with open(file_path, "w") as file:
        ujson.dump(data, file, indent=4)


whitelist = load_whitelist()


@router.message(Command("ask"))
async def handle_ask_gpt(message: Message):
    try:
        if message.text:
            text_input = message.text.split(maxsplit=1)
            if len(text_input) < 2:
                await message.answer("Использование: /ask <ваш вопрос>")
                return
            # Генерируем объяснение через OpenAI API
            text = await AI_CLIENT.get_response(
                text_input[1],
            )
            for i in get_gpt_formatted_chunks(text):
                await message.answer(i, parse_mode="MarkdownV2")
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


@router.message(Command("chat"))
async def start_session(message: Message):
    chat_id = message.chat.id
    chat_manager = ChatSessionManager()

    if not chat_manager.get_chat(chat_id):
        chat_model = GeminiChatModel(api_key=GEMINI_API_KEY)
        text_input = message.text.split(maxsplit=1) if message.text else ""
        if len(text_input) > 1:
            chat_model.new_chat(text_input[1])
        else:
            chat_model.new_chat()
        chat_manager.create_chat(chat_id, chat_model)

        await message.answer(
            "Чат создан! Можете начать общение.\n"
            "Сессия автоматически удаляется через час после безыиспользования\n"
            "Вы можете добавить системный промпт после '/chat'"
        )
    else:
        await message.answer("Вы уже в чате. Продолжайте диалог.")


@router.message()
async def handle_gpt_chat(message: Message):
    text = message.text
    chat_id = message.chat.id
    from_user = message.from_user

    # Пропускаем команды
    if not text or text.startswith("/") or not from_user:
        return

    # Разрешить только ответы на сообщения от бота
    if (
        message.reply_to_message
        and message.reply_to_message.from_user
        and message.bot
        and message.reply_to_message.from_user.id != message.bot.id
    ):
        return

    # Проверяем: есть ли активная сессия?
    chat_manager = ChatSessionManager()
    chat_session = chat_manager.get_chat(chat_id)

    if not chat_session:
        # Нет активной сессии — не обрабатываем
        return

    # --- Обработка whitelist'а ---
    ids = whitelist.get(message.chat.id, {})
    user_name = ids.get(
        from_user.id,
        f"{from_user.first_name} {from_user.last_name or ''}",
    )

    logger.info(f"GPT ответ для {user_name} в чате {chat_id} ({from_user.username})")

    is_pastebin = text.strip().endswith("pastebin")
    prompt_text = text.replace("pastebin", "").strip()

    try:
        response = await chat_session.send_message(f"{user_name}: {prompt_text}")

        if is_pastebin:
            paste_link = await upload_to_pastebin(response)
            await message.answer(f"Ответ загружен: {paste_link}")
        else:
            for chunk in get_gpt_formatted_chunks(response):
                await message.answer(chunk, parse_mode="MarkdownV2")

    except Exception:
        logger.exception("Ошибка при отправке в GPT:")
        await message.answer("Произошла ошибка. Попробуйте позже.")


@router.message(Command("add_me_as"))
async def add_user_to_whitelist(message: Message):
    text_input = message.text.split(maxsplit=1) if message.text else ""
    if len(text_input) < 2 or not message.from_user:
        await message.reply("Использование: /add_me_as  имя")
        return
    custom_name = text_input[1]
    if message.chat.id not in whitelist:
        whitelist[message.chat.id] = {}
    whitelist[message.chat.id][message.from_user.id] = custom_name
    save_whitelist(whitelist)
    await message.reply(
        f"Пользователь {custom_name} (ID: {message.from_user.id}) добавлен в белый список."
    )
