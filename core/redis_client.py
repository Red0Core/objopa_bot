from redis.asyncio import Redis
from redis.backoff import ExponentialBackoff
from redis.asyncio.retry import Retry
from redis.exceptions import (
   BusyLoadingError,
   ConnectionError,
   TimeoutError
)
from core.config import REDIS_HOST, REDIS_PORT, REDIS_PASSWORD

redis = Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    password=REDIS_PASSWORD,
    decode_responses=True,
    retry=Retry(ExponentialBackoff(), 5),
    retry_on_error=[BusyLoadingError, ConnectionError, TimeoutError]
)
