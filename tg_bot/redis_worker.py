import os
import asyncio
import ujson
import redis.asyncio as redis
from logger import logger
from config import MAIN_ACC

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

async def poll_redis(bot):
    while True:
        data = await r.rpop("notifications")
        if data:
            try:
                payload = ujson.loads(data)
                text = payload.get("text")
                if text:
                    await bot.send_message(chat_id=MAIN_ACC, text=text)
            except Exception as e:
                logger.exception(f"[redis_worker] Failed to process message: {e}")
        await asyncio.sleep(1)
