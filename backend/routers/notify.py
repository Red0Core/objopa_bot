from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel

from backend.models.workers import BaseWorkerTask, ImageSelectionTaskData
from core.logger import logger
from core.redis_client import get_redis

router = APIRouter(prefix="/notify", tags=["notifiers"])


class Notification(BaseModel):
    text: str
    send_to: str | None = None


@router.post("")
async def push_notification(notifier: Notification):
    r = await get_redis()
    await r.lpush("notifications", notifier.model_dump_json())  # type: ignore
    return {
        "status": "queued",
        "message": "Notification queued for sending.",
        "data": notifier.model_dump_json(),
    }


class ImageSelectionRequest(BaseModel):
    task_id: str
    user_id: int
    relative_paths: list[str]


@router.post("/image-selection")
async def notify_image_selection(data: ImageSelectionRequest):
    # Ключ уведомления для Telegram-бота
    notification_key = f"notifications:image_selection:{data.task_id}"

    payload = BaseWorkerTask(
        type="image_selection",
        task_id=data.task_id,
        created_at=datetime.now(timezone.utc),
        data=ImageSelectionTaskData(
            user_id=data.user_id,
            relative_paths=data.relative_paths,
        ),
    )

    logger.info(f"Pushing image selection task to Redis: {payload}")
    r = await get_redis()
    await r.rpush(notification_key, payload.model_dump_json())  # type: ignore
    return {"status": "ok"}
