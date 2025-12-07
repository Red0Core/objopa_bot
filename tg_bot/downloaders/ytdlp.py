import re
import traceback
from pathlib import Path

import telegramify_markdown
from yt_dlp import YoutubeDL

from core.config import DOWNLOADS_DIR
from core.logger import logger
from tg_bot.utils.cookies_manager import cookies_manager

MAX_SIZE_MB = 50
MAX_SIZE_BYTES = MAX_SIZE_MB * 1024 * 1024
IP_PATTERN = re.compile(r"https?://[^/]*\b(?:\d{1,3}\.){3}\d{1,3}\b")


def has_ip_in_url(url: str) -> bool:
    return bool(IP_PATTERN.search(url))


async def download_with_ytdlp(
    url: str,
    download_path: Path = DOWNLOADS_DIR,
    use_cookies: bool = False,
) -> tuple[list[Path], str | None, str | None]:
    """
    Download media using yt-dlp and return downloaded file paths, title and error.

    Args:
        url: Media URL
        download_path: Where to download
        use_cookies: Try to use cookies first if available
    """
    ydl_opts = {
        "outtmpl": str(download_path / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "extractor_args": {"generic": ["impersonate=chrome"]},
    }

    files: list[Path] = []
    title: str | None = None
    error: str | None = None

    async def _download_attempt(with_cookies: bool = False) -> bool:
        """Попытка скачать с указанными опциями. Возвращает True если успешно."""
        nonlocal files, title, error, ydl_opts

        current_opts = dict(ydl_opts)
        cookies_path = None

        if with_cookies:
            site_name = cookies_manager.get_site_name(url)
            cookies_path = await cookies_manager.get_cookies(site_name)
            if cookies_path:
                current_opts["cookiefile"] = str(cookies_path)
                logger.info(f"Using cookies for {site_name}")
            else:
                logger.info(f"No cookies available for {site_name}, using default opts")
                return False

        try:
            with YoutubeDL(current_opts) as ydl:
                info: dict | None = ydl.extract_info(url, download=False)
                if not info:
                    error = "❌ Failed to extract video information."
                    return False

                duration = info.get("duration") or 0
                formats = info.get("formats", [])

                def estimate_size(fmt: dict) -> int:
                    if "filesize" in fmt and fmt["filesize"]:
                        return fmt["filesize"]
                    if "filesize_approx" in fmt and fmt["filesize_approx"]:
                        return fmt["filesize_approx"]
                    tbr = fmt.get("tbr")
                    if tbr is not None and duration and duration > 0:
                        return int((tbr * 1000 / 8) * duration)
                    return 0

                # Check if this is a YouTube Shorts or similar with separate video/audio streams
                video_formats = [
                    fmt
                    for fmt in formats
                    if fmt.get("vcodec") != "none" and fmt.get("height") and not has_ip_in_url(fmt.get("url", ""))
                ]

                # Sort by quality
                video_formats.sort(
                    key=lambda f: (f.get("height", 0), f.get("tbr") if f.get("tbr") is not None else 0), reverse=True
                )

                for fmt in video_formats:
                    size = estimate_size(fmt)
                    logger.info(f"Format: {fmt}, Estimated size: {size} bytes")
                    if 0 < size <= MAX_SIZE_BYTES and fmt.get("height", 0) >= 480:
                        # Use format selectors that work well with YouTube Shorts
                        format_selector = (
                            f"{fmt['format_id']}+bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio[ext=aac]/bestaudio"
                        )
                        ydl.params["format"] = format_selector
                        # Set merge output format to ensure we get a single file
                        ydl.params["merge_output_format"] = "mp4"
                        # Ensure we prefer ffmpeg for merging
                        ydl.params["postprocessor_args"] = {"ffmpeg": ["-movflags", "faststart"]}

                        downloaded: dict | None = ydl.extract_info(url, download=True)
                        if not downloaded:
                            error = "❌ Failed to download the video."
                            return False
                        title = downloaded.get("title")
                        entries = downloaded["entries"] if "entries" in downloaded else [downloaded]
                        for entry in entries:
                            files.append(Path(ydl.prepare_filename(entry)))
                        return True

                # Если ни один формат не прошел по весу
                title = info.get("title", "Video")
                lines = [f"*❌ File too large to download\\. Available formats:*\n{title}\n"]

                # То мы собираем информацию о всех форматах и выбираем лучший по битрейту на каждом разрешении по высоте
                best_bitrate_on_resolutions = dict()
                for f in video_formats:
                    size_est = estimate_size(f)
                    height = int(f.get("height", 0))
                    best_bitrate_on_resolutions[height] = max(best_bitrate_on_resolutions.get(height, 0), size_est)

                # Формируем список с информацией о лучших форматах
                for f in video_formats:
                    size_est = estimate_size(f)
                    height = int(f.get("height", 0))
                    if size_est != best_bitrate_on_resolutions.get(height, 0):
                        continue
                    format_id = f.get("format_id", "")
                    resolution = f.get("resolution", str(height) + "p")
                    url_fmt = f.get("url", "")
                    size_mb = round(size_est / (1024 * 1024), 2) if size_est else "?"
                    lines.append(f"{format_id} - {resolution} (~{size_mb} MB): [Link]({url_fmt})")

                error = telegramify_markdown.markdownify("\n".join(lines))
                return False

        except Exception as e:
            error = str(e)

            # Проверяем, нужны ли cookies
            if with_cookies:
                # Уже пробовали с cookies, возвращаем ошибку
                logger.exception(f"Download with cookies failed: {e}")
                return False

            # Если ошибка содержит намёк на cookies
            if cookies_manager.has_cookies_error(error):
                site_name = cookies_manager.get_site_name(url)
                logger.warning(f"Cookies required for {site_name}, marking as expired")
                await cookies_manager.mark_cookies_expired(site_name)
                return False

            logger.error(f"yt-dlp download error: {traceback.format_exc()}")
            return False
        finally:
            # Очищаем временный файл cookies
            if cookies_path and cookies_path.exists():
                try:
                    cookies_path.unlink()
                except Exception:
                    pass

    # Пробуем со стратегией
    if use_cookies:
        # Сначала с cookies, потом без
        if await _download_attempt(with_cookies=True):
            title = f"{title}cookies_used" if title else "Media with cookies"
            return files, title, error

    # Потом без cookies
    if await _download_attempt(with_cookies=False):
        return files, title, error

    return files, title, error
