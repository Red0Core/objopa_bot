# core/locks.py
from contextlib import asynccontextmanager
from typing import AsyncIterator

from redis.asyncio import Redis
from redis.asyncio.lock import Lock

from core.logger import logger
from core.redis_client import get_redis  # ваша обёртка с reconnect/backoff

# Настройки
LOCK_NAME = "hailuo_account"
LOCK_TIMEOUT = 30 * 60  # сек, автоматически отпустится через полчаса
BLOCKING_TIMEOUT = 1.0  # сек, сколько ждём acquire


class LockAcquireError(Exception):
    """Не удалось захватить распределённый лок."""

    pass


@asynccontextmanager
async def lock_hailuo() -> AsyncIterator[Lock]:
    """
    Асинхронный контекст‑менеджер для распределённого лока.
    Пример:
        async with lock_hailuo() as lock:
            # здесь защищённый участок
            ...
    """
    # 1) Получаем клиент
    redis: Redis = await get_redis()

    # 2) Формируем замок
    lock: Lock = redis.lock(
        name=LOCK_NAME,
        timeout=LOCK_TIMEOUT,
        blocking_timeout=BLOCKING_TIMEOUT,
    )

    # 3) Пробуем захватить
    acquired: bool = await lock.acquire()
    if not acquired:
        raise LockAcquireError(f"Resource '{LOCK_NAME}' is busy, try later")

    try:
        # отдаем сам Lock, если вдруг нужно lock.extend() и т.п.
        yield lock
    finally:
        # 4) в любом случае отпускаем
        try:
            await lock.release()
        except Exception:
            # игнорим если уже отпущен или сбой
            pass


async def force_release_hailuo_lock() -> bool:
    """
    Принудительно удаляет ключ лока 'hailuo_account' из Redis.

    ВНИМАНИЕ: Использовать с осторожностью! Это может нарушить логику
    распределенной блокировки, если другой процесс все еще считает,
    что владеет локом.

    Returns:
        True, если ключ был удален (или его не существовало), False при ошибке.
    """
    redis: Redis = await get_redis()
    try:
        logger.warning(f"Принудительное удаление лока: {LOCK_NAME}")
        await redis.delete(LOCK_NAME)  # По факту не волнует возвращаемое значеие
        logger.info(f"Лок {LOCK_NAME} принудительно удален.")
        return True
    except Exception as e:
        logger.error(f"Ошибка при принудительном удалении лока {LOCK_NAME}: {e}", exc_info=True)
        return False
