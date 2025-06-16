import asyncio
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import aiofiles
import xxhash
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from redis.asyncio import Redis

from backend.models.workers import (
    BaseWorkerTask,
    FileUploadResponse,
    ScenarioInput,
    VideoGenerationPipelineTaskData,
    VideoGenerationPromptItem,
)
from core.config import (
    DOWNLOADS_PATH,
    TWITTER_COOKIES_TOKEN,
    UPLOAD_DIR,
    UPLOAD_VIDEO_DIR,
    WORKER_ARCHIVES_DIR,
)
from core.logger import logger
from core.redis_client import get_redis

router = APIRouter(prefix="/worker", tags=["worker"])
security = HTTPBearer()


@router.post("/video-generation-pipeline", response_model=dict)
async def submit_image_task(request: VideoGenerationPipelineTaskData):
    task_id = str(uuid4())
    task_data = BaseWorkerTask(
        type="video_generation",
        task_id=task_id,
        created_at=datetime.now(timezone.utc),
        data=VideoGenerationPipelineTaskData(
            image_prompts=request.image_prompts,
            animation_prompts=request.animation_prompts,
            user_id=request.user_id,
        ),
    )

    try:
        redis = await get_redis()
        await redis.rpush("hailuo_tasks", task_data.model_dump_json())  # type: ignore
        logger.info(f"New image generation task submitted: {task_id}")
    except Exception as e:
        logger.error(f"Redis error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to queue task",
        ) from e

    return {"task_id": task_id, "status": "queued"}


def generate_prompts_from_scenario(
    images_data: list[VideoGenerationPromptItem],
    animations_data: list[VideoGenerationPromptItem],
    global_characters: dict[str, str],
) -> tuple[list[str], list[str]]:
    """
    Генерирует промпты для изображений и анимаций из данных сценария.
    Добавляет описания персонажей из глобального словаря или из описания в самом item,
    если оно предоставлено и не является просто ключом.
    """
    processed_image_prompts = []
    for item in images_data:
        prompt_output = item.prompt
        if item.characters:  # Обрабатываем персонажей, если они есть
            for char_name, char_value in item.characters.items():
                # 1. Пытаемся найти описание в глобальном словаре по значению из item.characters
                global_description = global_characters.get(char_value)

                if global_description:
                    # Нашли в глобальном словаре - используем его
                    prompt_output += f"; {char_name}: {global_description}"
                elif char_value != char_name and " " in char_value:
                    # Не нашли в глобальном, но значение отличается от ключа и содержит пробел
                    # Считаем это локальным описанием
                    prompt_output += f"; {char_name}: {char_value}"
                else:
                    # Не нашли в глобальном и нет локального описания
                    # Просто добавляем имя персонажа и выводим предупреждение
                    prompt_output += f"; {char_name}"
                    logger.warning(
                        f"No description found for character key '{char_value}' (used by '{char_name}') "
                        f"in global characters or inline for image prompt: '{item.prompt}'"
                    )
        processed_image_prompts.append(prompt_output)

    processed_animation_prompts = []
    for item in animations_data:
        prompt_output = item.prompt
        if item.characters:  # Обрабатываем персонажей для анимаций, если они есть
            for char_name, char_value in item.characters.items():
                # 1. Пытаемся найти описание в глобальном словаре по значению из item.characters
                global_description = global_characters.get(char_value)

                if global_description:
                    # Нашли в глобальном словаре - используем его
                    prompt_output += f"; {char_name}: {global_description}"
                elif char_value != char_name and " " in char_value:
                    # Не нашли в глобальном, но значение отличается от ключа и содержит пробел
                    # Считаем это локальным описанием
                    prompt_output += f"; {char_name}: {char_value}"
                else:
                    # Не нашли в глобальном и нет локального описания
                    # Просто добавляем имя персонажа и выводим предупреждение
                    prompt_output += f"; {char_name}"
                    logger.warning(
                        f"No description found for character key '{char_value}' (used by '{char_name}') "
                        f"in global characters or inline for animation prompt: '{item.prompt}'"
                    )
        processed_animation_prompts.append(prompt_output)

    return processed_image_prompts, processed_animation_prompts


@router.post(
    "/video-generation-from-scenario",
    response_model=dict,
    summary="Submit video generation task from JSON scenario",
)
async def submit_scenario_task(scenario: ScenarioInput):
    """
    Принимает JSON-сценарий, генерирует промпты и ставит задачу
    генерации видео в очередь.
    """
    task_id = str(uuid4())

    try:
        # Генерируем промпты
        image_prompts, animation_prompts = generate_prompts_from_scenario(
            images_data=scenario.images,
            animations_data=scenario.animations,
            global_characters=scenario.characters,
        )

        # Проверяем, что количество промптов совпадает (опционально, но полезно)
        if len(image_prompts) != len(animation_prompts):
            logger.warning(
                f"Task {task_id}: Mismatch in number of image prompts ({len(image_prompts)}) and animation prompts ({len(animation_prompts)})"
            )
            raise HTTPException(
                status_code=400,
                detail="Number of image prompts must match number of animation prompts",
            )

        # Формируем данные задачи для воркера
        task_data = BaseWorkerTask(
            type="video_generation",  # Убедитесь, что тип соответствует тому, что ожидает ваш воркер
            task_id=task_id,
            created_at=datetime.now(timezone.utc),
            data=VideoGenerationPipelineTaskData(
                image_prompts=image_prompts,
                animation_prompts=animation_prompts,
                user_id=scenario.user_id,
            ),
        )

        # Задачу в очередь Redis
        redis = await get_redis()
        await redis.rpush(
            "hailuo_tasks", task_data.model_dump_json()
        )  # Используем ту же очередь #type:ignore
        logger.info(
            f"New video generation task from scenario submitted: {task_id} for user {scenario.user_id}"
        )

    except Exception as e:
        logger.exception(f"Error processing scenario task {task_id}: {e}")  # Логируем с traceback
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process scenario and queue task: {str(e)}",
        ) from e

    return {"task_id": task_id, "status": "queued"}


# Глобальные константы
FILE_HASH_KEY_PREFIX = "file_hash:"
FILE_STATS_KEY_PREFIX = "file_stats:"
FILE_EXPIRY_SET = "expired_files"
VIDEO_EXPIRY_SET = "expired_videos"
DEFAULT_EXPIRY = timedelta(days=1)
VIDEO_EXPIRY = timedelta(days=7)
DOWNLOADS_DIR = DOWNLOADS_PATH


@router.post("/upload", response_model=FileUploadResponse)
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),  # noqa: B008
    redis: Redis = Depends(get_redis),  # noqa: B008
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
        asyncio.create_task(_update_stats(redis, key, f"{FILE_STATS_KEY_PREFIX}{file_hash}_{size}"))
        logger.info(f"Duplicate upload ({time.time() - start:.2f}s)")
        return FileUploadResponse(
            filename=file.filename or "unknown",
            filepath=existing,
            size=size,
            content_type=file.content_type or "application/octet-stream",
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
        "count": 1,
    }
    expiry = int(DEFAULT_EXPIRY.total_seconds())

    pipe = redis.pipeline(transaction=False)
    pipe.set(key, final_name, ex=expiry)
    pipe.hset(f"{FILE_STATS_KEY_PREFIX}{file_hash}_{size}", mapping=stats)
    pipe.zadd(FILE_EXPIRY_SET, {final_name: time.time() + expiry})
    await pipe.execute()

    logger.info(f"Upload done in {time.time() - start:.2f}s")
    return FileUploadResponse(
        filename=file.filename or "unknown",
        filepath=final_name,
        size=size,
        content_type=file.content_type or "application/octet-stream",
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
    file: UploadFile = File(...),  # noqa: B008
    redis: Redis = Depends(get_redis),  # noqa: B008
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

    logger.info(f"Video upload done in {time.time() - start:.2f}s size={dst.stat().st_size}")
    return FileUploadResponse(
        filename=file.filename or "unknown",
        filepath=unique,
        size=dst.stat().st_size,
        content_type=file.content_type or "application/octet-stream",
    )


async def _cleanup_set(redis: Redis, set_key: str, base_dir: Path) -> int:
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
    redis: Redis = Depends(get_redis),  # noqa: B008
):
    files_deleted = await _cleanup_set(redis, FILE_EXPIRY_SET, UPLOAD_DIR)
    videos_deleted = await _cleanup_set(redis, VIDEO_EXPIRY_SET, UPLOAD_VIDEO_DIR)
    downloads_deleted = 0
    if DOWNLOADS_DIR.exists():
        for p in DOWNLOADS_DIR.iterdir():
            if p.is_file():
                try:
                    p.unlink()
                    downloads_deleted += 1
                except Exception as e:  # noqa: BLE001
                    logger.error(f"Failed to delete {p}: {e}")
    return {
        "status": "ok",
        "files_deleted": files_deleted,
        "videos_deleted": videos_deleted,
        "downloads_deleted": downloads_deleted,
    }


@router.post(
    "/upload-archive",
    response_model=FileUploadResponse,
    summary="Upload an archive from a worker",
)
async def upload_worker_archive(
    file: UploadFile = File(...),  # noqa: B008
):
    """
    Allows a worker to upload an archive file.
    The file is saved to a dedicated worker archives directory.
    Returns a direct download URL for the archive.
    """
    if not file or not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File not provided or filename missing",
        )

    start_time = time.time()
    original_filename = file.filename
    file_extension = Path(original_filename).suffix

    # Generate a unique filename for storing on the server to prevent conflicts
    saved_filename = f"{uuid4().hex}{file_extension}"
    archive_save_path = WORKER_ARCHIVES_DIR / saved_filename

    # Ensure the target directory exists (it should be created by config.py, but good to be safe)
    WORKER_ARCHIVES_DIR.mkdir(parents=True, exist_ok=True)

    file_size = 0
    try:
        async with aiofiles.open(archive_save_path, "wb") as out_file:
            while chunk := await file.read(4 * 1024 * 1024):  # Read and write in 4MB chunks
                await out_file.write(chunk)
                file_size += len(chunk)
    except Exception as e:
        logger.error(
            f"Error saving worker archive '{original_filename}' to '{archive_save_path}': {e}"
        )
        # Attempt to clean up partially written file if an error occurs
        if archive_save_path.exists():
            try:
                os.unlink(archive_save_path)
            except Exception as unlink_e:
                logger.error(
                    f"Error cleaning up partial archive '{archive_save_path}' during save error: {unlink_e}"
                )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not save archive file.",
        ) from e

    logger.info(
        f"Worker archive '{original_filename}' uploaded as '{saved_filename}' to '{archive_save_path}'. Size: {file_size}. Took {time.time() - start_time:.2f}s."
    )

    return FileUploadResponse(
        filename=original_filename,
        filepath=saved_filename,
        size=file_size,
        content_type=file.content_type or "application/octet-stream",
    )


@router.get("/download-archive/{filename}", summary="Download a worker-uploaded archive")
async def download_worker_archive(filename: str):
    """
    Allows downloading of an archive previously uploaded by a worker.
    """
    file_path = WORKER_ARCHIVES_DIR / filename

    if not file_path.is_file():  # Check if the file exists and is a file
        logger.warning(
            f"Worker archive not found for download: '{filename}' at expected path '{file_path}'"
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Archive not found.")

    # For archives, 'application/octet-stream' is a safe default.
    # You could try to guess based on extension if needed for specific archive types.
    return FileResponse(
        path=file_path,
        filename=filename,  # This name will be suggested to the user when downloading
        media_type="application/octet-stream",
    )


class TwitterCookies(BaseModel):
    auth_token: str
    ct0: str


@router.post("/set-twitter-cookies", summary="Update Twitter auth cookies")
async def set_twitter_cookies(
    data: TwitterCookies,
    credentials: HTTPAuthorizationCredentials = Depends(security),  # noqa: B008
):
    """
    Update Twitter authentication cookies. Requires a valid Bearer token.
    """
    if credentials.scheme.lower() != "bearer" or credentials.credentials != TWITTER_COOKIES_TOKEN:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    redis: Redis = await get_redis()
    await redis.mset({"twitter_auth_token": data.auth_token, "twitter_ct0": data.ct0})
    return {"status": "success", "message": "Twitter cookies updated successfully"}
