import re
import uuid
import asyncio
import json
from pathlib import Path
from typing import List, Dict, Any, Optional

from aiogram import Router, F
from aiogram.types import Message, FSInputFile
from aiogram.filters import Command
from aiogram.enums.parse_mode import ParseMode

from core.redis_client import redis
from core.logger import logger
from core.config import BACKEND_ROUTE

video_router = Router()

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
        "выбрать изображения для каждой сцены, а затем я создам готовое видео."
    )
    await message.reply(help_text, parse_mode=ParseMode.HTML)

@video_router.message(F.document.file_name.endswith(".img.txt") | F.document.file_name.endswith(".anim.txt"))
async def handle_prompt_file(message: Message):
    """
    Обработчик файлов .img.txt и .anim.txt с промптами.
    Сохраняет промпты и запускает генерацию видео при наличии обоих файлов.
    """
    if not message.document:
        return
    
    file_name = message.document.file_name
    # Используем комбинацию chat_id и user_id для уникальной идентификации
    chat_id = str(message.chat.id)
    user_id = str(message.from_user.id) if message.from_user else "unknown"
    session_key = f"{chat_id}:{user_id}"
    
    # Создаем ключи Redis для хранения промптов
    img_prompts_key = f"video_gen:img_prompts:{session_key}"
    anim_prompts_key = f"video_gen:anim_prompts:{session_key}"
    
    # Скачиваем файл
    file = await message.bot.get_file(message.document.file_id)
    file_path = await message.bot.download_file(file.file_path)
    
    # Читаем содержимое файла и разбиваем на строки
    content = file_path.read().decode('utf-8').strip()
    prompts = [line.strip() for line in content.split('\n') if line.strip()]
    
    # Проверяем, есть ли уже сохраненные промпты
    existing_prompts_json = None
    overwrite_message = ""
    
    # Сохраняем промпты в Redis в зависимости от типа файла
    if file_name.endswith(".img.txt"):
        # Проверяем, есть ли уже промпты для изображений
        existing_prompts_json = await redis.get(img_prompts_key)
        if existing_prompts_json:
            overwrite_message = "⚠️ Промпты для изображений были перезаписаны.\n\n"
            
        await redis.set(img_prompts_key, json.dumps(prompts))
        await message.reply(
            f"{overwrite_message}✅ Получено {len(prompts)} промптов для изображений.\n\n"
            f"Теперь отправьте файл с промптами для анимаций (<code>промпты.anim.txt</code>), "
            f"если вы ещё этого не сделали.",
            parse_mode=ParseMode.HTML
        )
    elif file_name.endswith(".anim.txt"):
        # Проверяем, есть ли уже промпты для анимаций
        existing_prompts_json = await redis.get(anim_prompts_key)
        if existing_prompts_json:
            overwrite_message = "⚠️ Промпты для анимаций были перезаписаны.\n\n"
            
        await redis.set(anim_prompts_key, json.dumps(prompts))
        await message.reply(
            f"{overwrite_message}✅ Получено {len(prompts)} промптов для анимаций.\n\n"
            f"Теперь отправьте файл с промптами для изображений (<code>промпты.img.txt</code>), "
            f"если вы ещё этого не сделали.",
            parse_mode=ParseMode.HTML
        )
    
    # Проверяем наличие обоих типов промптов
    has_img_prompts = await redis.exists(img_prompts_key)
    has_anim_prompts = await redis.exists(anim_prompts_key)
    
    # Если есть оба типа промптов, запускаем генерацию
    if has_img_prompts and has_anim_prompts:
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
                f"отправьте команду /start_generation для запуска процесса.",
                parse_mode=ParseMode.HTML
            )
        else:
            # Запускаем процесс генерации
            await start_video_generation(message, img_prompts, anim_prompts)

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
    img_prompts_key = f"video_gen:img_prompts:{session_key}"
    anim_prompts_key = f"video_gen:anim_prompts:{session_key}"
    
    # Проверяем наличие обоих типов промптов
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
            parse_mode=ParseMode.HTML
        )
        return
    
    # Получаем промпты
    img_prompts_json = await redis.get(img_prompts_key)
    anim_prompts_json = await redis.get(anim_prompts_key)
    
    img_prompts = json.loads(img_prompts_json)
    anim_prompts = json.loads(anim_prompts_json)
    
    # Запускаем процесс генерации
    await start_video_generation(message, img_prompts, anim_prompts)

async def start_video_generation(message: Message, img_prompts: List[str], anim_prompts: List[str]):
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
            "animation_prompts": anim_prompts
        }
    }
    
    # Отправляем сообщение о начале генерации
    await message.reply(
        f"🎬 <b>Генерация видео началась!</b>\n\n"
        f"• Количество сцен: <b>{len(img_prompts)}</b>\n"
        f"• ID задачи: <code>{task_id}</code>\n\n"
        f"Я отправлю вам варианты изображений для выбора. После выбора всех изображений "
        f"начнется создание анимаций и финального видео.",
        parse_mode=ParseMode.HTML
    )
    
    # Добавляем задачу в очередь Redis
    await redis.rpush("hailuo_tasks", json.dumps(task))
    logger.info(f"Задача на генерацию видео {task_id} добавлена в очередь")
    
    # Очищаем промпты из Redis
    await redis.delete(f"video_gen:img_prompts:{session_key}")
    await redis.delete(f"video_gen:anim_prompts:{session_key}")
