import asyncio
import json
import signal
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from redis.exceptions import BusyLoadingError, ConnectionError, TimeoutError

from core.config import OBZHORA_CHAT_ID
from core.locks import LockAcquireError
from core.logger import logger
from core.redis_client import get_redis
from workers.animation_generation_pipeline import (
    AnimationGenerationPipeline,
    SetAnimationsForcePipeline,
)
from workers.base_pipeline import BasePipeline
from workers.concat_generation_pipeline import ConcatAnimationsPipeline
from workers.delete_images_folder_pipeline import DeleteImagesFolderPipeline
from workers.image_generation_pipeline import ImageGenerationPipeline
from workers.reset_worker_state_pipeline import ResetWorkerStatePipeline
from workers.utils import send_notification
from workers.video_generation_pipeline import VideoGenerationPipeline

WHO_LAUNCHED_WORKER = "Mihuil"  # ENGLISH NAME
CHAT_ID = OBZHORA_CHAT_ID
# False для отладки, True для продакшена
# Используется только для полного процесса, не для клавиатуры!!!
NEED_TO_RETURN_TO_QUEUE = True
queue_name = "hailuo_tasks"

# Возможные типы задач и воркеры для них
PIPELINE_TYPE_REGISTRY = {
    "video_generation": VideoGenerationPipeline,
    "image_generation": ImageGenerationPipeline,
    "animation_generation": AnimationGenerationPipeline,
    "delete_image_folder": DeleteImagesFolderPipeline,
    "concat_animations": ConcatAnimationsPipeline,
    "reset_worker_session": ResetWorkerStatePipeline,
    "set_animations_force": SetAnimationsForcePipeline,
}


class QueueListener:
    def __init__(self, queue_name):
        self.queue_name = queue_name

    async def listen(self):
        logger.info("Начинаем получать задачи...")
        # Уведомление о запуске лучше отправить до основного цикла
        try:
            await send_notification(
                f"Воркер запущен у {WHO_LAUNCHED_WORKER}. Жду задачи...", CHAT_ID
            )
        except Exception as notify_err:
            logger.error(f"Не удалось отправить уведомление о запуске: {notify_err}")

        self.shutdown_requested = False
        loop = asyncio.get_running_loop()

        # Добавляем обработчики сигналов (работает на Linux/macOS, на Windows может быть иначе)
        # Для Windows можно попробовать signal.SIGBREAK
        signals = (signal.SIGBREAK, signal.SIGTERM, signal.SIGINT)
        for s in signals:
            try:
                loop.add_signal_handler(s, lambda s=s: asyncio.create_task(self.shutdown(s)))
            except NotImplementedError:
                # add_signal_handler может быть не реализован на Windows для всех сигналов
                logger.warning(
                    f"Не удалось добавить обработчик для сигнала {s}. Возможно, Windows?"
                )

        while not self.shutdown_requested:
            try:
                redis = await get_redis()
            except (ConnectionError, TimeoutError, BusyLoadingError):
                logger.warning("Redis временно сдох. жду 2 секунды..")
                await asyncio.sleep(2)
                continue
            task_json = await redis.blpop(self.queue_name, timeout=5)  # type: ignore
            if task_json:
                _, task_data = task_json
                try:
                    task = json.loads(task_data)
                    if datetime.now(timezone.utc) - datetime.fromisoformat(
                        task["created_at"]
                    ) > timedelta(hours=3):
                        logger.info(f"Задача {task['task_id']} устарела (> 3 часа). Пропускаю.")
                        continue
                    await send_notification(
                        f"Получена задача {task['type']}: {task['task_id']}",
                        str(task["data"]["user_id"]),
                    )
                    await self.process_task(task)
                except LockAcquireError:
                    if NEED_TO_RETURN_TO_QUEUE and task_data.get("type") != "video_generation":
                        await redis.rpush(self.queue_name, task_data)  # type: ignore
                        logger.info("Задача помещена обратно в очередь.")
                    logger.info(
                        "Не удалось получить блокировку. Таймаут минута. (Можно выключить софт если не нужно)"
                    )
                    await asyncio.sleep(60)
                except Exception as e:
                    if NEED_TO_RETURN_TO_QUEUE and task_data.get("type") != "video_generation":
                        await redis.rpush(self.queue_name, task_data)  # type: ignore
                        logger.info("Задача помещена обратно в очередь.")
                    logger.exception(f"Ошибка при обработке задачи: {e}")
                    await asyncio.sleep(60)
            else:
                logger.info("Задач нет сплим 2 секунды...")
                await asyncio.sleep(2)

    async def process_task(self, task: dict[str, Any]):
        task_type = task.get("type", "")
        task["worker_id"] = WHO_LAUNCHED_WORKER
        worker_cls = PIPELINE_TYPE_REGISTRY.get(task_type)

        if not worker_cls:
            logger.warning(f"Неизвестный тип задачи: {task_type}")
            return

        pipeline: BasePipeline = worker_cls(**task)
        logger.info(f"Запускаю пайплайн для задачи: {task_type}")
        await pipeline.run()
        logger.info(f"Задача {task['task_id']} завершена.")

    async def shutdown(self, sig: signal.Signals):
        """Обработчик сигнала завершения."""
        logger.info(f"Получен сигнал {sig.name}. Начинаю завершение...")
        self.shutdown_requested = True
        # Здесь можно добавить логику для graceful shutdown, например, дождаться завершения текущей задачи

        # Отправляем уведомление о смерти *до* полной остановки
        try:
            # Используем небольшой таймаут на отправку
            async with asyncio.timeout(10):
                await send_notification(
                    f"Воркер остановлен сигналом {sig.name} у {WHO_LAUNCHED_WORKER}", CHAT_ID
                )
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

    async def clear_queue(self):
        """Очистка очереди путем удаления ключа."""
        try:
            redis = await get_redis()
            deleted_count = await redis.delete(self.queue_name)
            if deleted_count > 0:
                logger.info(f"Очередь '{self.queue_name}' успешно очищена (ключ удален).")
            else:
                logger.info(f"Очередь '{self.queue_name}' уже была пуста или не существовала.")
        except (ConnectionError, TimeoutError) as err:
            logger.error(f"Не удалось подключиться к Redis для очистки очереди: {err}")
        except Exception as e:
            logger.exception(f"Ошибка при очистке очереди '{self.queue_name}': {e}", exc_info=True)


if __name__ == "__main__":
    listener = QueueListener(queue_name)
    if len(sys.argv) > 1 and sys.argv[1] == "clear":
        asyncio.run(listener.clear_queue())
        logger.success("Очередь очищена.")
    else:
        asyncio.run(listener.listen())
