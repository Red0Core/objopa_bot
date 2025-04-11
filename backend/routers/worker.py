from fastapi import APIRouter, Depends, HTTPException, status
from uuid import uuid4
from datetime import datetime
from redis.asyncio import Redis
from pydantic import BaseModel, Field
from core.config import REDIS_HOST, REDIS_PORT, REDIS_PASSWORD
from core.logger import logger
from backend.models.workers import BaseWorkerTask, ImageGenerationTaskData

router = APIRouter(prefix="/worker", tags=["worker"])

redis = Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD, decode_responses=True)

class ImageGenerationRequest(BaseModel):
    prompts: list[str] = Field(..., min_items=1, example=["cat with glasses", "dog with a cape"]) # type: ignore
    user_id: int = Field(..., example=123456789) # type: ignore

@router.post("/image")
async def submit_image_task(request: ImageGenerationRequest):
    task_id = str(uuid4())
    task_data = BaseWorkerTask(
        type="image_generation",
        task_id=task_id,
        created_at=datetime.now(),
        data=ImageGenerationTaskData(
            prompts=request.prompts,
            user_id=request.user_id,
        )
    )

    try:
        await redis.rpush("image_tasks", task_data.model_dump_json()) # type: ignore
        logger.info(f"New image generation task submitted: {task_id}")
    except Exception as e:
        logger.error(f"Redis error: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to queue task")

    return {"task_id": task_id, "status": "queued"}
