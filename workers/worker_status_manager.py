import json
from pathlib import Path
from core.logger import logger
from core.redis_client import get_redis
from datetime import datetime, timezone

# Время памяти воркера 
WORKER_MEMORY_TTL_SECONDS = 8 * 60 * 60

class WorkerStatusManager:
    """
    Менеджер статусов воркеров и промежуточных результатов задач,
    выполняемых на конкретном воркере.
    """
    def __init__(self, worker_id: str):
        self._worker_id = worker_id

    @property
    def worker_id(self) -> str:
        """Возвращает ID воркера."""
        return self._worker_id

    # --- Методы для статуса самого воркера ---
    async def set_worker_phase_status(self, pipeline_type: str, phase_status: str):
        """
        Устанавливает статус текущей фазы для указанного типа пайплайна на ЭТОМ воркере.
        Например: pipeline_type="image_generation", phase_status="awaiting_user_selection"
        """
        redis = await get_redis()
        worker_state_key = f"worker_state:{self._worker_id}"
        await redis.hset(worker_state_key, f"{pipeline_type}_phase", phase_status) # type: ignore
        await redis.hset(worker_state_key, "last_status_update", datetime.now(timezone.utc).isoformat()) # type: ignore
        logger.info(f"Worker {self._worker_id} {pipeline_type} phase status set to {phase_status}.")

    async def get_worker_phase_status(self, pipeline_type: str) -> str | None:
        """
        Получает статус текущей фазы для указанного типа пайплайна на ЭТОМ воркере.
        """
        redis = await get_redis()
        worker_state_key = f"worker_state:{self._worker_id}"
        status = await redis.hget(worker_state_key, f"{pipeline_type}_phase") # type: ignore
        return status

    # --- Статические методы для управления "памятью" выбранных изображений НА КОНКРЕТНОМ ВОРКЕРЕ ---
    @staticmethod
    def _get_worker_selected_images_key(worker_id: str) -> str:
        """Формирует ключ для хранения выбранных изображений на конкретном воркере."""
        # task_id здесь не нужен, так как предполагается, что воркер работает над одной "сессией" выбора за раз,
        # либо следующий этап на этом же воркере должен забрать последние выбранные изображения.
        return f"worker_memory:{worker_id}:selected_images"

    @staticmethod
    async def save_worker_selected_images(worker_id: str, image_paths: list[Path]):
        """
        Сохраняет список путей к выбранным изображениям для конкретного воркера.
        image_paths: Список строк (серверные или относительные пути).
        """
        redis = await get_redis()
        key = WorkerStatusManager._get_worker_selected_images_key(worker_id)
        try:
            # Преобразуем пути к изображениям в строки для хранения в Redis
            image_paths_str = [str(path) for path in image_paths]
            await redis.set(key, json.dumps(image_paths_str), ex=WORKER_MEMORY_TTL_SECONDS)
            logger.info(f"Сохранены выбранные пути изображений для worker '{worker_id}'. Ключ: {key}")
        except Exception as e:
            logger.error(f"Ошибка сохранения выбранных изображений для worker '{worker_id}': {e}", exc_info=True)
            raise

    @staticmethod
    async def get_worker_selected_images(worker_id: str) -> list[Path] | None:
        """
        Извлекает список путей к выбранным изображениям для конкретного воркера.
        Возвращает None, если данные не найдены.
        """
        redis = await get_redis()
        key = WorkerStatusManager._get_worker_selected_images_key(worker_id)
        try:
            paths_json = await redis.get(key)
            if paths_json:
                image_paths = json.loads(paths_json)
                logger.info(f"Извлечены выбранные пути изображений для worker '{worker_id}'. Ключ: {key}")
                for i in range(len(image_paths)):
                    image_paths[i] = Path(image_paths[i])
                return image_paths
            else:
                logger.info(f"Не найдены выбранные изображения для worker '{worker_id}'. Ключ: {key}")
                return None
        except Exception as e:
            logger.error(f"Ошибка извлечения выбранных изображений для worker '{worker_id}': {e}", exc_info=True)
            return None

    @staticmethod
    async def clear_worker_selected_images(worker_id: str):
        """
        Удаляет сохраненные пути к выбранным изображениям для конкретного воркера.
        """
        redis = await get_redis()
        key = WorkerStatusManager._get_worker_selected_images_key(worker_id)
        try:
            await redis.delete(key)
            logger.info(f"Удалены выбранные изображения для worker '{worker_id}'. Ключ: {key}")
        except Exception as e:
            logger.error(f"Ошибка удаления выбранных изображений для worker '{worker_id}': {e}", exc_info=True)
            raise
    
    # --- Статические методы для управления флагом готовности АНИМАЦИЙ НА КОНКРЕТНОМ ВОРКЕРЕ ---
    @staticmethod
    def _get_worker_animations_ready_flag_key(worker_id: str) -> str:
        """Формирует ключ для флага готовности анимаций на конкретном воркере."""
        return f"worker_memory:{worker_id}:animations_ready_flag"

    @staticmethod
    async def set_worker_animations_ready_flag(worker_id: str, are_ready: bool):
        """
        Устанавливает или сбрасывает флаг готовности анимаций для конкретного воркера.
        """
        redis = await get_redis()
        key = WorkerStatusManager._get_worker_animations_ready_flag_key(worker_id)
        try:
            if are_ready:
                # Устанавливаем ключ со значением "1" и TTL
                await redis.set(key, "1", ex=WORKER_MEMORY_TTL_SECONDS)
                logger.info(f"Установлен флаг готовности анимаций для worker '{worker_id}'. Ключ: {key}")
            else:
                # Если не готовы, просто удаляем ключ
                await redis.delete(key)
                logger.info(f"Сброшен (удален) флаг готовности анимаций для worker '{worker_id}'. Ключ: {key}")
        except Exception as e:
            logger.error(f"Ошибка установки/сброса флага готовности анимаций для worker '{worker_id}': {e}", exc_info=True)
            raise
    
    @staticmethod
    async def check_worker_animations_ready_flag(worker_id: str) -> bool:
        """
        Проверяет флаг готовности анимаций для конкретного воркера.
        Возвращает True, если флаг установлен (ключ существует), иначе False.
        """
        redis = await get_redis()
        key = WorkerStatusManager._get_worker_animations_ready_flag_key(worker_id)
        try:
            exists = await redis.exists(key)
            if exists:
                logger.info(f"Проверка флага: Анимации готовы для worker '{worker_id}'. Ключ: {key}")
                return True
            else:
                logger.info(f"Проверка флага: Анимации НЕ готовы для worker '{worker_id}'. Ключ: {key}")
                return False
        except Exception as e:
            logger.error(f"Ошибка проверки флага готовности анимаций для worker '{worker_id}': {e}", exc_info=True)
            return False # В случае ошибки считаем, что не готовы, для безопасности
