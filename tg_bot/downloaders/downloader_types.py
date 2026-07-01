from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Optional


class DownloaderType(Enum):
    CUSTOM = "custom"
    YTDLP = "yt-dlp"
    YTDLP_COOKIES = "yt-dlp_cookies"
    GALLERY_DL = "gallery-dl"
    SPOTIFY = "spotify"
    TWITTER = "twitter"
    INSTAGRAM = "instagram"


@dataclass
class DownloadResult:
    """Результат скачивания медиа."""

    success: bool
    files: List[Path]
    caption: Optional[str] = None
    error: Optional[str] = None
    downloader_used: Optional[DownloaderType] = None
