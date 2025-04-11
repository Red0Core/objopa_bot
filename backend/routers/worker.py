from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from uuid import uuid4
from datetime import datetime
from pydantic import BaseModel, Field
from core.config import UPLOAD_DIR
from core.logger import logger
from core.redis_client import redis
from backend.models.workers import BaseWorkerTask, FileUploadResponse, ImageGenerationTaskData
import os
import shutil

router = APIRouter(prefix="/worker", tags=["worker"])

class ImageGenerationRequest(BaseModel):
    prompts: list[str] = Field(..., min_items=1, example=["cat with glasses", "dog with a cape"]) # type: ignore
    user_id: int = Field(..., example=123456789) # type: ignore

@router.post("/image")
async def submit_image_task(request: ImageGenerationRequest):
    task_id = str(uuid4())
    task_data = BaseWorkerTask(
        type="video_generation",
        task_id=task_id,
        created_at=datetime.now(),
        data=ImageGenerationTaskData(
            prompts=request.prompts,
            user_id=request.user_id,
        )
    )

    try:
        await redis.rpush("hailuo_tasks", task_data.model_dump_json()) # type: ignore
        logger.info(f"New image generation task submitted: {task_id}")
    except Exception as e:
        logger.error(f"Redis error: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to queue task")

    return {"task_id": task_id, "status": "queued"}

@router.post("/upload", response_model=FileUploadResponse)
async def upload_file(file: UploadFile = File(...)):
    """
    Загружает файл и возвращает путь к нему.
    
    - **file**: Загружаемый файл
    
    Возвращает информацию о файле, включая путь к нему на сервере.
    """
    if not file:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Файл не предоставлен")
    
    try:
        # Создаем уникальное имя файла
        file_ext = os.path.splitext(file.filename or "")[1]
        unique_filename = f"{uuid4()}{file_ext}"
        
        # Полный путь для сохранения
        file_path = UPLOAD_DIR / unique_filename
        
        # Сохраняем файл
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        # Получаем размер файла
        file_size = os.path.getsize(file_path)
        
        logger.info(f"Файл {file.filename} успешно загружен как {unique_filename}")
        
        return FileUploadResponse(
            filename=file.filename or "unknown",
            filepath=str(file_path.relative_to(UPLOAD_DIR)),
            size=file_size,
            content_type=file.content_type or "application/octet-stream"
        )
            
    except Exception as e:
        logger.error(f"Ошибка при загрузке файла: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Ошибка сохранения файла: {str(e)}"
        )