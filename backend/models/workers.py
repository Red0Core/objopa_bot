from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field
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

class ImageSelectionTaskData(BaseModel):
    relative_paths: list[str]
    user_id: int

class FileUploadResponse(BaseModel):
    filename: str
    filepath: str
    size: int
    content_type: str
    status: str = "uploaded"
