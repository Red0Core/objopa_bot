import asyncio
import json
import signal
from typing import Any
from core.logger import logger
from core.redis_client import get_redis
from core.locks import lock_hailuo, LockAcquireError
from workers.base_pipeline import BasePipeline
from workers.video_generation_pipeline import VideoGenerationPipeline, send_notification

WHO_LAUNCHED_WORKER = "Михуил"
CHAT_ID = "..."

# Возможные типы задач и воркеры для них
PIPELINE_TYPE_REGISTRY = {
    "video_generation": VideoGenerationPipeline,
}

class QueueListener:
    def __init__(self, queue_name):
        self.queue_name = queue_name

    async def listen(self):
        logger.info("Начинаем получать задачи...")
        # Уведомление о запуске лучше отправить до основного цикла
        try:
            await send_notification(f"Воркер запущен у {WHO_LAUNCHED_WORKER}. Жду задачи...", CHAT_ID)
        except Exception as notify_err:
            logger.error(f"Не удалось отправить уведомление о запуске: {notify_err}")

        self.shutdown_requested = False
        loop = asyncio.get_running_loop()

        # Добавляем обработчики сигналов (работает на Linux/macOS, на Windows может быть иначе)
        # Для Windows можно попробовать signal.SIGBREAK
        signals = (signal.SIGBREAK, signal.SIGTERM, signal.SIGINT)
        for s in signals:
            try:
                 loop.add_signal_handler(
                     s, lambda s=s: asyncio.create_task(self.shutdown(s))
                 )
            except NotImplementedError:
                 # add_signal_handler может быть не реализован на Windows для всех сигналов
                 logger.warning(f"Не удалось добавить обработчик для сигнала {s}. Возможно, Windows?")
        
        while not self.shutdown_requested:
            redis = await get_redis()
            task_json = await redis.blpop(self.queue_name, timeout=5) # type: ignore
            if task_json:
                _, task_data = task_json
                try:
                    task = json.loads(task_data)
                    await self.process_task(task)
                except LockAcquireError:
                    await redis.rpush(self.queue_name, task_data)
                    logger.info("Не удалось получить блокировку, задача помещена обратно в очередь. Таймаут минута. (Можно выключить софт если не нужно)")
                    await asyncio.sleep(60)
                except Exception as e:
                    await redis.rpush(self.queue_name, task_data)
                    logger.error(f"Ошибка при обработке задачи: {e}")
                    logger.info("Задача помещена обратно в очередь.")
                    await asyncio.sleep(60)
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

    async def shutdown(self, sig: signal.Signals):
        """Обработчик сигнала завершения."""
        logger.info(f"Получен сигнал {sig.name}. Начинаю завершение...")
        self.shutdown_requested = True
        # Здесь можно добавить логику для graceful shutdown, например, дождаться завершения текущей задачи

        # Отправляем уведомление о смерти *до* полной остановки
        try:
            # Используем небольшой таймаут на отправку
            async with asyncio.timeout(10):
                 await send_notification(f"Воркер остановлен сигналом {sig.name} у {WHO_LAUNCHED_WORKER}", CHAT_ID)
            logger.info("Уведомление об остановке отправлено.")
        except asyncio.TimeoutError:
             logger.error("Не удалось отправить уведомление об остановке (таймаут).")
        except Exception as notify_err:
            logger.error(f"Не удалось отправить уведомление об остановке: {notify_err}")

        # Даем циклу listen шанс завершиться
        await asyncio.sleep(1)

        # Отменяем все оставшиеся задачи (если нужно)
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        logger.info(f"Отменяю {len(tasks)} оставшихся задач...")
        [task.cancel() for task in tasks]
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("Завершение работы.")

if __name__ == "__main__":
    listener = QueueListener("hailuo_tasks")

    asyncio.run(listener.listen())
