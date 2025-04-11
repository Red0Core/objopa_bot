import asyncio
import json
from typing import Any
from httpx import AsyncClient
from redis.asyncio import Redis
from core.config import BACKEND_ROUTE
from core.logger import logger
from core.redis_client import redis
from workers.base_pipeline import BasePipeline
from workers.video_generation_pipeline import VideoGenerationPipeline

# Возможные типы задач и воркеры для них
PIPELINE_TYPE_REGISTRY = {
    "video_generation": VideoGenerationPipeline,
}

class QueueListener:
    def __init__(self, queue_name):
        self.queue_name = queue_name

    async def listen(self):
        logger.info("Начинаем получать задачи...")
        while True:
            task_json = await redis.blpop(self.queue_name, timeout=5) # type: ignore
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
        worker_cls = PIPELINE_TYPE_REGISTRY.get(task_type)

        if not worker_cls:
            logger.warning(f"Неизвестный тип задачи: {task_type}")
            return

        pipeline: BasePipeline = worker_cls(**task)
        logger.info(f"Запускаю пайплайн для задачи: {task_type}")
        await pipeline.run()
        logger.info(f"Задача {task["task_id"]} завершена.")

if __name__ == "__main__":
    listener = QueueListener("hailuo_tasks")

    asyncio.run(listener.listen())
