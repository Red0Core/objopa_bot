import abc
from typing import Any
from datetime import datetime

class BasePipeline(abc.ABC):
    def __init__(self, task_id: str, **params) -> None:
        pass

    @abc.abstractmethod
    async def run(self) -> None:
        pass
