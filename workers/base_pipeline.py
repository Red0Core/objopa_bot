import abc

class BasePipeline(abc.ABC):
    def __init__(self, task_id: str, **params) -> None:
        pass

    @abc.abstractmethod
    async def run(self) -> None:
        pass
