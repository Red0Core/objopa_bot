import asyncio
import uuid
from pathlib import Path

from httpx import AsyncClient

from core.config import BACKEND_ROUTE
from core.logger import logger
from core.redis_client import get_redis
from workers.base_pipeline import BasePipeline

# Импортируем фабрику
from workers.generator_factory import GeneratorFactory


class VideoGenerationPipeline(BasePipeline):
    def __init__(self, task_id: str, **params):
        self.task_id = task_id
        self.created_at = params.get("created_at")
        data = params.get("data", {})
        self.image_prompts = data.get("image_prompts", [])
        self.animation_prompts = data.get("animation_prompts", [])
        self.user_id = data.get("user_id")

        # Инициализируем генераторы
        self.image_generator = GeneratorFactory.create_image_generator()
        self.video_generator = GeneratorFactory.create_video_generator()

    async def run(self) -> None:
        # Пример обработки задачи генерации изображений
        logger.info(f"Task ID: {self.task_id}; User ID: {self.user_id}; Image Prompts: {self.image_prompts}; Animations Prompts: {self.animation_prompts}")

        current_local_paths: list[list[Path]] = await self.generate_images(self.image_prompts)
        current_server_paths: list[list[str]] = await self._upload_groups(current_local_paths)
        
        # Инициализация состояния выбора
        final_chosen_local_paths: list[Path | None] = [None] * len(self.image_prompts)
        indices_requiring_regenerate: list[int] = []

        # Перебор до тех пор пока все не будут выбраны 
        while None in final_chosen_local_paths:
            # Фаза выбора изображений
            for idx, server_group in enumerate(current_server_paths):
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
                regenerated_local_paths = await self.generate_images(self.image_prompts, indices_requiring_regenerate)
                regenerated_server_paths = await self._upload_groups(regenerated_local_paths)
                
                for i, original_idx in enumerate(indices_requiring_regenerate):
                    # Обновляем пути для перегенерированных сцен
                    current_server_paths[original_idx] = regenerated_server_paths[i]
                    current_local_paths[original_idx] = regenerated_local_paths[i]
                
                indices_requiring_regenerate.clear()  # Очищаем список для следующей итерации
        
        final_local_paths: list[Path] = []
        for idx, server_group in enumerate(current_local_paths):
            if final_chosen_local_paths[idx] is None:
                # Если все еще None, то берем первое изображение из группы
                final_local_paths.append(server_group[0])
            else:
                path = final_chosen_local_paths[idx]
                assert path is not None  # Линтер гадит, что path может быть None
                final_local_paths.append(path)

        # Генерация видео из выбранных изображений
        video_path = await self.generate_video(final_local_paths, self.animation_prompts)

        # Загружаем видео на сервер
        video_server_path = await upload_file_to_backend(video_path, is_video=True)
        # Отправляем уведомление о завершении генерации
        await self.send_notification("Генерация и загрузка видео завершена. " \
                                     f"Cсылка к видео: {BACKEND_ROUTE}/worker/download-video/{video_server_path}")
        
    async def generate_images(self, prompts: list[str], indicies_to_generate: list[int] | None = None) -> list[list[Path]]:
        # Например, вызов API генерации изображений
        return await self.image_generator.generate(prompts, indicies_to_generate)

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

    async def send_notification(self, text: str, send_to: str | None = None) -> None:
        if send_to is None:
            send_to = str(self.user_id)
        # Отправляем уведомление через бекенд
        await send_notification(text, send_to)

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


    
async def send_notification(text: str, send_to: str) -> None:    
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
                    "send_to": send_to
                }
            )
            req.raise_for_status()
        logger.info(f"Уведомление отправлено: {text} для {send_to}")
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления: {e}")

async def upload_file_to_backend(file_path: Path, backend_url: str = BACKEND_ROUTE, is_video=False) -> str:
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
                    f"{backend_url}/worker/upload{'-video' if is_video else ''}",
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
        raise RuntimeError(f"Не удалось загрузить файл: {str(e)}") from e
