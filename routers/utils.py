from aiogram import Router, types, F
from aiogram.filters import Command

router = Router()

@router.message(Command("logchat"))
async def log_chat_id(message: types.Message):
    await message.reply(f"Chat ID этой группы: {message.chat.id}")

@router.message(F.animation)
async def get_gif_file_id(message: types.Message):
    gif_file_id = message.animation.file_id
    await message.reply(f"Вот ваш file_id для GIF: {gif_file_id}")
    