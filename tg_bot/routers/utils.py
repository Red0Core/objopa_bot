from aiogram import F, Router, types
from aiogram.filters import Command

from core.config import MAIN_ACC
from core.memory import memory_report, trim_memory

router = Router()


@router.message(Command("logchat"))
async def log_chat_id(message: types.Message):
    await message.reply(f"Chat ID этой группы: {message.chat.id}")


@router.message(Command("ram"))
async def ram_status(message: types.Message):
    await message.reply(memory_report())


@router.message(Command("ram_trim"))
async def ram_trim(message: types.Message):
    if not message.from_user or message.from_user.id != MAIN_ACC:
        await message.reply("Недостаточно прав.")
        return
    from core.memory import current_rss_mb

    before = current_rss_mb()
    trim_memory()
    after = current_rss_mb()
    await message.reply(f"Trim: {before:.1f} MB → {after:.1f} MB\n{memory_report()}")


@router.message(F.animation)
async def get_gif_file_id(message: types.Message):
    if not message.animation:
        return
    gif_file_id = message.animation.file_id
    await message.reply(f"Вот ваш file_id для GIF: {gif_file_id}")
