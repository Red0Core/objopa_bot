import abc
from typing import Any

class BaseWorker(abc.ABC):
    def __init__(self, task_id: str, params: dict[str, Any]) -> None:
        self.task_id = task_id
        self.params = params

    @abc.abstractmethod
    async def run(self) -> None:
        pass