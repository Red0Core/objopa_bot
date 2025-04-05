from loguru import logger
import sys

# Настройка логгера
logger.remove()  # Удаляем стандартный вывод loguru
logger.add(
    sys.stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    level="INFO",  # Уровень логирования
    enqueue=True,  # Для многопоточности
)

# Логирование в файл
logger.add(
    "logs/bot.log",
    rotation="10 MB",  # Новый файл каждые 10 MB
    retention="10 days",  # Хранить файлы 10 дней
    compression="zip",  # Сжатие старых логов
    level="INFO",
)

__all__ = ["logger"]
