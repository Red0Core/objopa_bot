from core.logger import logger
from workers.base_pipeline import BasePipeline
from workers.generator_factory import GeneratorFactory
from workers.utils import send_notification
from workers.worker_status_manager import WorkerStatusManager


class AnimationGenerationPipeline(BasePipeline):
    def __init__(self, task_id: str, **params):
        self.task_id = task_id
        self.worker_status_manager = WorkerStatusManager(
            params.get("worker_id", "default_id")
        )
        self.created_at = params.get("created_at")
        data = params.get("data", {})
        self.animation_prompts = data.get("animation_prompts", [])
        self.user_id = data.get("user_id")

        self.animation_generator = GeneratorFactory.create_animations_generator()

    async def run(self) -> None:
        """
        Запуск пайплайна генерации анимаций.
        """
        local_paths = await self.worker_status_manager.get_worker_selected_images(
            self.worker_status_manager.worker_id
        )
        if not local_paths:
            logger.error("No local paths found for animation generation.")
            await send_notification(
                "Нет локальных изображений для генерации анимаций.", str(self.user_id)
            )
            raise ValueError("No local paths found for animation generation.")

        # Фаза генерации видео с использованием выбранных путей локальных изображений
        await self.animation_generator.generate(local_paths, self.animation_prompts)
        await self.worker_status_manager.set_worker_animations_ready_flag(
            self.worker_status_manager.worker_id, True
        )

        # Отправляем уведомление о завершении генерации
        logger.success(f"Animation generation completed for task {self.task_id}")
        await send_notification("Генерация анимаций завершена.", str(self.user_id))


class SetAnimationsForcePipeline(BasePipeline):
    def __init__(self, task_id: str, **params):
        self.task_id = task_id
        self.worker_status_manager = WorkerStatusManager(
            params.get("worker_id", "default_id")
        )
        self.created_at = params.get("created_at")
        data = params.get("data", {})
        self.user_id = data.get("user_id")

    async def run(self) -> None:
        """
        Запуск пайплайна установки флага наличия анимаций.
        """
        await self.worker_status_manager.set_worker_animations_ready_flag(
            self.worker_status_manager.worker_id, True
        )
        logger.success(f"Force animation generation flag set for task {self.task_id}")
        await send_notification("Флаг наличия анимаций установлен.", str(self.user_id))
