import asyncio
import traceback
from pathlib import Path
from typing import List, Tuple

from yt_dlp import YoutubeDL

from core.config import DOWNLOADS_PATH
from core.logger import logger

MAX_SIZE_MB = 50
MAX_SIZE_BYTES = MAX_SIZE_MB * 1024 * 1024


async def download_with_ytdlp(
    url: str, download_path: Path = DOWNLOADS_PATH
) -> Tuple[List[Path], str | None, str | None]:
    """Download media using yt-dlp and return downloaded file paths, title and error."""
    ydl_opts = {
        "outtmpl": str(download_path / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "extractor_args": {"generic": ["impersonate=chrome"]},
    }

    files: List[Path] = []
    title: str | None = None
    error: str | None = None

    def _download() -> None:
        nonlocal files, title, error, url
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            logger.info(info)

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

            candidates = [fmt for fmt in formats if fmt.get("ext") == "mp4" and fmt.get("height")]

            # Сортируем по убыванию качества
            candidates.sort(key=lambda f: f.get("height", 0), reverse=True)

            for fmt in candidates:
                size = estimate_size(fmt)
                logger.info(f"Format: {fmt}, Estimated size: {size} bytes")
                if 0 < size <= MAX_SIZE_BYTES and fmt.get("height") >= 720:
                    format_selector = f"bestvideo[height={fmt['height']}][ext=mp4]+bestaudio/best"
                    ydl.params["format"] = format_selector
                    downloaded = ydl.extract_info(url, download=True)
                    title = downloaded.get("title")
                    entries = downloaded["entries"] if "entries" in downloaded else [downloaded]
                    for entry in entries:
                        files.append(Path(ydl.prepare_filename(entry)))
                    return

            # Если ни один формат не прошел по весу
            title = info.get("title", "Video")
            lines = ["*❌ File too large to download\\. Available formats:*\n"]
            for f in candidates:
                format_id = f.get("format_id", "")
                resolution = f.get("resolution", f.get("height", "Unknown"))
                url = f.get("url", "")
                size_est = estimate_size(f)
                size_mb = round(size_est / (1024 * 1024), 2) if size_est else "?"
                safe_url = (
                    url.replace(".", "\\.")
                    .replace("-", "\\-")
                    .replace("?", "\\?")
                    .replace("&", "\\&")
                    .replace("=", "\\=")
                )
                lines.append(f"{format_id} - {resolution} \\(~{size_mb} MB\\): [Link]({safe_url})")

            error = "\n".join(lines)

    try:
        await asyncio.to_thread(_download)
    except Exception as e:  # noqa: BLE001
        logger.error(f"yt-dlp download error: {traceback.format_exc()}")
        error = str(e)

    return files, title, error
