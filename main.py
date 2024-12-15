import asyncio
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import TOKEN_BOT
from routers import setup_routers
from tasks.sheduled import on_startup

async def main():
    # Создание бота
    bot = Bot(token=TOKEN_BOT, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    # Регистрация роутеров
    setup_routers(dp)

    # Регистрация фоновых задач
    dp.startup.register(on_startup)

    # Запуск polling
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
