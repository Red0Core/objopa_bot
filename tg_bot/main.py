import asyncio
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.strategy import FSMStrategy

from core.config import TOKEN_BOT
from routers import setup_routers
from tasks.sheduled import on_startup
import platform
from core.logger import logger
from services.message_queue import MessageQueue

async def main():
    # Создание бота
    bot = Bot(token=TOKEN_BOT, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(fsm_strategy=FSMStrategy.CHAT)
    MessageQueue(bot=bot)

    # Регистрация роутеров
    setup_routers(dp)

    # Регистрация фоновых задач
    dp.startup.register(on_startup)

    # Запуск polling
    await dp.start_polling(bot)

if __name__ == "__main__":
    if platform.system() == "Windows":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        logger.info("Используется WindowsSelectorEventLoopPolicy для Windows.")
        asyncio.run(main())
    elif platform.system() == "Linux":
        try:
            import uvloop # type: ignore[import]
            logger.info("Используется uvloop для Linux.")
            uvloop.run(main())
        except ImportError:
            logger.warning("uvloop не установлен, используется стандартный asyncio.")
    else:
        logger.info("Используется стандартный asyncio.")
        asyncio.run(main())
