import re
from datetime import datetime, timedelta
from pathlib import Path

from core.logger import logger
from core.redis_client import get_redis
from core.config import STORAGE_DIR

COOKIES_DIR = STORAGE_DIR / "cookies"
COOKIES_DIR.mkdir(parents=True, exist_ok=True)

# Regex для детекта устаревших cookies в ошибках
COOKIES_ERROR_PATTERNS = [
    r"Use --cookies-from-browser or --cookies for the authentication",
    r"Account authentication is required",
    r"Use --cookies",
    r"login required",
    r"requires login",
]

COOKIES_REGEX = re.compile("|".join(COOKIES_ERROR_PATTERNS), re.IGNORECASE)


class CookiesManager:
    """Управление cookies для yt-dlp по сайтам и пользователям."""

    COOKIES_EXPIRE_DAYS = 30  # Cookies хранятся 30 дней

    @staticmethod
    def get_site_name(url: str) -> str:
        """Извлекает имя сайта из URL: instagram.com, reddit.com и т.д."""
        try:
            from urllib.parse import urlparse

            domain = urlparse(url).netloc
            # Убираем www. и берём основной домен
            return domain.replace("www.", "").split(".")[0].lower()
        except Exception:
            return ""

    @staticmethod
    def extract_site_from_cookies_file(cookies_path: Path) -> str | None:
        """
        Извлекает имя сайта из файла cookies (формат Netscape).
        После комментариев идут строки с доменами типа .instagram.com
        """
        try:
            content = cookies_path.read_text(encoding="utf-8")
            lines = content.strip().split("\n")

            for line in lines:
                # Пропускаем пустые строки и комментарии
                if not line.strip() or line.startswith("#"):
                    continue

                # Парсим строку Netscape cookies
                # Формат: domain  flag  path  secure  expiration  name  value
                parts = line.split("\t")
                if len(parts) >= 1:
                    domain = parts[0]
                    # Извлекаем имя сайта из домена (.instagram.com -> instagram)
                    domain_clean = domain.lstrip(".")
                    site_name = domain_clean.split(".")[0].lower()
                    if site_name:
                        logger.info(f"Extracted site name from cookies: {site_name}")
                        return site_name

            return None
        except Exception as e:
            logger.error(f"Error extracting site from cookies file: {e}")
            return None

    @staticmethod
    async def save_cookies(site_name: str, cookies_path: Path) -> bool:
        """Сохраняет cookies файл в Redis с TTL."""
        try:
            if not cookies_path.exists():
                logger.error(f"Cookies file not found: {cookies_path}")
                return False

            # Читаем файл
            cookies_content = cookies_path.read_text(encoding="utf-8")

            # Сохраняем в Redis с expiry
            key = f"cookies:{site_name.lower()}"
            ttl = int(timedelta(days=CookiesManager.COOKIES_EXPIRE_DAYS).total_seconds())

            redis_client = await get_redis()
            await redis_client.setex(key, ttl, cookies_content)

            # Сохраняем временную метку
            await redis_client.setex(f"{key}:timestamp", ttl, datetime.now().isoformat())

            logger.info(f"Cookies saved for {site_name} (expires in {CookiesManager.COOKIES_EXPIRE_DAYS} days)")
            return True
        except Exception as e:
            logger.error(f"Error saving cookies: {e}")
            return False

    @staticmethod
    async def get_cookies(site_name: str) -> Path | None:
        """Получает cookies для сайта и записывает в временный файл."""
        try:
            key = f"cookies:{site_name.lower()}"
            redis_client = await get_redis()
            cookies_content = await redis_client.get(key)

            if not cookies_content:
                return None

            # Создаём временный файл
            temp_cookies = COOKIES_DIR / f"{site_name.lower()}.txt"
            temp_cookies.write_text(cookies_content, encoding="utf-8")

            return temp_cookies
        except Exception as e:
            logger.error(f"Error getting cookies for {site_name}: {e}")
            return None

    @staticmethod
    async def mark_cookies_expired(site_name: str) -> bool:
        """Помечает cookies как устаревшие (удаляет из Redis)."""
        try:
            key = f"cookies:{site_name.lower()}"
            redis_client = await get_redis()
            await redis_client.delete(key)
            await redis_client.delete(f"{key}:timestamp")
            logger.info(f"Cookies marked as expired for {site_name}")
            return True
        except Exception as e:
            logger.error(f"Error marking cookies as expired: {e}")
            return False

    @staticmethod
    def has_cookies_error(error: str) -> bool:
        """Проверяет, содержит ли ошибка указание на необходимость cookies."""
        return bool(COOKIES_REGEX.search(error))

    @staticmethod
    async def list_available_cookies() -> dict[str, str]:
        """Возвращает список доступных cookies с датами."""
        try:
            result = {}
            redis_client = await get_redis()
            for key in await redis_client.keys("cookies:*"):
                if key.endswith(":timestamp"):
                    continue
                site_name = key.replace("cookies:", "").lower()
                timestamp_key = f"{key}:timestamp"
                timestamp = redis_client.get(timestamp_key)
                result[site_name] = timestamp if timestamp else "unknown"
            return result
        except Exception as e:
            logger.error(f"Error listing cookies: {e}")
            return {}

    @staticmethod
    async def delete_cookies(site_name: str) -> bool:
        """Удаляет cookies для сайта."""
        try:
            key = f"cookies:{site_name.lower()}"
            redis_client = await get_redis()
            await redis_client.delete(key)
            await redis_client.delete(f"{key}:timestamp")
            logger.info(f"Cookies deleted for {site_name}")
            return True
        except Exception as e:
            logger.error(f"Error deleting cookies: {e}")
            return False


# Глобальный экземпляр
cookies_manager = CookiesManager()
