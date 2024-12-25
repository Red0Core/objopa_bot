from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from services.gpt import GeminiModel, OpenRouterModel, APIKeyError, AIModelError, RateLimitError, UnexpectedResponseError, QuotaExceededError
from config import GEMINI_API_KEY
import re

router = Router()
AI_CLIENT = GeminiModel(api_key=GEMINI_API_KEY)

def markdown_to_telegram_html(text: str) -> str:
    """
    Преобразует текст из Markdown в HTML для использования в Telegram, строго соответствуя поддерживаемым тегам.
    """
    # 1. Экранирование спецсимволов
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    # 2. Жирный текст (**text** -> <b>text</b>)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)

    # 3. Курсив (*text* -> <i>text</i>)
    text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)

    # 4. Зачёркнутый текст (~~text~~ -> <s>text</s>)
    text = re.sub(r'~~(.+?)~~', r'<s>\1</s>', text)

    # 5. Подчёркнутый текст (__text__ -> <u>text</u>)
    text = re.sub(r'__(.+?)__', r'<u>\1</u>', text)

    # 6. Ссылки ([text](url) -> <a href="url">text</a>)
    text = re.sub(r'\[(.+?)\]\((.+?)\)', r'<a href="\2">\1</a>', text)

    # 7. Блоки кода (```code``` -> <pre>code</pre>)
    text = re.sub(r'```(.+?)```', r'<pre>\1</pre>', text, flags=re.DOTALL)

    # 8. Inline-код (`code` -> <code>code</code>)
    text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)

    # 9. Спойлеры (||text|| -> <tg-spoiler>text</tg-spoiler>)
    text = re.sub(r'\|\|(.+?)\|\|', r'<tg-spoiler>\1</tg-spoiler>', text)

    # 10. Обработка лишних пробелов вокруг HTML-тегов
    text = re.sub(r'>\s+<', '><', text)

    return text

@router.message(Command("dice"))
async def handle_mention(message: Message, bot):
    # Отвечаем на упоминание
    await message.reply("Сейчас я решу это с помощью кубика! 🎲")
    
    # Бросаем кубик
    dice_message = await bot.send_dice(message.chat.id)
    dice_value = dice_message.dice.value  # Значение кубика (1-6)

    system_prompt = """
Ты — креативный и весёлый помощник. 
Когда человек обращается к тебе, ты отвечаешь в стиле вдохновляющего и немного шуточного мотивационного письма или даёшь креативный список "за и против".
Ты всегда находишь оригинальные аргументы и добавляешь немного юмора, чтобы поднять настроение.
Твоя задача — сделать каждое сообщение запоминающимся.
Максимум 1-2 абзаца. Надо коротко, но смешно! Чем меньше текста, тем лучше!
Если результат броска кубика равен 1-3, ты поддерживаешь идею пользователя, если 4-6 — отговариваешь его.
"""

    action_prompt = f"""
Пользователь задумался о следующем: "{message.text.split(maxsplit=1)[1]}". Бросок кубика показал {dice_value}. Напиши креативный и весёлый текст:
- Если кубик показал 1-3: Напиши вдохновляющее письмо, объясняющее, почему это отличная идея.
- Если кубик показал 4-6: Напиши креативный текст с элементами юмора, объясняющий, почему это плохая идея.

Старайся делать ответы нестандартными и оригинальными. Добавляй метафоры, шутки или неожиданные повороты. Твой текст должен быть лёгким и поднимать настроение.
"""

    try:
        # Генерируем объяснение через OpenAI API
        text = await AI_CLIENT.get_response(
            action_prompt, 
            system_prompt
        )
        cleaned_text = markdown_to_telegram_html(text)

        await message.reply(cleaned_text, parse_mode="HTML")
    except APIKeyError as e:
        await message.reply("Ошибка: Неверный API-ключ. Обратитесь к администратору.")
    except RateLimitError as e:
        await message.reply("Ошибка: Превышен лимит запросов. Попробуйте позже.")
    except QuotaExceededError as e:
        await message.reply("Ошибка: Превышена квота использования API.")
    except UnexpectedResponseError as e:
        await message.reply("Ошибка: Непредвиденный ответ от модели. Попробуйте позже.")
    except AIModelError as e:
        await message.reply(f"Ошибка: {str(e)}")

@router.message(Command("ask"))
async def handle_ask_gpt(message: Message, bot):
    try:
        # Генерируем объяснение через OpenAI API
        text = await AI_CLIENT.get_response(
            message.text.split(maxsplit=1)[1]
        )
        cleaned_text = markdown_to_telegram_html(text)

        await message.reply(cleaned_text, parse_mode="HTML")
    except APIKeyError as e:
        await message.reply("Ошибка: Неверный API-ключ. Обратитесь к администратору.")
    except RateLimitError as e:
        await message.reply("Ошибка: Превышен лимит запросов. Попробуйте позже.")
    except QuotaExceededError as e:
        await message.reply("Ошибка: Превышена квота использования API.")
    except UnexpectedResponseError as e:
        await message.reply("Ошибка: Непредвиденный ответ от модели. Попробуйте позже.")
    except AIModelError as e:
        await message.reply(f"Ошибка: {str(e)}")
