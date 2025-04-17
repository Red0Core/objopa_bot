import asyncio
from pathlib import Path
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status, BackgroundTasks
from uuid import uuid4
from datetime import datetime, timedelta
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from core.config import UPLOAD_DIR, UPLOAD_VIDEO_DIR
from core.logger import logger
from core.redis_client import get_redis
from backend.models.workers import BaseWorkerTask, FileUploadResponse, VideoGenerationPipelineTaskData
import os
import xxhash
import aiofiles
import time

router = APIRouter(prefix="/worker", tags=["worker"])

@router.post("/video-generation-pipeline", response_model=dict)
async def submit_image_task(request: VideoGenerationPipelineTaskData):
    task_id = str(uuid4())
    task_data = BaseWorkerTask(
        type="video_generation",
        task_id=task_id,
        created_at=datetime.now(),
        data=VideoGenerationPipelineTaskData(
            image_prompts=request.image_prompts,
            animation_prompts=request.animation_prompts,
            user_id=request.user_id,
        )
    )

    try:
        redis = await get_redis()
        await redis.rpush("hailuo_tasks", task_data.model_dump_json()) # type: ignore
        logger.info(f"New image generation task submitted: {task_id}")
    except Exception as e:
        logger.error(f"Redis error: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to queue task")

    return {"task_id": task_id, "status": "queued"}

# Глобальные константы
FILE_HASH_KEY_PREFIX = "file_hash:"
FILE_STATS_KEY_PREFIX = "file_stats:"
FILE_EXPIRY_SET = "expired_files"
VIDEO_EXPIRY_SET = "expired_videos"
DEFAULT_EXPIRY = timedelta(days=1)
VIDEO_EXPIRY = timedelta(days=7)

@router.post("/upload", response_model=FileUploadResponse)
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    redis: Redis = Depends(get_redis)
):
    if not file:
        raise HTTPException(400, "Файл не предоставлен")

    start = time.time()
    ext = Path(file.filename or "").suffix
    temp_name = f"tmp_{uuid4().hex}{ext}"
    temp_path = UPLOAD_DIR / temp_name

    # оптимизация: хеш первых 20 МБ, чанк 4 МБ
    hasher = xxhash.xxh64()
    hash_limit = 20 * 1024 * 1024
    bytes_hashed = 0
    size = 0

    async with aiofiles.open(temp_path, "wb") as out:
        while chunk := await file.read(4 * 1024 * 1024):
            size += len(chunk)
            if bytes_hashed < hash_limit:
                to_hash = chunk[: min(len(chunk), hash_limit - bytes_hashed)]
                hasher.update(to_hash)
                bytes_hashed += len(to_hash)
            await out.write(chunk)

    file_hash = hasher.hexdigest()
    key = f"{FILE_HASH_KEY_PREFIX}{file_hash}_{size}"

    existing = await redis.get(key)
    if existing:
        # дубликат
        background_tasks.add_task(os.unlink, temp_path)
        asyncio.create_task(
            _update_stats(redis, key, f"{FILE_STATS_KEY_PREFIX}{file_hash}_{size}")
        )
        logger.info(f"Duplicate upload ({time.time()-start:.2f}s)")
        return FileUploadResponse(
            filename=file.filename or "unknown",
            filepath=existing,
            size=size,
            content_type=file.content_type or "application/octet-stream"
        )

    final_name = f"{file_hash[:8]}_{uuid4().hex[:4]}{ext}"
    final_path = UPLOAD_DIR / final_name

    if size < 10 * 1024 * 1024:
        os.rename(temp_path, final_path)
    else:
        background_tasks.add_task(os.rename, temp_path, final_path)

    stats = {
        "size": size,
        "content_type": file.content_type or "application/octet-stream",
        "uploaded": int(time.time()),
        "count": 1
    }
    expiry = int(DEFAULT_EXPIRY.total_seconds())

    pipe = redis.pipeline(transaction=False)
    pipe.set(key, final_name, ex=expiry)
    pipe.hset(f"{FILE_STATS_KEY_PREFIX}{file_hash}_{size}", mapping=stats)
    pipe.zadd(FILE_EXPIRY_SET, {final_name: time.time() + expiry})
    await pipe.execute()

    logger.info(f"Upload done in {time.time()-start:.2f}s")
    return FileUploadResponse(
        filename=file.filename or "unknown",
        filepath=final_name,
        size=size,
        content_type=file.content_type or "application/octet-stream"
    )


# Фоновая задача обновления статистики
async def _update_stats(redis: Redis, key: str, stats_key: str):
    try:
        pipe = redis.pipeline()
        pipe.hincrby(stats_key, "count", 1)
        pipe.expire(key, int(VIDEO_EXPIRY.total_seconds()))
        pipe.expire(stats_key, int(VIDEO_EXPIRY.total_seconds()))
        await pipe.execute()
    except Exception as e:
        logger.error(f"Stats update failed: {e}")


@router.get("/download-video/{filename}")
async def download_video(filename: str):
    path = UPLOAD_VIDEO_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Файл не найден")
    return FileResponse(path, filename=filename)


@router.post("/upload-video", response_model=FileUploadResponse)
async def upload_video(
    file: UploadFile = File(...),
    redis: Redis = Depends(get_redis)
):
    if not file:
        raise HTTPException(400, "Видео не предоставлено")
    start = time.time()
    ext = Path(file.filename or "").suffix
    unique = f"{uuid4().hex}{ext}"
    dst = UPLOAD_VIDEO_DIR / unique
    UPLOAD_VIDEO_DIR.mkdir(parents=True, exist_ok=True)

    # стримим в файл
    async with aiofiles.open(dst, "wb") as out:
        while chunk := await file.read(4 * 1024 * 1024):
            await out.write(chunk)

    # Кладём только имя файла (чтобы в Redis не хранить абсолютные пути)
    expiry_seconds = int(VIDEO_EXPIRY.total_seconds())
    await redis.zadd(VIDEO_EXPIRY_SET, {unique: time.time() + expiry_seconds})

    logger.info(f"Video upload done in {time.time()-start:.2f}s size={dst.stat().st_size}")
    return FileUploadResponse(
        filename=file.filename or "unknown",
        filepath=unique,
        size=dst.stat().st_size,
        content_type=file.content_type or "application/octet-stream"
    )


async def _cleanup_set(
    redis: Redis,
    set_key: str,
    base_dir: Path
) -> int:
    now_ts = time.time()
    expired = await redis.zrangebyscore(set_key, 0, now_ts)
    if not expired:
        return 0

    deleted = 0
    for filename in expired:
        path: Path = base_dir / filename
        if path.exists():
            try:
                path.unlink()
                deleted += 1
            except Exception as e:
                logger.error(f"Не смог удалить {path}: {e}")

    # batch‑удаляем все обработанные ключи из множества
    await redis.zrem(set_key, *expired)
    logger.info(f"Cleaned {deleted} paths from {set_key}")
    return deleted



@router.post("/cleanup", summary="Ручная очистка истёкших файлов/видео")
async def manual_cleanup(
    redis: Redis = Depends(get_redis)
):
    files_deleted  = await _cleanup_set(redis, FILE_EXPIRY_SET,  UPLOAD_DIR)
    videos_deleted = await _cleanup_set(redis, VIDEO_EXPIRY_SET, UPLOAD_VIDEO_DIR)
    return {
        "status": "ok",
        "files_deleted": files_deleted,
        "videos_deleted": videos_deleted
    }
