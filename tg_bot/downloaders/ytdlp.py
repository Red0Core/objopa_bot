import asyncio
from pathlib import Path
from typing import List, Tuple
import traceback

from yt_dlp import YoutubeDL

from core.logger import logger
from core.config import DOWNLOADS_PATH


async def download_with_ytdlp(
    url: str, download_path: Path = DOWNLOADS_PATH
) -> Tuple[List[Path], str | None, str | None]:
    """Download media using yt-dlp and return downloaded file paths, title and error."""
    ydl_opts = {
        "outtmpl": str(download_path / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "impersonate": ""
    }

    files: List[Path] = []
    title: str | None = None
    error: str | None = None

    def _download() -> None:
        nonlocal files, title
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
<<<<<<< HEAD
=======
            logger.info(info)
>>>>>>> 6c585ab (Integration with Twitter Auth, yt-dlp, gallery-dl)
            formats = info.get("formats", [])
            limit = 2 * 1024 * 1024 * 1024

            def select_height(max_h: int) -> str:
                candidates = [
                    f
                    for f in formats
                    if f.get("height")
                    and f.get("ext") == "mp4"
                    and f["height"] <= max_h
                ]
                if not candidates:
                    return "best"
                best = max(candidates, key=lambda f: f.get("height", 0))
                size = best.get("filesize") or best.get("filesize_approx") or 0
                if size and size > limit and max_h > 480:
                    return select_height(720)
                return (
                    f"bestvideo[height<={max_h}][ext=mp4]+bestaudio/best"
                    f"/best[height<={max_h}]"
                )

            ydl.params["format"] = select_height(1080)
            info = ydl.extract_info(url, download=True)
            title = info.get("title")
            entries = info["entries"] if "entries" in info else [info]
            for entry in entries:
                files.append(Path(ydl.prepare_filename(entry)))

    try:
        await asyncio.to_thread(_download)
    except Exception as e:  # noqa: BLE001
<<<<<<< HEAD
        logger.error(f"yt-dlp download error: {e}")
=======
        logger.error(f"yt-dlp download error: {traceback.format_exc()}")
>>>>>>> 6c585ab (Integration with Twitter Auth, yt-dlp, gallery-dl)
        error = str(e)

    return files, title, error
