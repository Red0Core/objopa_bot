from datetime import datetime
import redis.asyncio
from fastapi import APIRouter
from pydantic import BaseModel, HttpUrl
from core.logger import logger
from core.config import REDIS_HOST, REDIS_PASSWORD, REDIS_PORT
from backend.models.workers import BaseWorkerTask, ImageSelectionTaskData

r = redis.asyncio.Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD, decode_responses=True)
router = APIRouter(prefix="/notify", tags=["notifiers"])

class Notification(BaseModel):
    text: str
    send_to: str | None = None

@router.post("")
async def push_notification(notifier: Notification):
    await r.lpush("notifications", notifier.model_dump_json())  # type: ignore
    return {
        "status": "queued",
        "message": "Notification queued for sending.",
        "data": notifier.model_dump_json()
    }


class ImageSelectionRequest(BaseModel):
    task_id: str
    user_id: int
    images: list[HttpUrl]


@router.post("/image-selection")
async def notify_image_selection(data: ImageSelectionRequest):
    # Ключ уведомления для Telegram-бота
    notification_key = f"notifications:image_selection:{data.task_id}"

    payload = BaseWorkerTask(
        type="image_selection",
        task_id=data.task_id,
        created_at=datetime.now(),
        data= ImageSelectionTaskData(
            user_id=data.user_id,
            images=data.images,
        )
    )

    logger.info(f"Pushing image selection task to Redis: {payload}")
    await r.rpush(notification_key, payload.model_dump_json())  # type: ignore
    return {"status": "ok"}
