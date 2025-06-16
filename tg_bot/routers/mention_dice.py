from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Message

from core.config import GEMINI_API_KEY
from tg_bot.services.gpt import (
    AIModelError,
    APIKeyError,
    GeminiModel,
    QuotaExceededError,
    RateLimitError,
    UnexpectedResponseError,
    get_gpt_formatted_chunks,
)

router = Router()
AI_CLIENT = GeminiModel(api_key=GEMINI_API_KEY)


@router.message(Command("dice"))
async def handle_mention(message: Message, bot: Bot):
    # Отвечаем на упоминание
    await message.reply("Сейчас я решу это с помощью кубика! 🎲")

    # Бросаем кубик
    dice_message = await bot.send_dice(message.chat.id)
    dice_value = (
        dice_message.dice.value
    )  # Значение кубика (1-6) # type: ignore[union-attr]
    text = message.text.split(maxsplit=1)[1] if message.text else ""

    system_prompt = """
Ты — креативный и весёлый помощник. 
Когда человек обращается к тебе, ты отвечаешь в стиле вдохновляющего и немного шуточного мотивационного письма или даёшь креативный список "за и против".
Ты всегда находишь оригинальные аргументы и добавляешь немного юмора, чтобы поднять настроение.
Твоя задача — сделать каждое сообщение запоминающимся.
Максимум 1-2 абзаца. Надо коротко, но смешно! Чем меньше текста, тем лучше!
Если результат броска кубика равен 1-3, ты поддерживаешь идею пользователя, если 4-6 — отговариваешь его.
"""

    action_prompt = f"""
Пользователь задумался о следующем: "{text}". Бросок кубика показал {dice_value}. Напиши креативный и весёлый текст:
- Если кубик показал 1-3: Напиши вдохновляющее письмо, объясняющее, почему это отличная идея.
- Если кубик показал 4-6: Напиши креативный текст с элементами юмора, объясняющий, почему это плохая идея.

Старайся делать ответы нестандартными и оригинальными. Добавляй метафоры, шутки или неожиданные повороты. Твой текст должен быть лёгким и поднимать настроение.
"""

    try:
        # Генерируем объяснение через OpenAI API
        text = await AI_CLIENT.get_response(action_prompt, system_prompt)
        for chunk in get_gpt_formatted_chunks(text):
            await message.reply(chunk, parse_mode="MarkdownV2")
    except APIKeyError:
        await message.reply("Ошибка: Неверный API-ключ. Обратитесь к администратору.")
    except RateLimitError:
        await message.reply("Ошибка: Превышен лимит запросов. Попробуйте позже.")
    except QuotaExceededError:
        await message.reply("Ошибка: Превышена квота использования API.")
    except UnexpectedResponseError:
        await message.reply("Ошибка: Непредвиденный ответ от модели. Попробуйте позже.")
    except AIModelError as e:
        await message.reply(f"Ошибка: {str(e)}")
