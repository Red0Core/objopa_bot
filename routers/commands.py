from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from config import GIFS_ID

router = Router()

@router.message(Command("start"))
async def start_handler(message: Message):
    await message.answer_animation(
        animation=GIFS_ID["Салам дай брад"],
        caption="Я пока только MEXC паршу. Добавь в чат, и будет тоже ласт промки выводить сам, только дай доступ пж."
    )
