import asyncio
from pathlib import Path
from fastapi import APIRouter, File, HTTPException, UploadFile, status, BackgroundTasks
from uuid import uuid4
from datetime import datetime, timedelta
from pydantic import BaseModel, Field
from core.config import UPLOAD_DIR
from core.logger import logger
from core.redis_client import redis
from backend.models.workers import BaseWorkerTask, FileUploadResponse, ImageGenerationTaskData
import os
import xxhash
import aiofiles
import time

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

# Глобальные константы
FILE_HASH_KEY_PREFIX = "file_hash:"
FILE_STATS_KEY_PREFIX = "file_stats:"
FILE_EXPIRY_SET = "expired_files"
DEFAULT_EXPIRY = timedelta(days=1)

@router.post("/upload", response_model=FileUploadResponse)
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    """Загружает файл с оптимизацией для снижения CPU-нагрузки"""
    if not file:
        raise HTTPException(status_code=400, detail="Файл не предоставлен")
    
    try:
        start = time.time()
        # Создаем временное имя файла
        file_ext = os.path.splitext(file.filename or "")[1]
        temp_filename = f"temp_{uuid4().hex}{file_ext}"
        temp_path = UPLOAD_DIR / temp_filename
        
        # Обеспечиваем наличие директории
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        
        # 1. ОПТИМИЗАЦИЯ: Используем более легкий хеш-алгоритм
        hasher = xxhash.xxh64()  # Намного быстрее MD5
        file_size = 0
        
        # 2. ОПТИМИЗАЦИЯ: Более эффективный чанк для баланса I/O и CPU
        chunk_size = 4 * 1024 * 1024  # 4MB chunks
        
        # Открываем файл для записи
        async with aiofiles.open(temp_path, 'wb') as out_file:
            # 3. ОПТИМИЗАЦИЯ: Хешируем только первые 20MB для очень больших файлов
            hash_limit = 20 * 1024 * 1024  # 20MB hash limit
            bytes_hashed = 0
            
            while chunk := await file.read(chunk_size):
                file_size += len(chunk)
                
                # Хешируем только начало файла для очень больших файлов
                if bytes_hashed < hash_limit:
                    bytes_to_hash = min(len(chunk), hash_limit - bytes_hashed)
                    hasher.update(chunk[:bytes_to_hash])
                    bytes_hashed += bytes_to_hash
                
                await out_file.write(chunk)
        
        # Получаем хеш файла
        file_hash = hasher.hexdigest()
        file_hash_with_size = f"{file_hash}_{file_size}"  # Добавляем размер для уникальности
        
        # Формируем ключи Redis
        file_hash_key = f"{FILE_HASH_KEY_PREFIX}{file_hash_with_size}"
        
        # 4. ОПТИМИЗАЦИЯ: Первая проверка на дубликат через кэш в памяти
        # (этот шаг можно реализовать с LRU-кэшем если нужно)
        
        # 5. ОПТИМИЗАЦИЯ: Проверяем дубликат в Redis одним запросом
        existing_filepath = await redis.get(file_hash_key)
        
        if existing_filepath:
            # Дубликат найден - запускаем асинхронное удаление временного файла
            background_tasks.add_task(os.unlink, temp_path)
            
            # 6. ОПТИМИЗАЦИЯ: Отложенное обновление статистики
            asyncio.create_task(
                update_file_stats(
                    file_hash_key, 
                    f"{FILE_STATS_KEY_PREFIX}{file_hash_with_size}",
                    DEFAULT_EXPIRY
                )
            )
            logger.info(f"{time.time()-start} время с существующим файлом")
            return FileUploadResponse(
                filename=file.filename or "unknown",
                filepath=existing_filepath.decode() if isinstance(existing_filepath, bytes) else existing_filepath,
                size=file_size,
                content_type=file.content_type or "application/octet-stream",
            )
        
        # Создаем финальное имя файла
        unique_filename = f"{file_hash[:8]}_{uuid4().hex[:4]}{file_ext}"
        final_path = UPLOAD_DIR / unique_filename
        
        # 7. ОПТИМИЗАЦИЯ: Прямая запись без временных файлов для небольших файлов
        if file_size < 10 * 1024 * 1024:  # Меньше 10MB
            os.rename(temp_path, final_path)
        else:
            # Для больших файлов используем асинхронное переименование
            background_tasks.add_task(os.rename, temp_path, final_path)
        
        # 8. ОПТИМИЗАЦИЯ: Минимальный набор данных для Redis
        file_info = {
            "size": file_size,
            "content_type": file.content_type or "application/octet-stream",
            "upload_time": int(datetime.now().timestamp())
        }
        
        # 9. ОПТИМИЗАЦИЯ: Более компактный pipeline для Redis
        expiry_seconds = int(DEFAULT_EXPIRY.total_seconds())
        async with redis.pipeline(transaction=False) as pipe:  # transaction=False для скорости
            await pipe.set(file_hash_key, unique_filename, ex=expiry_seconds)
            await pipe.hset(f"{FILE_STATS_KEY_PREFIX}{file_hash_with_size}", mapping=file_info)
            await pipe.zadd(FILE_EXPIRY_SET, {str(final_path): datetime.now().timestamp() + expiry_seconds})
            await pipe.execute()
        
        # 10. ОПТИМИЗАЦИЯ: Логирование только для больших файлов
        if file_size > 1024 * 1024:  # Больше 1MB
            logger.info(f"Файл {file.filename} ({human_readable_size(file_size)}) загружен как {unique_filename}")
        logger.info(f"{time.time() - start} время без существующего файла")
        return FileUploadResponse(
            filename=file.filename or "unknown",
            filepath=unique_filename,
            size=file_size,
            content_type=file.content_type or "application/octet-stream"
        )
    
    except Exception as e:
        # В случае ошибки удаляем временный файл, если он создан
        if 'temp_path' in locals() and os.path.exists(temp_path):
            background_tasks.add_task(os.unlink, temp_path)
        
        logger.error(f"Ошибка при загрузке файла: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка сохранения файла: {str(e)}")

# Вспомогательная функция для фонового обновления статистики
async def update_file_stats(hash_key: str, stats_key: str, expiry: timedelta):
    """Фоновое обновление статистики файла"""
    try:
        async with redis.pipeline() as pipe:
            await pipe.hincrby(stats_key, "usage_count", 1)
            await pipe.expire(hash_key, expiry)
            await pipe.expire(stats_key, expiry)
            await pipe.execute()
    except Exception as e:
        logger.error(f"Ошибка при обновлении статистики файла: {e}")

def human_readable_size(size, decimal_places=2):
    """Преобразует размер в байтах в человеко-читаемый формат"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB', 'PB']:
        if size < 1024.0 or unit == 'PB':
            break
        size /= 1024.0
    return f"{size:.{decimal_places}f} {unit}"

async def cleanup_expired_files():
    """Фоновая задача для очистки истекших файлов"""
    try:
        now = datetime.now().timestamp()
        
        # Получаем файлы с истекшим сроком
        expired = await redis.zrangebyscore(FILE_EXPIRY_SET, 0, now)
        
        if not expired:
            return
            
        logger.info(f"Обнаружено {len(expired)} файлов для очистки")
        
        for file_path_str in expired:
            file_path = Path(file_path_str)
            if file_path.exists():
                try:
                    os.remove(file_path)
                    logger.info(f"Удален устаревший файл: {file_path}")
                except Exception as e:
                    logger.error(f"Ошибка удаления файла {file_path}: {e}")
            
            # Удаляем запись из множества независимо от результата
            await redis.zrem(FILE_EXPIRY_SET, file_path_str)
            
    except Exception as e:
        logger.error(f"Ошибка в процессе очистки файлов: {e}")

@router.post("/cleanup")
async def manual_cleanup():
    """Ручной запуск очистки устаревших файлов"""
    await cleanup_expired_files()
    return {"status": "cleanup_started"}
