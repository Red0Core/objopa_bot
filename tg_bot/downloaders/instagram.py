import asyncio
import os
import re
from pathlib import Path

import instaloader
from curl_cffi.requests import AsyncSession

from core.config import DOWNLOADS_PATH
from core.config import STORAGE_PATH as STORAGE_DIR
from core.logger import logger

INSTAGRAM_REGEX = re.compile(
    r"(https?:\/\/(?:www\.)?instagram\.com\/(?:share\/)?(p|reel|tv|stories)\/[\w\-]+)"
)


async def get_instagram_shortcode(url: str) -> str | None:
    """
    Определяет shortcode поста Instagram (редиректим сразу).
    """
    async with AsyncSession() as session:
        try:
            response = await session.get(url, allow_redirects=True, impersonate="chrome")  # type: ignore
            final_url = response.url  # Итоговый URL
            match = re.search(r"/(p|reel|tv)/([\w-]+)", final_url)
            if match:
                return match.group(2)

        except Exception as e:
            print(f"Ошибка при редиректе: {e}")
            return None

    return None  # Shortcode не найден


# Имя пользователя для сессии
INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME")


def init_instaloader():
    """Инициализация Instaloader с авторизацией"""
    user_agent = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Mobile Safari/537.36"
    bot_loader = instaloader.Instaloader(
        filename_pattern="{shortcode}", iphone_support=False, user_agent=user_agent
    )

    try:
        if INSTAGRAM_USERNAME:
            bot_loader.load_session_from_file(
                INSTAGRAM_USERNAME,
                str((STORAGE_DIR / "session" / f"session-{INSTAGRAM_USERNAME}").absolute()),
            )
            logger.success("✅ Успешная авторизация в Instagram.")
        else:
            raise ValueError("Не указаны имя пользователя и пароль Instagram.")
    except FileNotFoundError:
        logger.error("⚠️ Файл сессии не найден. Будут загружены только открытые посты.")

    return bot_loader


# Создаём Instaloader сессию при старте
INSTALOADER_SESSION = init_instaloader()


async def download_instagram_media(url: str) -> tuple[str | None, str | None]:
    """Асинхронно загружает посты Instagram (фото, видео, текст)
    и возвращает shortcode и ошибку, если есть."""
    bot_loader = INSTALOADER_SESSION
    try:
        shortcode = await get_instagram_shortcode(url)
        if not shortcode:
            return None, "❌ Ошибка: Не удалось извлечь shortcode из ссылки."

        post = await asyncio.to_thread(
            instaloader.Post.from_shortcode, bot_loader.context, shortcode
        )
        await asyncio.to_thread(bot_loader.download_post, post, DOWNLOADS_PATH)

        return shortcode, None

    except instaloader.exceptions.LoginRequiredException:
        return None, "❌ Ошибка: Требуется вход в Instagram. Сессия устарела."

    except instaloader.exceptions.ConnectionException:
        return None, "❌ Ошибка: Проблемы с соединением. Проверьте интернет или VPN."

    except instaloader.exceptions.BadResponseException:
        return None, "❌ Ошибка: Instagram заблокировал доступ. Попробуйте позже."

    except Exception as e:
        return None, f"❌ Ошибка: {e}"


DOWNLOADS_PATH.mkdir(exist_ok=True)


async def select_instagram_media(
    shortcode: str, download_path: Path = DOWNLOADS_PATH
) -> dict[str, list[Path] | str]:
    """
    Определяет, какие файлы скачал Instaloader, и выбирает нужный формат для отправки.
    """
    files = [f for f in download_path.iterdir() if f.name.startswith(shortcode)]

    images: list[Path] = []
    videos: list[Path] = []
    caption: str = ""

    for file_path in files:
        suffix = file_path.suffix.lower()

        # Определяем тип файла
        if suffix in (".jpg", ".jpeg", ".png"):
            images.append(file_path)
        elif suffix in (".mp4", ".mov"):
            videos.append(file_path)
        elif suffix == ".txt":
            caption = file_path.read_text(encoding="utf-8")  # Читаем описание поста

    return {"images": images, "videos": videos, "caption": caption}
