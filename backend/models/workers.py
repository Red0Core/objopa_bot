from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class BaseWorkerTask(BaseModel):
    task_id: str
    created_at: datetime
    type: str
    data: Any

class VideoGenerationPipelineTaskData(BaseModel):
    image_prompts: list[str] = Field(..., min_items=1, example=["cat with glasses", "dog with a cape"]) # type: ignore
    animation_prompts: list[str]  = Field(..., min_items=1, example=["cat eat fish", "dog eat sousage"]) # type: ignore
    user_id: int = Field(..., example=123456789) # type: ignore

class ImagePromptItem(BaseModel):
    prompt: str
    characters: dict[str, str] = Field(default_factory=dict)

class AnimationPromptItem(BaseModel):
    prompt: str

class ScenarioInput(BaseModel):
    characters: dict[str, str] = Field(default_factory=dict)
    images: list[ImagePromptItem] = Field(..., min_items=1) # type: ignore
    animations: list[AnimationPromptItem] = Field(..., min_items=1) # type: ignore
    user_id: int = Field(..., example=123456789) # type: ignore
    # Добавьте другие поля, если они нужны, например, task_type или настройки генерации

class ImageSelectionTaskData(BaseModel):
    relative_paths: list[str]
    user_id: int

class FileUploadResponse(BaseModel):
    filename: str
    filepath: str
    size: int
    content_type: str
    status: str = "uploaded"
