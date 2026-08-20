import asyncio
from collections import defaultdict, deque
from functools import wraps

from aiogram import Bot


class MessageQueue:
    _instance = None

    def __new__(cls, bot: Bot | None = None, rate_limit: int = 20, time_window: int = 60):
        if cls._instance is None:
            if bot is None:
                raise ValueError("Bot instance must be provided on the first initialization.")
            cls._instance = super(MessageQueue, cls).__new__(cls)
            cls._instance._init(bot, rate_limit, time_window)
        elif bot is not None and bot != cls._instance._bot:
            raise RuntimeError("MessageQueue is already initialized with a different Bot instance.")
        return cls._instance

    def _init(self, bot: Bot, rate_limit: int, time_window: int):
        self._bot = bot
        self._rate_limit = rate_limit
        self._time_window = time_window
        self._message_queues: dict[int, deque] = defaultdict(deque)
        self._max_queue = 32

    async def process_message_queue(self, chat_id: int):
        while self._message_queues[chat_id]:
            if self._bot is None:
                raise RuntimeError("Bot instance is not initialized in MessageQueue.")

            message_data = self._message_queues[chat_id].popleft()

            try:
                await self._bot.edit_message_text(**message_data)
            except Exception as e:
                print(f"Ошибка при отправке сообщения: {e}")

            await asyncio.sleep(self._time_window / self._rate_limit)

        self._message_queues.pop(chat_id, None)

    async def add_message_to_queue(self, chat_id: int, message_data: dict):
        queue = self._message_queues[chat_id]
        if len(queue) >= self._max_queue:
            queue.popleft()
        start_worker = len(queue) == 0
        queue.append(message_data)

        if start_worker:
            asyncio.create_task(self.process_message_queue(chat_id))

    @staticmethod
    def rate_limit():
        def decorator(handler):
            @wraps(handler)
            async def wrapper(callback, *args, **kwargs):
                instance = MessageQueue._instance
                if instance is None or instance._bot is None:
                    raise RuntimeError("MessageQueue is not initialized. Bot instance is missing.")

                chat_id = callback.message.chat.id
                result = await handler(callback, *args, **kwargs)

                if isinstance(result, dict):
                    await instance.add_message_to_queue(chat_id, result)

                return result

            return wrapper

        return decorator
