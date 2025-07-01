"""
Менеджер скачивания медиа с приоритетами и улучшенной обработкой ошибок.
Приоритет: кастомные версии -> yt-dlp -> gallery-dl
"""
import asyncio
from pathlib import Path
from typing import List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

from core.logger import logger
from .instagram import INSTAGRAM_REGEX, download_instagram_media
from .twitter import TWITTER_REGEX, download_twitter_media
from .ytdlp import download_with_ytdlp
from .gallery_dl import download_with_gallery_dl


class DownloaderType(Enum):
    CUSTOM = "custom"
    YTDLP = "yt-dlp"
    GALLERY_DL = "gallery-dl"


@dataclass
class DownloadResult:
    """Результат скачивания медиа."""
    success: bool
    files: List[Path]
    caption: Optional[str] = None
    error: Optional[str] = None
    downloader_used: Optional[DownloaderType] = None


class DownloaderManager:
    """Менеджер для управления различными способами скачивания."""
    
    def __init__(self):
        self.download_attempts = []
    
    async def download_media(self, url: str) -> DownloadResult:
        """
        Скачивает медиа с использованием приоритетной системы.
        
        Порядок попыток:
        1. Кастомные скачиватели (Instagram, Twitter) - строгий режим
        2. yt-dlp (для видео контента) - только если не кастомная платформа
        3. gallery-dl (для изображений) - только если не кастомная платформа
        """
        self.download_attempts = []
        
        # Проверяем, является ли URL кастомной платформой
        is_custom_platform = (INSTAGRAM_REGEX.match(url) or TWITTER_REGEX.match(url))
        
        # Попытка 1: Кастомные скачиватели
        custom_result = await self._try_custom_downloaders(url)
        if custom_result.success:
            return custom_result
        
        # Если это кастомная платформа и она не сработала - не пробуем другие методы
        if is_custom_platform:
            logger.warning(f"Custom platform failed for {url}, not trying other methods")
            return custom_result  # Возвращаем ошибку кастомного скачивателя
        
        # Попытка 2: yt-dlp для видео контента (только для не-кастомных платформ)
        ytdlp_result = await self._try_ytdlp(url)
        if ytdlp_result.success:
            return ytdlp_result
        
        # Попытка 3: gallery-dl для изображений (только для не-кастомных платформ)
        gallery_result = await self._try_gallery_dl(url)
        if gallery_result.success:
            return gallery_result
        
        # Если все попытки неудачны, возвращаем последнюю ошибку
        attempts_summary = "\n".join(self.download_attempts)
        error_message = f"❌ Все попытки скачивания неудачны:\n{attempts_summary}"
        
        return DownloadResult(
            success=False,
            files=[],
            caption=None,
            error=error_message,
            downloader_used=None
        )
    
    async def _try_custom_downloaders(self, url: str) -> DownloadResult:
        """Пробует кастомные скачиватели."""
        try:
            if INSTAGRAM_REGEX.match(url):
                logger.info(f"Attempting Instagram download for: {url}")
                shortcode, error = await download_instagram_media(url)
                
                if shortcode and not error:
                    from core.config import DOWNLOADS_PATH
                    files = sorted([f for f in DOWNLOADS_PATH.iterdir() 
                                  if f.name.startswith(shortcode)])
                    
                    # Извлекаем caption из txt файла
                    caption = None
                    media_files = []
                    for file_path in files:
                        if file_path.suffix.lower() == ".txt":
                            caption = file_path.read_text(encoding="utf-8")
                        else:
                            media_files.append(file_path)
                    
                    self.download_attempts.append(f"Instagram: {'Success' if media_files else 'No media files found'}")
                    
                    if media_files:
                        return DownloadResult(
                            success=True,
                            files=media_files,
                            caption=caption,
                            error=None,
                            downloader_used=DownloaderType.CUSTOM
                        )
                
                self.download_attempts.append(f"Instagram: {error or 'Unknown error'}")
            
            elif TWITTER_REGEX.match(url):
                logger.info(f"Attempting Twitter download for: {url}")
                img_files, vid_files, caption, error = await download_twitter_media(url)
                
                if (img_files or vid_files) and not error:
                    all_files = (img_files or []) + (vid_files or [])
                    self.download_attempts.append("Twitter: Success")
                    
                    return DownloadResult(
                        success=True,
                        files=all_files,
                        caption=caption,
                        error=None,
                        downloader_used=DownloaderType.CUSTOM
                    )
                
                self.download_attempts.append(f"Twitter: {error or 'No media found'}")
        
        except Exception as e:
            logger.error(f"Custom downloader error: {e}")
            self.download_attempts.append(f"Custom: Exception - {str(e)}")
        
        return DownloadResult(
            success=False,
            files=[],
            caption=None,
            error="Custom downloaders failed",
            downloader_used=None
        )
    
    async def _try_ytdlp(self, url: str) -> DownloadResult:
        """Пробует yt-dlp."""
        try:
            logger.info(f"Attempting yt-dlp download for: {url}")
            files, caption, error = await download_with_ytdlp(url)
            
            if files:
                # Фильтруем только видео файлы для yt-dlp приоритета
                video_files = [f for f in files if f.suffix.lower() in ('.mp4', '.mov', '.mkv', '.webm')]
                
                if video_files:
                    self.download_attempts.append("yt-dlp: Success (video)")
                    return DownloadResult(
                        success=True,
                        files=video_files,
                        caption=caption,
                        error=None,
                        downloader_used=DownloaderType.YTDLP
                    )
                else:
                    # Если нет видео файлов, продолжаем с gallery-dl
                    self.download_attempts.append("yt-dlp: No video files found")
            else:
                # Более детальная обработка ошибок yt-dlp
                if error:
                    filtered_error = self._filter_ytdlp_error(error)
                    self.download_attempts.append(f"yt-dlp: {filtered_error}")
                else:
                    self.download_attempts.append("yt-dlp: No files downloaded")
        
        except Exception as e:
            logger.error(f"yt-dlp error: {e}")
            filtered_error = self._filter_ytdlp_error(str(e))
            self.download_attempts.append(f"yt-dlp: Exception - {filtered_error}")
        
        return DownloadResult(
            success=False,
            files=[],
            caption=None,
            error="yt-dlp failed",
            downloader_used=None
        )

    def _filter_ytdlp_error(self, error: str) -> str:
        """Фильтрует и упрощает ошибки yt-dlp для пользователя."""
        error_lower = error.lower()
        
        # Распространенные ошибки и их упрощенные версии
        if "not available" in error_lower or "unavailable" in error_lower:
            return "Видео недоступно"
        elif "private" in error_lower or "permission" in error_lower:
            return "Видео приватное или нет доступа"
        elif "age-restricted" in error_lower or "sign in" in error_lower:
            return "Видео с возрастными ограничениями"
        elif "copyright" in error_lower or "removed" in error_lower:
            return "Видео удалено или нарушение авторских прав"
        elif "geo" in error_lower or "region" in error_lower or "country" in error_lower:
            return "Видео заблокировано в вашем регионе"
        elif "network" in error_lower or "timeout" in error_lower or "connection" in error_lower:
            return "Проблемы с сетью или таймаут"
        elif "too large" in error_lower or "file too large" in error_lower:
            return "Файл слишком большой для скачивания"
        elif "unsupported url" in error_lower or "no video formats" in error_lower:
            return "Неподдерживаемый URL или формат"
        elif "extractor" in error_lower:
            return "Ошибка извлечения данных"
        else:
            # Если ошибка не распознана, возвращаем укороченную версию
            return error[:100] + "..." if len(error) > 100 else error
    
    async def _try_gallery_dl(self, url: str) -> DownloadResult:
        """Пробует gallery-dl."""
        try:
            logger.info(f"Attempting gallery-dl download for: {url}")
            files, caption, error = await download_with_gallery_dl(url)
            
            if files:
                self.download_attempts.append("gallery-dl: Success")
                return DownloadResult(
                    success=True,
                    files=files,
                    caption=caption,
                    error=None,
                    downloader_used=DownloaderType.GALLERY_DL
                )
            else:
                # Более детальная обработка ошибок gallery-dl
                if error:
                    filtered_error = self._filter_gallery_dl_error(error)
                    self.download_attempts.append(f"gallery-dl: {filtered_error}")
                else:
                    self.download_attempts.append("gallery-dl: No files downloaded")
        
        except Exception as e:
            logger.error(f"gallery-dl error: {e}")
            filtered_error = self._filter_gallery_dl_error(str(e))
            self.download_attempts.append(f"gallery-dl: Exception - {filtered_error}")
        
        return DownloadResult(
            success=False,
            files=[],
            caption=None,
            error="gallery-dl failed",
            downloader_used=None
        )

    def _filter_gallery_dl_error(self, error: str) -> str:
        """Фильтрует и упрощает ошибки gallery-dl для пользователя."""
        error_lower = error.lower()
        
        # Распространенные ошибки gallery-dl
        if "403" in error or "forbidden" in error_lower:
            return "Доступ запрещен (403)"
        elif "404" in error or "not found" in error_lower:
            return "Страница не найдена (404)"
        elif "401" in error or "unauthorized" in error_lower:
            return "Требуется авторизация (401)"
        elif "429" in error or "rate limit" in error_lower or "too many requests" in error_lower:
            return "Превышен лимит запросов (429)"
        elif "500" in error or "internal server error" in error_lower:
            return "Ошибка сервера (500)"
        elif "502" in error or "bad gateway" in error_lower:
            return "Плохой шлюз (502)"
        elif "503" in error or "service unavailable" in error_lower:
            return "Сервис недоступен (503)"
        elif "timeout" in error_lower or "timed out" in error_lower:
            return "Превышено время ожидания"
        elif "connection" in error_lower and ("refused" in error_lower or "failed" in error_lower):
            return "Ошибка подключения"
        elif "ssl" in error_lower or "certificate" in error_lower:
            return "Ошибка SSL сертификата"
        elif "unsupported" in error_lower:
            return "Неподдерживаемый сайт или формат"
        elif "no extractor" in error_lower:
            return "Нет подходящего экстрактора для этого сайта"
        elif "private" in error_lower or "protected" in error_lower:
            return "Приватный или защищенный контент"
        else:
            # Если ошибка не распознана, возвращаем укороченную версию
            return error[:100] + "..." if len(error) > 100 else error


# Создаем глобальный экземпляр менеджера
downloader_manager = DownloaderManager()
