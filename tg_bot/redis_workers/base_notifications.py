import asyncio

import redis.asyncio as redis
import ujson

from core.config import MAIN_ACC, REDIS_HOST, REDIS_PASSWORD, REDIS_PORT
from core.logger import logger

r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD, decode_responses=True)


async def poll_redis(bot):
    while True:
        data = await r.rpop("notifications")  # type: ignore[no-untyped-call]
        if data:
            try:
                payload = ujson.loads(data)  # type: ignore[no-untyped-call]
                text = payload.get("text")
                send_to = payload.get("send_to")
                if text:
                    await bot.send_message(chat_id=MAIN_ACC if not send_to else send_to, text=text)
            except Exception as e:
                logger.exception(f"[redis_worker] Failed to process message: {e}")
        await asyncio.sleep(1)
