import asyncio
from datetime import datetime, timedelta
from typing import Dict, Optional, cast

from requests import session

from core.logger import logger
from .gpt import AIChatInterface


class ChatSessionManager:
    _instance = None
    _sessions: Dict[int, Dict[str, AIChatInterface | datetime]] = {}  # Более точный тип для словаря
    _last_activity: Dict[int, datetime] = {}
    _lock: asyncio.Lock = asyncio.Lock()  # Добавляем блокировку для защиты общего состояния

    CLEANUP_INTERVAL = 60 * 15  # Интервал проверки старых сессий: 15 минут
    SESSION_TIMEOUT = timedelta(seconds=3600)  # Время жизни сессии без активности: 1 час (3600 секунд)

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ChatSessionManager, cls).__new__(cls)
            cls._instance._init()  # Вызываем инициализатор
        return cls._instance

    def _init(self):
        """Инициализация менеджера сессий, запускающая фоновую задачу очистки."""
        # Эти атрибуты инициализируются только один раз для синглтона
        self._sessions: Dict[int, Dict[str, AIChatInterface | datetime]] = {}
        self._last_activity: Dict[int, datetime] = {}
        # Запускаем фоновую задачу для очистки старых сессий
        # Убедитесь, что это запускается только один раз
        if not hasattr(self, "_cleanup_task") or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_sessions_loop())
            logger.info("Chat session cleanup task started.")

    async def create_chat(self, chat_id: int, chat_model: AIChatInterface):
        """Создает новую чат-сессию для пользователя."""
        async with self._lock:  # Защищаем доступ к словарям
            self._sessions[chat_id] = {"chat_model": chat_model, "last_active": datetime.now()}
            self._last_activity[chat_id] = datetime.now()  # Дублируем для удобства очистки
        logger.info(f"Создал новую сессию для {chat_id}")

    async def remove_chat(self, chat_id: int):
        """Удаляет чат-сессию."""
        async with self._lock:  # Защищаем доступ к словарям
            if chat_id in self._sessions:
                del self._sessions[chat_id]
                if chat_id in self._last_activity:
                    del self._last_activity[chat_id]
                logger.info(f"Сессия ГПТ {chat_id} удалена.")
            else:
                logger.warning(f"Попытка удаления несуществующей сессии: {chat_id}")

    async def get_chat(self, chat_id: int) -> Optional[AIChatInterface]:
        """Возвращает чат-модель для сессии, обновляя время активности."""
        async with self._lock:  # Защищаем доступ к словарям
            session_data = self._sessions.get(chat_id)
            if session_data:
                chat_model = session_data.get("chat_model")
                # В `get_chat` мы только обновляем активность, но не удаляем
                # Удаление происходит в фоновой задаче `_cleanup_sessions_loop`
                if not chat_model:
                    logger.warning(f"Сессия {chat_id} не содержит chat_model.")
                    return None
                self._last_activity[chat_id] = datetime.now()  # Обновляем активность
                logger.debug(f"Использую активную сессию в {chat_id}")  # Изменено на debug, чтобы не засорять логи
                return cast(AIChatInterface, chat_model)
            else:
                logger.debug(f"Сессии {chat_id} нет.")  # Изменено на debug
        return None

    async def _cleanup_sessions_loop(self):
        """Фоновая задача для периодической очистки устаревших сессий."""
        while True:
            await asyncio.sleep(self.CLEANUP_INTERVAL)
            logger.info("Запущена фоновая задача очистки сессий.")
            now = datetime.now()
            chats_to_delete = []

            async with self._lock:  # Защищаем словари во время итерации и удаления
                # Собираем ID сессий для удаления
                for chat_id, last_active in self._last_activity.items():
                    if now - last_active > self.SESSION_TIMEOUT:
                        chats_to_delete.append(chat_id)

                # Удаляем сессии
                for chat_id in chats_to_delete:
                    if chat_id in self._sessions:  # Двойная проверка на всякий случай
                        del self._sessions[chat_id]
                    if chat_id in self._last_activity:
                        del self._last_activity[chat_id]
                    logger.info(f"Очищена устаревшая сессия для chat_id: {chat_id}")
            logger.info(f"Завершение очистки сессий. Удалено {len(chats_to_delete)} неактивных сессий.")

    def stop_cleanup_task(self):
        """Останавливает фоновую задачу очистки сессий."""
        if hasattr(self, "_cleanup_task") and self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            logger.info("Задача очистки чат-сессий отменена.")


# --- Утилита для получения менеджера (не обязательна, но сохранена для совместимости) ---
# Теперь эта функция просто возвращает синглтон-экземпляр ChatSessionManager
def get_chat_manager() -> ChatSessionManager:
    return ChatSessionManager()
