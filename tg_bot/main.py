import os

os.environ.setdefault("MALLOC_ARENA_MAX", "2")
os.environ.setdefault("PYTHONMALLOC", "malloc")
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import asyncio
import platform

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.strategy import FSMStrategy

from core.config import REDIS_HOST, REDIS_PASSWORD, REDIS_PORT, REDIS_SSL, TOKEN_BOT
from core.logger import logger
from core.memory import AIOHTTP_CONNECTOR_LIMIT, start_memory_maintenance, trim_memory
from tg_bot.routers import setup_routers
from tg_bot.services.message_queue import MessageQueue
from tg_bot.tasks.sheduled import on_startup


def _build_storage():
    try:
        from aiogram.fsm.storage.redis import DefaultKeyBuilder, RedisStorage

        scheme = "rediss" if REDIS_SSL else "redis"
        url = f"{scheme}://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/1"
        storage = RedisStorage.from_url(url, key_builder=DefaultKeyBuilder(with_bot_id=True))
        logger.info("FSM storage: Redis")
        return storage
    except Exception as exc:
        from aiogram.fsm.storage.memory import MemoryStorage

        logger.warning(f"FSM Redis storage unavailable ({exc}), falling back to MemoryStorage")
        return MemoryStorage()


async def main():
    session = AiohttpSession(limit=AIOHTTP_CONNECTOR_LIMIT)
    bot = Bot(token=TOKEN_BOT, session=session)
    dp = Dispatcher(storage=_build_storage(), fsm_strategy=FSMStrategy.CHAT)
    MessageQueue(bot=bot)

    setup_routers(dp)
    dp.startup.register(on_startup)
    dp.startup.register(start_memory_maintenance)

    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        await bot.session.close()
        trim_memory()


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
            asyncio.run(main())
    else:
        logger.info("Используется стандартный asyncio.")
        asyncio.run(main())
