from core.config import BACKEND_ROUTE
from core.logger import logger
from workers.animation_generation_pipeline import AnimationGenerationPipeline
from workers.base_pipeline import BasePipeline
from workers.concat_generation_pipeline import ConcatAnimationsPipeline
from workers.image_generation_pipeline import ImageGenerationPipeline
from workers.reset_worker_state_pipeline import ResetWorkerStatePipeline


class VideoGenerationPipeline(BasePipeline):
    def __init__(self, task_id: str, **params):
        self.task_id = task_id
        self.created_at = params.get("created_at")
        self.worker_id = params.get("worker_id", "default_id")
        data = params.get("data", {})
        self.image_prompts = data.get("image_prompts", [])
        self.animation_prompts = data.get("animation_prompts", [])
        self.user_id = data.get("user_id")

    async def run(self) -> None:
        # Пример обработки задачи генерации изображений
        logger.info(f"VideoGenerationPipeline: {self.task_id}; User ID: {self.user_id}; Image Prompts: {self.image_prompts}; Animations Prompts: {self.animation_prompts}")

        await ImageGenerationPipeline(
            task_id=self.task_id,
            worker_id=self.worker_id,
            created_at=self.created_at,
            data={
                "image_prompts": self.image_prompts,
                "user_id": self.user_id
            }
        ).run()
        logger.success(f"Image generation completed for task {self.task_id}")
        
        await AnimationGenerationPipeline(
            task_id=self.task_id,
            worker_id=self.worker_id,
            created_at=self.created_at,
            data={
                "animation_prompts": self.animation_prompts,
                "user_id": self.user_id
            }
        ).run()

        logger.success(f"Animation generation completed for task {self.task_id}")

        await ConcatAnimationsPipeline(
            task_id=self.task_id,
            worker_id=self.worker_id,
            created_at=self.created_at,
            data={
                "user_id": self.user_id,
                "animation_prompts": self.animation_prompts
            }
        ).run()

        logger.success(f"Concatenation of animations completed for task {self.task_id}")

        await ResetWorkerStatePipeline(
            task_id=self.task_id,
            worker_id=self.worker_id,
            created_at=self.created_at,
            data={
                "user_id": self.user_id
            }
        ).run()
        logger.success("Resetting worker state completed")
        logger.success(f"Video generation completed for task {self.task_id}")
