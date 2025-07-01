import asyncio
import re
import traceback
from pathlib import Path

import telegramify_markdown
from yt_dlp import YoutubeDL

from core.config import DOWNLOADS_DIR
from core.logger import logger

MAX_SIZE_MB = 50
MAX_SIZE_BYTES = MAX_SIZE_MB * 1024 * 1024
IP_PATTERN = re.compile(r"https?://[^/]*\b(?:\d{1,3}\.){3}\d{1,3}\b")


def has_ip_in_url(url: str) -> bool:
    return bool(IP_PATTERN.search(url))


async def download_with_ytdlp(
    url: str, download_path: Path = DOWNLOADS_DIR
) -> tuple[list[Path], str | None, str | None]:
    """Download media using yt-dlp and return downloaded file paths, title and error."""
    ydl_opts = {
        "outtmpl": str(download_path / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "extractor_args": {"generic": ["impersonate=chrome"]},
    }

    files: list[Path] = []
    title: str | None = None
    error: str | None = None

    def _download() -> None:
        nonlocal files, title, error, url
        with YoutubeDL(ydl_opts) as ydl:
            info: dict | None = ydl.extract_info(url, download=False)
            if not info:
                error = "❌ Failed to extract video information."
                return

            duration = info.get("duration") or 0
            formats = info.get("formats", [])

            def estimate_size(fmt: dict) -> int:
                if "filesize" in fmt and fmt["filesize"]:
                    return fmt["filesize"]
                if "filesize_approx" in fmt and fmt["filesize_approx"]:
                    return fmt["filesize_approx"]
                if "tbr" in fmt and fmt["tbr"] is not None and duration:
                    return int((fmt["tbr"] * 1000 / 8) * duration)
                return 0

            # Check if this is a YouTube Shorts or similar with separate video/audio streams
            video_formats = [
                fmt for fmt in formats 
                if fmt.get("vcodec") != "none" and fmt.get("height") and not has_ip_in_url(fmt.get("url", ""))
            ]
            
            # Sort by quality
            video_formats.sort(key=lambda f: (f.get("height", 0), f.get("tbr", 0)), reverse=True)
            
            for fmt in video_formats:
                size = estimate_size(fmt)
                logger.info(f"Format: {fmt}, Estimated size: {size} bytes")
                if 0 < size <= MAX_SIZE_BYTES and fmt.get("height", 0) >= 480:
                    # Use format selectors that work well with YouTube Shorts
                    format_selector = f"{fmt['format_id']}+bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio[ext=aac]/bestaudio"
                    ydl.params["format"] = format_selector
                    # Set merge output format to ensure we get a single file
                    ydl.params["merge_output_format"] = "mp4"
                    # Ensure we prefer ffmpeg for merging
                    ydl.params["postprocessor_args"] = {"ffmpeg": ["-movflags", "faststart"]}
                    
                    downloaded: dict | None = ydl.extract_info(url, download=True)
                    if not downloaded:
                        error = "❌ Failed to download the video."
                        return
                    title = downloaded.get("title")
                    entries = downloaded["entries"] if "entries" in downloaded else [downloaded]
                    for entry in entries:
                        files.append(Path(ydl.prepare_filename(entry)))
                    return

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
                url = f.get("url", "")
                size_mb = round(size_est / (1024 * 1024), 2) if size_est else "?"
                lines.append(f"{format_id} - {resolution} (~{size_mb} MB): [Link]({url})")

            error = telegramify_markdown.markdownify("\n".join(lines))

    try:
        await asyncio.to_thread(_download)
    except Exception as e:  # noqa: BLE001
        logger.error(f"yt-dlp download error: {traceback.format_exc()}")
        error = str(e)

    return files, title, error
