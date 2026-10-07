"""Bounded GPT chat sessions so conversation objects cannot grow without limit."""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, Optional, cast

from core.logger import logger
from core.memory import GPT_SESSION_TIMEOUT_SEC, MAX_GPT_SESSIONS, trim_memory

from .gpt import AIChatInterface


class ChatSessionManager:
    _instance = None
    _sessions: Dict[int, Dict[str, AIChatInterface | datetime]] = {}
    _last_activity: Dict[int, datetime] = {}
    _lock: asyncio.Lock = asyncio.Lock()

    CLEANUP_INTERVAL = 60 * 5
    SESSION_TIMEOUT = timedelta(seconds=GPT_SESSION_TIMEOUT_SEC)

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ChatSessionManager, cls).__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        self._sessions: Dict[int, Dict[str, AIChatInterface | datetime]] = {}
        self._last_activity: Dict[int, datetime] = {}
        if not hasattr(self, "_cleanup_task") or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_sessions_loop())
            logger.info("Chat session cleanup task started.")

    @staticmethod
    def _close_model(chat_model: object) -> None:
        closer = getattr(chat_model, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                logger.debug("Failed to close GPT chat model", exc_info=True)

    async def create_chat(self, chat_id: int, chat_model: AIChatInterface):
        async with self._lock:
            await self._evict_if_needed_unlocked(keep=chat_id)
            self._sessions[chat_id] = {"chat_model": chat_model, "last_active": datetime.now()}
            self._last_activity[chat_id] = datetime.now()
        logger.info(f"Создал новую сессию для {chat_id} (active={len(self._sessions)})")

    async def remove_chat(self, chat_id: int):
        async with self._lock:
            session_data = self._sessions.pop(chat_id, None)
            self._last_activity.pop(chat_id, None)
        if session_data:
            self._close_model(session_data.get("chat_model"))
            logger.info(f"Сессия ГПТ {chat_id} удалена.")
            trim_memory()
        else:
            logger.warning(f"Попытка удаления несуществующей сессии: {chat_id}")

    async def get_chat(self, chat_id: int) -> Optional[AIChatInterface]:
        async with self._lock:
            session_data = self._sessions.get(chat_id)
            if session_data:
                chat_model = session_data.get("chat_model")
                if not chat_model:
                    logger.warning(f"Сессия {chat_id} не содержит chat_model.")
                    return None
                self._last_activity[chat_id] = datetime.now()
                logger.debug(f"Использую активную сессию в {chat_id}")
                return cast(AIChatInterface, chat_model)
            logger.debug(f"Сессии {chat_id} нет.")
        return None

    async def _evict_if_needed_unlocked(self, keep: int | None = None) -> None:
        while len(self._sessions) >= MAX_GPT_SESSIONS:
            candidates = [cid for cid in self._last_activity if cid != keep]
            if not candidates:
                break
            oldest = min(candidates, key=lambda cid: self._last_activity.get(cid, datetime.min))
            session_data = self._sessions.pop(oldest, None)
            self._last_activity.pop(oldest, None)
            if session_data:
                self._close_model(session_data.get("chat_model"))
            logger.info(f"Evicted GPT session {oldest} (cap {MAX_GPT_SESSIONS})")

    async def _cleanup_sessions_loop(self):
        while True:
            await asyncio.sleep(self.CLEANUP_INTERVAL)
            logger.info("Запущена фоновая задача очистки сессий.")
            now = datetime.now()
            chats_to_delete = []

            async with self._lock:
                for chat_id, last_active in self._last_activity.items():
                    if now - last_active > self.SESSION_TIMEOUT:
                        chats_to_delete.append(chat_id)

                for chat_id in chats_to_delete:
                    session_data = self._sessions.pop(chat_id, None)
                    self._last_activity.pop(chat_id, None)
                    if session_data:
                        self._close_model(session_data.get("chat_model"))
                    logger.info(f"Очищена устаревшая сессия для chat_id: {chat_id}")

            if chats_to_delete:
                trim_memory()
            logger.info(f"Завершение очистки сессий. Удалено {len(chats_to_delete)} неактивных сессий.")

    def stop_cleanup_task(self):
        if hasattr(self, "_cleanup_task") and self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            logger.info("Задача очистки чат-сессий отменена.")


def get_chat_manager() -> ChatSessionManager:
    return ChatSessionManager()
