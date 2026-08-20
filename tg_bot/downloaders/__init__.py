from .downloader_manager import DownloaderType, DownloadResult, downloader_manager
from .patterns import INSTAGRAM_REGEX, TWITTER_REGEX

__all__ = [
    "INSTAGRAM_REGEX",
    "TWITTER_REGEX",
    "downloader_manager",
    "DownloadResult",
    "DownloaderType",
]
