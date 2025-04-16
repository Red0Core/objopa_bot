import asyncio

from pathlib import Path
from core.redis_client import redis
from workers.base_pipeline import BasePipeline
from core.logger import logger
from core.config import BACKEND_ROUTE, BASE_DIR
from httpx import AsyncClient
import uuid

# Импортируем фабрику
from workers.generator_factory import GeneratorFactory

class VideoGenerationPipeline(BasePipeline):
    def __init__(self, task_id: str, **params):
        self.task_id = task_id
        self.created_at = params.get("created_at")
        data = params.get("data", {})
        self.image_prompts = data.get("image_prompts", [])
        self.animation_prompts = data.get("animation_prompts", [])
        self.image_prompts = data.get("image_prompts", [])
        self.animation_prompts = data.get("animation_prompts", [])
        self.user_id = data.get("user_id")

        # Инициализируем генераторы
        self.image_generator = GeneratorFactory.create_image_generator()
        self.video_generator = GeneratorFactory.create_video_generator()

    async def run(self) -> None:
        # Пример обработки задачи генерации изображений
        logger.info(f"Task ID: {self.task_id}; User ID: {self.user_id}; Image Prompts: {self.image_prompts}; Animations Prompts: {self.animation_prompts}")

        all_generated_images_paths = await self.generate_images(self.image_prompts)
        all_server_images_paths: list[list[str]] = []
        
        # Шаг 0: Параллельно загружаем все изображения на сервер батчами
        for group_of_images in all_generated_images_paths:
            upload_tasks = []
            for image in group_of_images:
                upload_tasks.append(upload_file_to_backend(image))
            all_server_images_paths.append([x for x in await asyncio.gather(*upload_tasks)])

        logger.info(f"Все изображения загружены на сервер: {all_server_images_paths}")
        # Шаг 1: Параллельно отправляем все группы изображений
        send_tasks = []
        for i, group_of_images in enumerate(all_server_images_paths):
            # Создаем задачи для параллельной отправки
            task = asyncio.create_task(
                self.send_one_group_of_image(group_of_images)
            )
            send_tasks.append(task)
        
        # Ожидаем завершения всех отправок и получаем идентификаторы задач
        task_ids = await asyncio.gather(*send_tasks)
        logger.info(f"Все группы изображений отправлены успешно: {len(task_ids)} групп")
        
        # Шаг 2: Последовательно ожидаем выбор пользователя для каждой группы
        # (оставляем последовательным для лучшего пользовательского опыта)
        final_selected_images = []
        for i, task_id in enumerate(task_ids):
            logger.info(f"Ожидание выбора для группы {i+1}/{len(task_ids)}...")
            selection_index = await self.get_images_selection(str(task_id))
            # Convert to integer and select the image from the group
            selection_index = int(selection_index)
            selected_image = all_generated_images_paths[i][selection_index]
            final_selected_images.append(selected_image)
            logger.info(f"Выбрано изображение из группы {i+1}: {selected_image}")
            

        # Генерация видео из выбранных изображений
        video_path = await self.generate_video(final_selected_images, self.animation_prompts)

        # Отправляем уведомление о завершении генерации
        await self.send_notification(f"Генерация видео завершена. Путь к видео: {video_path}")
        
    async def generate_images(self, prompts: list[str]) -> list[list[Path]]:
        # Например, вызов API генерации изображений
        return await self.image_generator.generate(prompts)
    

    async def generate_video(self, images: list[Path], prompts) -> Path:
        # Например, вызов API генерации видео
        return await self.video_generator.generate(images, prompts)

    async def send_one_group_of_image(self, images: list[str]) -> uuid.UUID:
        # Отправка уведомления в бекенд
        image_selection_task_id = uuid.uuid4()
        async with AsyncClient() as session:
            await session.post(
                f"{BACKEND_ROUTE}/notify/image-selection",
                json={
                    "task_id": str(image_selection_task_id),
                    "user_id": self.user_id, # В тг боте это user_id
                    "relative_paths": images
                }
            )
        logger.info(f"Task ID: {self.task_id}; Images sent to backend.")
        return image_selection_task_id

    async def get_images_selection(self, task_id: str) -> int:
        while True:
            selection_index = await redis.get(f"result:image_selection:{task_id}")
            if selection_index is not None:
                logger.info(f"Выбор получен: {selection_index}")
                await redis.delete(f"result:image_selection:{task_id}")
                return selection_index
            await asyncio.sleep(1)
    
    async def send_notification(self, text: str, send_to: int | None = None) -> None:
        if send_to is None:
            send_to = self.user_id
        
        try:
            async with AsyncClient() as session:
                session.headers.update({
                    "accept": "application/json",
                    "Content-Type": "application/json"
                })
                req = await session.post(
                    f"{BACKEND_ROUTE}/notify",
                    json={
                        "text": text,
                        "send_to": str(send_to)
                    }
                )
                req.raise_for_status()
            logger.info(f"Уведомление отправлено: {text} для {send_to}")
        except Exception as e:
            logger.error(f"Ошибка при отправке уведомления: {e}")

async def upload_file_to_backend(file_path: Path, backend_url: str = BACKEND_ROUTE) -> str:
    """
    Загружает файл на сервер и возвращает путь к нему.
    
    Args:
        file_path: Путь к локальному файлу для загрузки
        backend_url: Базовый URL бэкенда
        
    Returns:
        Путь к файлу на сервере
    """
    try:
        # Проверяем существование файла
        if not file_path.exists():
            raise FileNotFoundError(f"Файл не найден: {file_path}")
            
        # Открываем файл в двоичном режиме
        with open(file_path, "rb") as file_data:
            # Получаем только имя файла без пути
            file_name = file_path.name
            
            # Создаем multipart/form-data запрос
            files = {"file": (file_name, file_data, "application/octet-stream")}
            
            # Отправляем запрос
            async with AsyncClient() as client:
                response = await client.post(
                    f"{backend_url}/worker/upload",
                    files=files
                )
                
                # Проверяем статус ответа
                response.raise_for_status()
                
                # Парсим ответ
                result = response.json()
                logger.info(f"Файл {file_name} успешно загружен на сервер: {result['filepath']}")
                
                # Возвращаем путь к файлу на сервере
                return result['filepath']
                
    except FileNotFoundError as e:
        logger.error(f"Ошибка при загрузке файла: {e}")
        raise
    except Exception as e:
        logger.error(f"Ошибка при загрузке файла: {e}")
        raise RuntimeError(f"Не удалось загрузить файл: {str(e)}")
