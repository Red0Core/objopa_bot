from datetime import datetime
from typing import Any
from pydantic import BaseModel, HttpUrl

class BaseWorkerTask(BaseModel):
    task_id: str
    created_at: datetime
    type: str
    data: Any

class ImageGenerationTaskData(BaseModel):
    prompts: list[str]
    user_id: int

class ImageSelectionTaskData(BaseModel):
    images: list[HttpUrl]
    user_id: int