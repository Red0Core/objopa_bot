import asyncio
import json
from typing import Any
from httpx import AsyncClient
from redis.asyncio import Redis
from core.config import BACKEND_ROUTE, REDIS_HOST, REDIS_PORT, REDIS_PASSWORD
from core.logger import logger
from workers.base_worker import BaseWorker
from workers.image_generation_worker import ImageWorker

# Возможные типы задач и воркеры для них
WORKER_REGISTRY = {
    "image_generation": ImageWorker,
    # в будущем можно добавить другие типы воркеров
}

class QueueListener:
    def __init__(self, redis: Redis, queue_name: str = "task_queue"):
        self.redis = redis
        self.queue_name = queue_name

    async def listen(self):
        logger.info("Начинаем получать задачи...")
        while True:
            task_json = await self.redis.blpop(self.queue_name, timeout=5) # type: ignore
            if task_json:
                _, task_data = task_json
                try:
                    task = json.loads(task_data)
                    await self.process_task(task)
                except Exception as e:
                    logger.error(f"Ошибка при обработке задачи: {e}")
            else:
                await asyncio.sleep(1)

    async def process_task(self, task: dict[str, Any]):
        task_type = task.get("type", "")
        worker_cls = WORKER_REGISTRY.get(task_type)

        if not worker_cls:
            logger.warning(f"Неизвестный тип задачи: {task_type}")
            return

        worker: BaseWorker = worker_cls(task["task_id"], task["data"])
        logger.info(f"Запускаю воркер для задачи: {task_type}")
        await worker.run()
        logger.info(f"Задача {task["task_id"]} завершена.")

        result_key = f"result:image_selection:{task["task_id"]}"
        logger.info(f"Ожидание выбора изображения для задачи {task['task_id']} от пользователя {task['data']['user_id']}...")

        selection = None
        for _ in range(360): # 5 минут
            selection = await self.redis.get(result_key)
            if selection is not None:
                logger.info(f"Выбор получен: {selection}")
                await self.redis.delete(result_key)
                break
            await asyncio.sleep(1)

        if not selection:
            logger.warning(f"Таймаут ожидания выбора для задачи {task['task_id']}")
        else:
            selection = int(selection) + 1
            # Здесь можно обработать выбор пользователя
            logger.info(f"Пользователь выбрал изображение {selection} для задачи {task['task_id']}")
            # Например, отправить уведомление в другой сервис или сохранить результат в БД
            async with AsyncClient() as session:
                await session.post(
                    f"{BACKEND_ROUTE}/notify",
                    json={
                        "text": f"Генерация завершена для задачи {task['task_id']}. Выбрано изображение {selection}.",
                        "send_to": str(task["data"]["user_id"])
                    }
                )

if __name__ == "__main__":
    from redis.asyncio import Redis

    redis = Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD)
    listener = QueueListener(redis, "image_tasks")

    asyncio.run(listener.listen())
