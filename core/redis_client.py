from redis.asyncio import Redis
from core.config import REDIS_HOST, REDIS_PORT, REDIS_PASSWORD

redis = Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    password=REDIS_PASSWORD,
    decode_responses=True,
)
