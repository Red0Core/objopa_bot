from .downloader_manager import DownloaderType, DownloadResult, downloader_manager
from .gallery_dl import download_with_gallery_dl
from .instagram import INSTAGRAM_REGEX, download_instagram_media
from .twitter import (
    TWITTER_REGEX,
    download_twitter_media,
    set_auth_token,
    set_csrf_token,
)
from .ytdlp import download_with_ytdlp

__all__ = [
    "INSTAGRAM_REGEX",
    "download_instagram_media",
    "TWITTER_REGEX",
    "download_twitter_media",
    "set_auth_token",
    "set_csrf_token",
    "download_with_ytdlp",
    "download_with_gallery_dl",
    "downloader_manager",
    "DownloadResult",
    "DownloaderType",
]
