from typing import Optional

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import BusyLoadingError, ConnectionError, TimeoutError

from core.config import REDIS_HOST, REDIS_PASSWORD, REDIS_PORT, REDIS_SSL
from core.logger import logger

_redis: Optional[Redis] = None


def _make_redis() -> Redis:
    """Создаёт новый Redis-инстанс с retry/backoff настройками."""
    return Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD,
        decode_responses=True,
        retry=Retry(
            backoff=ExponentialBackoff(),
            retries=5
        ),
        retry_on_error=[ConnectionError, TimeoutError, BusyLoadingError],
        ssl=REDIS_SSL,
    )


async def get_redis() -> Redis:
    """
    Возвращает живой Redis-инстанс. Пингует текущий, пересоздаёт при сбое.
    """
    global _redis
    if _redis is None:
        _redis = _make_redis()

    try:
        await _redis.ping()
    except (ConnectionError, TimeoutError, BusyLoadingError) as err:
        logger.warning(f"Redis ping failed ({err}), reconnecting instance...")
        try:
            await _redis.close()
        except Exception:
            logger.debug("Error closing broken Redis client", exc_info=True)
        finally:
            _redis.connection_pool.disconnect()
        _redis = _make_redis()
        # гарантируем соединение
        await _redis.ping()
        logger.info("Redis reconnected successfully.")

    return _redis


async def close_redis() -> None:
    """Закрывает текущее соединение Redis."""
    global _redis
    if _redis:
        try:
            await _redis.close()
            logger.info("Redis connection closed.")
        except Exception as err:
            logger.error(f"Error closing Redis client: {err}")
        finally:
            _redis.connection_pool.disconnect()
            _redis = None
