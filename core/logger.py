import sys

from loguru import logger

logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    level="INFO",
    enqueue=True,
    backtrace=False,
    diagnose=False,
)

logger.add(
    "logs/bot.log",
    rotation="5 MB",
    retention="5 days",
    compression="zip",
    level="INFO",
    enqueue=True,
    backtrace=False,
    diagnose=False,
)

__all__ = ["logger"]
