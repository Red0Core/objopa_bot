from datetime import datetime
from typing import Any
from pydantic import BaseModel

class BaseWorkerTask(BaseModel):
    task_id: str
    created_at: datetime
    type: str
    data: Any

class ImageGenerationTaskData(BaseModel):
    prompts: list[str]
    user_id: int

class ImageSelectionTaskData(BaseModel):
    relative_paths: list[str]
    user_id: int

class FileUploadResponse(BaseModel):
    filename: str
    filepath: str
    size: int
    content_type: str
    status: str = "uploaded"
