import asyncio
import platform
from urllib.request import getproxies

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.strategy import FSMStrategy

from core.config import TELEGRAM_PROXY, TOKEN_BOT
from core.logger import logger
from tg_bot.routers import setup_routers
from tg_bot.services.message_queue import MessageQueue
from tg_bot.tasks.sheduled import on_startup


async def main():
    # Создание бота
    proxy = TELEGRAM_PROXY
    if proxy is None and platform.system() == "Windows":
        proxy = getproxies().get("https")
    session = AiohttpSession(proxy=proxy or None)
    bot = Bot(token=TOKEN_BOT, session=session)
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
            import uvloop  # type: ignore[import]

            logger.info("Используется uvloop для Linux.")
            uvloop.run(main())
        except ImportError:
            logger.warning("uvloop не установлен, используется стандартный asyncio.")
    else:
        logger.info("Используется стандартный asyncio.")
        asyncio.run(main())
