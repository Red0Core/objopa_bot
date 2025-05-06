from core.config import BACKEND_ROUTE
from core.logger import logger
from workers.base_pipeline import BasePipeline
from workers.generator_factory import GeneratorFactory
from workers.utils import send_notification, upload_file_to_backend
from workers.worker_status_manager import WorkerStatusManager


class ConcatAnimationsPipeline(BasePipeline):
    def __init__(self, task_id: str, **params):
        self.task_id = task_id
        self.worker_status_manager = WorkerStatusManager(params.get("worker_id", "default_id"))
        self.created_at = params.get("created_at")
        data = params.get("data", {})
        self.user_id = data.get("user_id")

        self.video_generator = GeneratorFactory.create_video_generator()

    async def run(self) -> None:
        """
        Запуск пайплайна конкатенации анимаций.
        """
        if not await self.worker_status_manager.check_worker_animations_ready_flag(self.worker_status_manager.worker_id):
            logger.error("No animations ready for concatenation.")
            await send_notification(
                f"Нет готовых анимаций для конкатенации.",
                str(self.user_id)
            )
            raise ValueError("No animations ready for concatenation.")

        video_path = await self.video_generator.generate([])
        video_server_path = await upload_file_to_backend(video_path, is_video=True)
        video_server_path = f"{BACKEND_ROUTE}/worker/download-video/{video_server_path}"

        await send_notification(
            f"Ссылка на видео: {video_server_path}",
            str(self.user_id)
        )