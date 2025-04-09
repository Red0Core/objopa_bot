import os

import redis.asyncio
from fastapi import APIRouter
from pydantic import BaseModel

r = redis.asyncio.Redis(host=os.getenv("REDIS_HOST", "redis"), port=6379, decode_responses=True)
router = APIRouter()

class Notification(BaseModel):
    text: str
    send_to: str | None = None

@router.post("/notify")
async def push_notification(notifier: Notification):
    await r.lpush("notifications", notifier.model_dump_json())  # type: ignore
    return {
        "status": "queued",
        "message": "Notification queued for sending.",
        "data": notifier.model_dump_json()
    }
