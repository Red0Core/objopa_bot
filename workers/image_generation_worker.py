from workers.base_worker import BaseWorker
from core.logger import logger
import httpx
from core.config import BACKEND_ROUTE
from httpx import AsyncClient

class ImageWorker(BaseWorker):
    async def run(self) -> None:
        # Пример обработки задачи генерации изображений
        logger.info(f"Task ID: {self.task_id}; Params: {self.params}")

        images = [f"https://dummyimage.com/600x400/000/fff&text=Image+{i+1}" for i in range(len(self.params["prompts"]))]

        # Отправка уведомления в бекенд
        async with AsyncClient() as session:
            await session.post(
                f"{BACKEND_ROUTE}/notify/image-selection",
                json={
                    "task_id": self.task_id,
                    "user_id": self.params["user_id"],  # Заменить на нужный ID
                    "images": images
                }
            )
            logger.info(f"Task ID: {self.task_id}; Images sent to backend.")
