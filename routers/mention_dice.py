from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from services.gpt import get_openrouter_gemini_2_0_response
import re

router = Router()

def gpt_to_telegram_markdown_v2(text: str) -> str:
    """
    Конвертирует текст из стандартного Markdown (GPT) в формат Telegram MarkdownV2.
    """
    # 1. Экранируем специальные символы для Telegram MarkdownV2
    special_characters = r'_*[]()~`>#+-=|{}.!'
    escaped_text = ''.join(f'\\{char}' if char in special_characters else char for char in text)
    
    # 2. Заменяем **жирный** на *жирный*
    escaped_text = re.sub(r'\\\*\\\*(.*?)\\\*\\\*', r'*\1*', escaped_text)
    
    # 3. Заменяем ~~зачёркнутый~~ на ~зачёркнутый~
    escaped_text = re.sub(r'\\~\\~(.*?)\\~\\~', r'~\1~', escaped_text)
    
    # 4. Обрабатываем ссылки [текст](url)
    escaped_text = re.sub(r'\\\[(.*?)\\\]\\\((.*?)\\\)', r'[\1](\2)', escaped_text)
    
    # 5. Возвращаем преобразованный текст
    return escaped_text

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
Если результат броска кубика равен 1-3, ты поддерживаешь идею пользователя, если 4-6 — отговариваешь его.
"""

    action_prompt = f"""
Пользователь задумался о следующем: {{вопрос пользователя: "{message.text}"}}. Бросок кубика показал {{результат кубика: {dice_value}}}. Напиши креативный и весёлый текст:
- Если кубик показал 1-3: Напиши вдохновляющее письмо, объясняющее, почему это отличная идея.
- Если кубик показал 4-6: Напиши креативный текст с элементами юмора, объясняющий, почему это плохая идея.

Старайся делать ответы нестандартными и оригинальными. Добавляй метафоры, шутки или неожиданные повороты. Твой текст должен быть лёгким и поднимать настроение.
"""

    # Генерируем объяснение через OpenAI API
    explanation = await get_openrouter_gemini_2_0_response(
        action_prompt, 
        system_prompt
    )
    cleaned_response = gpt_to_telegram_markdown_v2(explanation.content)
    print(cleaned_response)
    await message.reply(cleaned_response, parse_mode="MarkdownV2")
