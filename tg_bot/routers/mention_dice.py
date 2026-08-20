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
_ai_client: GeminiModel | None = None


def get_ai_client() -> GeminiModel:
    global _ai_client
    if _ai_client is None:
        _ai_client = GeminiModel(api_key=GEMINI_API_KEY)
    return _ai_client


# Back-compat for day_tracker which imported AI_CLIENT.
def __getattr__(name: str):
    if name == "AI_CLIENT":
        return get_ai_client()
    raise AttributeError(name)


@router.message(Command("dice"))
async def handle_mention(message: Message, bot: Bot):
    # Отвечаем на упоминание
    await message.reply("Сейчас я решу это с помощью кубика! 🎲")

    # Бросаем кубик
    dice_message = await bot.send_dice(message.chat.id)
    dice_value = dice_message.dice.value  # Значение кубика (1-6) # type: ignore[union-attr]
    text = message.text.split(maxsplit=1)[1] if message.text else ""

    system_prompt = """Ты — креативный и весёлый помощник. Пиши по-русски.
Тон: вдохновляющий, с лёгким юмором и метафорами. Коротко: 1–2 абзаца, без тяжёлого форматирования.

Логика понимания запроса:
1) Определи тип:
   - «Выбор»: пользователь сравнивает варианты (есть слова «или», «между», перечисления через запятую/нумерацию).
   - «Одно действие»: один сценарий, где надо решить — делать или не делать.
2) Результат кубика (число 1–6) приходит в запросе. Используй его для решения.

Правила принятия решения:
- Если «Выбор»:
  - Если 2 варианта: 1–3 → выбирай первый; 4–6 → выбирай второй.
  - Если 3+ вариантов: выбери вариант с индексом ((значение кубика − 1) mod N) + 1.
- Если «Одно действие»:
  - 1–3 → «Делать». Поддержи идею, дай вдохновляющее объяснение.
  - 4–6 → «Не делать». Отговори с добрым юмором и аргументами.

Формат ответа:
- С первой строки чётко зафиксируй решение коротко: «Выбор: <вариант>» или «Решение: делать/не делать».
- Дальше — короткое объяснение (1–5 предложений), очень креативно и по делу.
- Избегай лишних символов, но активно использовуй emoji, в особенности демона, крутой чувак, 💪 и т.д. Не растягивай текст. 
- MarkdownV2 разрешен.
"""

    action_prompt = f"""Пользователь задумался о следующем: "{text}". Бросок кубика показал {dice_value}. Напиши креативный и весёлый текст"""

    try:
        # Генерируем объяснение через OpenAI API
        text = await get_ai_client().get_response(action_prompt, system_prompt)
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
