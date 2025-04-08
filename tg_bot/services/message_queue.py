import asyncio
from collections import defaultdict, deque
from functools import wraps

from aiogram import Bot


class MessageQueue:
    _instance = None

    def __new__(cls, bot: Bot | None = None, rate_limit: int = 20, time_window: int = 60):
        """
        Создаёт или возвращает единственный экземпляр класса.
        """
        if cls._instance is None:
            if bot is None:
                raise ValueError("Bot instance must be provided on the first initialization.")
            cls._instance = super(MessageQueue, cls).__new__(cls)
            cls._instance._init(bot, rate_limit, time_window)
        elif bot is not None and bot != cls._instance._bot:
            raise RuntimeError("MessageQueue is already initialized with a different Bot instance.")
        return cls._instance

    def _init(self, bot: Bot, rate_limit: int, time_window: int):
        """
        Инициализация очереди сообщений.
        """
        self._bot = bot  # Теперь это экземплярный атрибут
        self._rate_limit = rate_limit
        self._time_window = time_window
        self._message_queues = defaultdict(deque)

    async def process_message_queue(self, chat_id: int):
        """
        Обрабатывает очередь сообщений для конкретного чата.
        """
        while self._message_queues[chat_id]:
            # Проверяем, инициализирован ли бот
            if self._bot is None:
                raise RuntimeError("Bot instance is not initialized in MessageQueue.")

            # Извлекаем сообщение из очереди
            message_data = self._message_queues[chat_id].popleft()

            # Пытаемся отправить сообщение
            try:
                await self._bot.edit_message_text(**message_data)
            except Exception as e:
                print(f"Ошибка при отправке сообщения: {e}")

            # Задержка между сообщениями
            await asyncio.sleep(self._time_window / self._rate_limit)

    async def add_message_to_queue(self, chat_id: int, message_data: dict):
        """
        Добавляет сообщение в очередь для определённого чата.
        """
        # Добавляем данные сообщения в очередь
        self._message_queues[chat_id].append(message_data)

        # Если очередь только что пополнилась, запускаем обработчик
        if len(self._message_queues[chat_id]) == 1:
            asyncio.create_task(self.process_message_queue(chat_id))

    @staticmethod
    def rate_limit():
        """
        Статический декоратор для управления частотой отправки сообщений.
        """

        def decorator(handler):
            @wraps(handler)
            async def wrapper(callback, *args, **kwargs):
                instance = MessageQueue._instance
                if instance is None or instance._bot is None:
                    raise RuntimeError("MessageQueue is not initialized. Bot instance is missing.")

                # Извлекаем chat_id
                chat_id = callback.message.chat.id

                # Выполняем обработчик
                result = await handler(callback, *args, **kwargs)

                # Если результат содержит данные для очереди, добавляем их
                if isinstance(result, dict):
                    await instance.add_message_to_queue(chat_id, result)

                return result

            return wrapper

        return decorator
