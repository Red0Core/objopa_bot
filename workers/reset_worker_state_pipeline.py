from core.logger import logger
from workers.base_pipeline import BasePipeline
from workers.utils import send_notification  # Если нужно уведомление
from workers.worker_status_manager import WorkerStatusManager


class ResetWorkerStatePipeline(BasePipeline):
    def __init__(self, task_id: str, **params):
        self.task_id = task_id  # Для логирования
        self.worker_id = params.get("worker_id", "default_id")  # ID воркера, который нужно сбросить
        self.user_id = params.get("data", {}).get("user_id")  # Для уведомления

    async def run(self) -> None:
        logger.info(
            f"ResetWorkerStatePipeline: Запуск для worker_id: {self.worker_id} (Task: {self.task_id})"
        )

        try:
            # Очищаем выбранные изображения для этого воркера
            await WorkerStatusManager.clear_worker_selected_images(self.worker_id)
            logger.info(f"Worker {self.worker_id}: Выбранные изображения очищены.")

            # Сбрасываем флаг готовности анимаций для этого воркера
            await WorkerStatusManager.set_worker_animations_ready_flag(self.worker_id, False)
            logger.info(f"Worker {self.worker_id}: Флаг готовности анимаций сброшен.")

            # Можно добавить очистку других состояний, если они есть
            # await WorkerStatusManager.clear_worker_generated_videos(self.worker_id) # Если было

            logger.success(
                f"Worker {self.worker_id}: Состояние успешно сброшено (Task: {self.task_id})."
            )
            if self.user_id:
                await send_notification(
                    f"Состояние воркера (ID: ...{self.worker_id[-6:]}) было успешно сброшено.",
                    str(self.user_id),
                )
        except Exception as e:
            logger.error(
                f"Worker {self.worker_id}: Ошибка при сбросе состояния: {e}", exc_info=True
            )
            if self.user_id:
                await send_notification(
                    f"Произошла ошибка при сбросе состояния воркера (ID: ...{self.worker_id[-6:]}).",
                    str(self.user_id),
                )
