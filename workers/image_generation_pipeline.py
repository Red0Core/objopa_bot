# Импортируем фабрику
import asyncio
from pathlib import Path
import uuid

from httpx import AsyncClient
from core.config import BACKEND_ROUTE
from core.redis_client import get_redis
from workers.base_pipeline import BasePipeline
from workers.generator_factory import GeneratorFactory
from core.logger import logger
from workers.utils import upload_file_to_backend, send_notification
from workers.worker_status_manager import WorkerStatusManager

class ImageGenerationPipeline(BasePipeline):
    def __init__(self, task_id: str, **params):
        self.task_id = task_id
        self.worker_status_manager = WorkerStatusManager(params.get("worker_id", "default_id"))
        self.created_at = params.get("created_at")
        data = params.get("data", {})
        self.image_prompts = data.get("image_prompts", [])
        self.user_id = data.get("user_id")

        # Инициализируем генераторы
        self.image_generator = GeneratorFactory.create_image_generator()
    
    async def run(self) -> None:
        """
        Запуск пайплайна генерации изображений.
        """
        logger.info(f"ImageGenerationPipeline: {self.task_id}, prompts: {self.image_prompts}, user_id: {self.user_id}")
        
        # Генерация изображений с выбором пользователем
        try:
            final_local_paths = await self.generate_images_with_selection()
            # Отправляем уведомление о завершении генерации
            await send_notification(
                f"Генерация изображений завершена. {len(final_local_paths)} изображений.",
                str(self.user_id)
            )
        except Exception as e:
            logger.exception(f"Ошибка при генерации изображений: {e}", exc_info=True)
            await self.worker_status_manager.clear_worker_selected_images(self.worker_status_manager.worker_id)
            

    async def generate_images_with_selection(self) -> list[Path]:
        """
        Генерация изображений с выбором пользователем.

        Returns:
            list[Path]: Список локальных путей к выбранным изображениям.
        """
        # Генерация и загрузка изображений на сервак
        current_local_paths: list[list[Path]] = await self.image_generator.generate(self.image_prompts)
        current_server_paths: list[list[str]] = await self._upload_groups(current_local_paths)
        logger.info(f"Server paths: {current_server_paths}\nLocal paths: {current_local_paths}")

        # Инициализация состояния выбора
        final_chosen_local_paths: list[Path | None] = [None] * len(self.image_prompts)
        indices_requiring_regenerate: list[int] = []

        # Стадия выбора изображений и последующей перегенерации
        while None in final_chosen_local_paths:
            # Фаза выбора изображений
            for idx, server_group in enumerate(current_server_paths):
                logger.info(f"Task ID: {self.task_id}; User ID: {self.user_id}; Server Group: {server_group}")
                if final_chosen_local_paths[idx] is None:
                    # Отправляем уведомление с выбором изображений
                    image_selection_task_id = await self.send_one_group_of_image(server_group)
                    # Ждем выбора пользователя
                    selection_index = await self.get_images_selection(str(image_selection_task_id))
                    if selection_index == -1:
                        # Если пользователь выбрал пересоздание, то добавляем в список
                        indices_requiring_regenerate.append(idx)
                    else:
                        # Сохраняем выбранный путь
                        final_chosen_local_paths[idx] = current_local_paths[idx][selection_index]
            
            # Фаза перегенерации
            if indices_requiring_regenerate:
                # Генерируем только те группы, которые нужно перегенерировать
                logger.info(f"Перегенерация сцен: {indices_requiring_regenerate}")
                # Возвращает только перегенерированные группы len(indices_requiring_regenerate)
                regenerated_local_paths = await self.image_generator.generate(self.image_prompts, indices_requiring_regenerate)
                regenerated_server_paths = await self._upload_groups(regenerated_local_paths)
                
                for i, original_idx in enumerate(indices_requiring_regenerate):
                    # Обновляем пути для перегенерированных сцен
                    current_server_paths[original_idx] = regenerated_server_paths[i]
                    current_local_paths[original_idx] = regenerated_local_paths[i]
                
                indices_requiring_regenerate.clear()  # Очищаем список для следующей итерации
        # После выбора, финальная корректировка путей
        final_local_paths: list[Path] = []
        for idx, server_group in enumerate(current_local_paths):
            if final_chosen_local_paths[idx] is None:
                # Если все еще None, то берем первое изображение из группы
                final_local_paths.append(server_group[0])
            else:
                path = final_chosen_local_paths[idx]
                assert path is not None  # Линтер гадит, что path может быть None
                final_local_paths.append(path)
        
        # Сохраняем выбранные изображения в Redis для текущего воркера
        await self.worker_status_manager.clear_worker_selected_images(self.worker_status_manager.worker_id)
        await self.worker_status_manager.save_worker_selected_images(self.worker_status_manager.worker_id, final_local_paths)

        return final_local_paths

    async def _upload_groups(self, local_paths_groups: list[list[Path]]) -> list[list[str]]:
        """Параллельно загружает группы изображений и возвращает серверные пути."""
        all_upload_tasks = []
        for group in local_paths_groups:
            if group:
                group_tasks = [upload_file_to_backend(image_path) for image_path in group]
                all_upload_tasks.append(asyncio.gather(*group_tasks)) # Оборачиваем в газер для будущего await
            else:
                # Добавляем "пустую" корутину, чтобы сохранить структуру
                all_upload_tasks.append(asyncio.sleep(0, result=[]))

        try:
            server_paths_groups = await asyncio.gather(*all_upload_tasks)
            logger.info(f"Загружено {sum(len(g) for g in server_paths_groups)} изображений из {len(server_paths_groups)} групп.")
            return server_paths_groups
        except Exception as upload_err:
            logger.error(f"Критическая ошибка при массовой загрузке изображений: {upload_err}", exc_info=True)
            # Пробрасываем ошибку, чтобы прервать пайплайн
            raise RuntimeError("Ошибка загрузки изображений") from upload_err
    
    async def send_one_group_of_image(self, images: list[str]) -> uuid.UUID:
        # Отправка уведомления в бекенд
        image_selection_task_id = uuid.uuid4()
        logger.info(f"Task ID: {self.task_id}; User ID: {self.user_id}; Images: {images}")
        async with AsyncClient() as session:
            await session.post(
                f"{BACKEND_ROUTE}/notify/image-selection",
                json={
                    "task_id": str(image_selection_task_id),
                    "user_id": self.user_id, # В тг боте это user_id
                    "relative_paths": images,
                }
            )
        logger.info(f"Task ID: {self.task_id}; Images sent to backend.")
        return image_selection_task_id

    async def get_images_selection(self, task_id: str) -> int:
        while True:
            redis = await get_redis()
            selection_index = await redis.get(f"result:image_selection:{task_id}")
            if selection_index is not None:
                logger.info(f"Выбор получен: {selection_index}")
                await redis.delete(f"result:image_selection:{task_id}")
                return int(selection_index)
            await asyncio.sleep(1)
