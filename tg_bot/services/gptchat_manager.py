from .gpt import AIChatInterface
from datetime import datetime, timedelta
from logger import logger

class ChatSessionManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ChatSessionManager, cls).__new__(cls)
            cls._instance.sessions = {}  # Хранилище чатов
        return cls._instance

    def create_chat(self, chat_id, chat_model: AIChatInterface):
        self.sessions[chat_id] = {
            "chat_model": chat_model,
            "last_active": datetime.now()
        }
        logger.info(f"Создал новую сессию для {chat_id}")

    def remove_chat(self, chat_id):
        if chat_id in self.sessions:
            del self.sessions[chat_id]
            logger.info(f"Сессия ГПТ {chat_id} удалена.")
        else:
            logger.warning(f"Попытка доступа к несуществующей сессии: {chat_id}")

    def get_chat(self, chat_id) -> AIChatInterface | None:
        if chat_id in self.sessions:
            last_active = self.sessions[chat_id]["last_active"]
            if datetime.now() - last_active > timedelta(seconds=3600): # Час бездействия = удаление
                self.remove_chat(chat_id)
                logger.info("Прошлая сессия устарела...")
                return None
            self.sessions[chat_id]["last_active"] = datetime.now()
            logger.info(f"Использую активную сессию в {chat_id}")
            return self.sessions[chat_id]["chat_model"]
        else:
            logger.info("Сессии нет!!!")
        return None

def get_chat_manager() -> ChatSessionManager:
    return ChatSessionManager()
