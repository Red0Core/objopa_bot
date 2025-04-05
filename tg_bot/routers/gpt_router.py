from pathlib import Path
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from core.config import GEMINI_API_KEY, STORAGE_PATH
from .mention_dice import markdown_to_telegram_html, split_message_by_paragraphs, AI_CLIENT
from services.gpt import AIChatInterface, APIKeyError, RateLimitError, QuotaExceededError, UnexpectedResponseError, AIModelError, GeminiChatModel
from services.gptchat_manager import ChatSessionManager
from core.logger import logger

router = Router()

import ujson
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

class ChatStates(StatesGroup):
    waiting_for_message = State()

class ChatFSMStateSessionManager(ChatSessionManager):
    def __init__(self):
        super().__init__()
        self.state: FSMContext | None = None
    
    def set_state(self, state: FSMContext):
        """Store FSMContext for later use in chat session management"""
        self.state = state
        return self

    async def clear_state(self) -> None:
        """Очистка FSM"""
        if self.state is not None:
            await self.state.clear()
    
    async def async_get_chat(self, chat_id) -> AIChatInterface | None:
        chat = super().get_chat(chat_id)
        if chat is None:
            await self.clear_state()

        return chat

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
            cleaned_text = markdown_to_telegram_html(text)
            messages = split_message_by_paragraphs(cleaned_text)
            for i in messages:
                await message.answer(i, parse_mode="HTML")
    except APIKeyError as e:
        await message.answer("Ошибка: Неверный API-ключ. Обратитесь к администратору.")
    except RateLimitError as e:
        await message.answer("Ошибка: Превышен лимит запросов. Попробуйте позже.")
    except QuotaExceededError as e:
        await message.answer("Ошибка: Превышена квота использования API.")
    except UnexpectedResponseError as e:
        await message.answer("Ошибка: Непредвиденный ответ от модели. Попробуйте позже.")
    except AIModelError as e:
        await message.answer(f"Ошибка: {str(e)}")

@router.message(Command("chat"))
async def start_session(message: Message, state: FSMContext):
    chat_id = message.chat.id
    chat_manager = ChatFSMStateSessionManager()

    if not await chat_manager.async_get_chat(chat_id):
        chat_model = GeminiChatModel(api_key=GEMINI_API_KEY)
        text_input = message.text.split(maxsplit=1) if message.text else ""
        if len(text_input) > 1:
            chat_model.new_chat(text_input[1])
        else:
            chat_model.new_chat()
        chat_manager.create_chat(chat_id, chat_model)

        await state.set_state(ChatStates.waiting_for_message)
        chat_manager.set_state(state)

        await message.answer(
            "Чат создан! Можете начать общение.\n" \
            "Сессия автоматически удаляется через час после безыиспользования\n" \
            "Вы можете добавить системный промпт после '/chat'"
        )
    else:
        await message.answer("Вы уже в чате. Продолжайте диалог.")

@router.message(ChatStates.waiting_for_message)
async def continue_session(message: Message, state: FSMContext):
    chat_id = message.chat.id
    chat_manager = ChatFSMStateSessionManager()

    chat_session = await chat_manager.async_get_chat(chat_id)
    if chat_session and message.from_user:
        ids = whitelist.get(message.chat.id, {})
        user_name = ids.get(message.from_user.id, f"{message.from_user.first_name} {message.from_user.last_name or ''}")

        logger.info(f"Отвечаю на сообщение в {chat_id} {message.from_user.username}-{message.from_user.first_name}+{message.from_user.last_name}")
        text = await chat_session.send_message(f"{user_name}: {message.text}")
        await state.set_state(ChatStates.waiting_for_message)

        cleaned_text = markdown_to_telegram_html(text)
        messages = split_message_by_paragraphs(cleaned_text)
        for i in messages:
            await message.answer(i, parse_mode="HTML")
    else:
        await message.answer("Используйте команду /chat для начала общения.")

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
    await message.reply(f"Пользователь {custom_name} (ID: {message.from_user.id}) добавлен в белый список.")
