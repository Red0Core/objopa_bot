import asyncio
import re
from pathlib import Path

import instaloader
from curl_cffi.requests import AsyncSession

from core.config import DOWNLOADS_DIR, INSTAGRAM_PASSWORD, INSTAGRAM_USERNAME, STORAGE_DIR
from core.logger import logger
from tg_bot.services.instagram_ua_service import instagram_ua_service

INSTAGRAM_REGEX = re.compile(r"(https?:\/\/(?:www\.)?instagram\.com\/(?:share\/)?(p|reel|tv|stories)\/[\w\-]+)")


async def get_instagram_shortcode(url: str) -> str | None:
    """
    Определяет shortcode поста Instagram (редиректим сразу).
    """
    # Импортируем сервис внутри функции для избежания циклических импортов
    try:
        user_agent = await instagram_ua_service.get_user_agent()
        logger.debug(f"Using User-Agent: {user_agent[:50]}...")
    except Exception as e:
        logger.warning(f"Failed to get dynamic User-Agent: {e}, using fallback")
        user_agent = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Mobile Safari/537.36"

    async with AsyncSession() as session:
        try:
            headers = {
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Accept-Encoding": "gzip, deflate, br",
                "DNT": "1",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            }
            match = re.search(r"/(p|reel|tv)/([\w-]+)", url)
            if not match:
                logger.info(f"Current {url} doesn't have shortcode, redirect")
                response = await session.get(
                    url, allow_redirects=True, impersonate="chrome", headers=headers, timeout=10
                )  # type: ignore

                final_url = str(response.url)  # Итоговый URL
                match = re.search(r"/(p|reel|tv)/([\w-]+)", final_url)
            if match:
                shortcode = match.group(2)
                logger.info(f"Extracted shortcode: {shortcode}")
                return shortcode

        except Exception as e:
            logger.error(f"Ошибка при редиректе Instagram URL: {e}")
            return None

    return None  # Shortcode не найден


async def init_instaloader():
    """Инициализация Instaloader с динамическим User-Agent"""
    try:
        user_agent = await instagram_ua_service.get_user_agent()
        logger.info(f"Initializing Instaloader with UA: {user_agent[:50]}...")
    except Exception as e:
        logger.warning(f"Failed to get dynamic User-Agent for Instaloader: {e}, using fallback")
        user_agent = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Mobile Safari/537.36"

    bot_loader = instaloader.Instaloader(
        filename_pattern="{shortcode}",
        iphone_support=False,
        user_agent=user_agent,
        quiet=True,  # Убираем лишние выводы
        download_pictures=True,
        download_videos=True,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        fatal_status_codes=[302, 400, 401, 429, 403],
    )

    try:
        if INSTAGRAM_USERNAME:
            session_loaded = False
            try:
                bot_loader.load_session_from_file(
                    INSTAGRAM_USERNAME,
                    str((STORAGE_DIR / "session" / f"session-{INSTAGRAM_USERNAME}").absolute()),
                )
                if bot_loader.test_login():
                    logger.success(f"Instaloader session loaded for user: {INSTAGRAM_USERNAME}")
                    session_loaded = True
                else:
                    logger.warning("⚠️ Session from file is invalid.")
            except FileNotFoundError:
                logger.warning("⚠️ Session file not found. Attempting login with password.")
            except KeyError:
                logger.warning("⚠️ Session file is corrupted or missing required data. Attempting login with password.")

            if not session_loaded and INSTAGRAM_PASSWORD:
                logger.info(f"Attempting to log in as {INSTAGRAM_USERNAME}...")
                bot_loader.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)
                if bot_loader.test_login():
                    logger.success(f"Successfully logged in as {INSTAGRAM_USERNAME}")
                    # Сохраняем сессию для будущих запусков
                    bot_loader.save_session_to_file(
                        str((STORAGE_DIR / "session" / f"session-{INSTAGRAM_USERNAME}").absolute())
                    )
                else:
                    logger.error("❌ Login failed with username/password. Check credentials.")
            elif not session_loaded:
                logger.warning("No password provided. Only public posts can be downloaded.")
        else:
            logger.info("No Instagram username provided. Only public posts can be downloaded.")

    except instaloader.exceptions.QueryReturnedBadRequestException as e:
        if "checkpoint" in str(e):
            logger.error("Инста забанила акк, надо разблочиться")
    except Exception as e:
        logger.exception(f"An unexpected error occurred during Instaloader session setup: {e}")
    return bot_loader


# Кэш для Instaloader инстанса
_instaloader_session = None


async def get_instaloader_session():
    """Получает или создает инстанс Instaloader."""
    global _instaloader_session
    if _instaloader_session is None:
        _instaloader_session = await init_instaloader()
    return _instaloader_session


async def reset_instaloader_session():
    """Сбрасывает кэш Instaloader для пересоздания с новым User-Agent."""
    global _instaloader_session
    _instaloader_session = None
    logger.info("Instagram loader cache reset")


async def download_instagram_media(url: str) -> tuple[str | None, str | None]:
    """Асинхронно загружает посты Instagram (фото, видео, текст)
    и возвращает shortcode и ошибку, если есть."""
    try:
        shortcode: str | None = await get_instagram_shortcode(url)
        if not shortcode:
            return None, "❌ Ошибка: Не удалось извлечь shortcode из ссылки."

        bot_loader = await get_instaloader_session()

        post = await asyncio.to_thread(instaloader.Post.from_shortcode, bot_loader.context, shortcode)
        await asyncio.to_thread(bot_loader.download_post, post, DOWNLOADS_DIR)

        return shortcode, None

    except instaloader.exceptions.LoginRequiredException:
        return None, "❌ Ошибка: Требуется авторизация в Instagram для этого контента."

    except (instaloader.exceptions.BadResponseException, instaloader.exceptions.ConnectionException):
        # При плохом ответе пытаемся обновить User-Agent и пересоздать сессию
        try:
            # Получаем новый User-Agent (будет автоматически взят из Redis)
            await instagram_ua_service.get_user_agent()
            global _instaloader_session
            _instaloader_session = None  # Сбрасываем кэш для пересоздания с новым UA
            logger.warning("Instagram blocked access, refreshed User-Agent and reset cache...")
        except Exception as e:
            logger.error(f"Failed to refresh User-Agent: {e}")

        return None, "❌ Ошибка: Instagram заблокировал доступ. Попробуйте обновить User-Agent командой /ua_set"

    except instaloader.exceptions.QueryReturnedBadRequestException as e:
        if "checkpoint" in str(e):
            return None, "❌ Ошибка: Instagram забанил акк. Надо разблочить"

    except Exception as e:
        return None, f"❌ Ошибка: {e}"

    return None, f"Хз, ничего не скачалось. {url}"


DOWNLOADS_DIR.mkdir(exist_ok=True)


async def select_instagram_media(shortcode: str, download_path: Path = DOWNLOADS_DIR) -> dict[str, list[Path] | str]:
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
