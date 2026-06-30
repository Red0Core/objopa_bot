import html
import http.cookiejar
import re
from pathlib import Path
from typing import Optional, Tuple

from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import RequestException

from core.config import DOWNLOADS_DIR
from core.logger import logger
from tg_bot.downloaders.downloader_manager import DownloaderType, DownloadResult


async def _extract_from_html(text: str) -> Tuple[Optional[str], Optional[str]]:
    """Extracts video URL and caption from Instagram HTML."""
    video_url = None
    caption = None

    # 1. Try meta tags
    meta_video = re.search(r'<meta property="og:video" content="([^"]+)"', text)
    if meta_video:
        video_url = html.unescape(meta_video.group(1)).replace("\\/", "/")

    meta_desc = re.search(r'<meta property="og:description" content="([^"]+)"', text)
    if meta_desc:
        caption = html.unescape(meta_desc.group(1))

    # 2. Try embed json structures if meta tags failed
    if not video_url:
        for match in re.finditer(r'"video_url":"(.*?)"', text):
            video_url = match.group(1).replace(r"\/", "/")
            break

        if not video_url:
            for match in re.finditer(r'VideoURL\\":\\"(.*?)\\"', text):
                video_url = match.group(1).replace(r"\/", "/")
                break

        if not video_url:
            for match in re.finditer(r'video_url\\":\\"(.*?)\\"', text):
                video_url = match.group(1).replace(r"\/", "/")
                break

    if not caption:
        caption_match = re.search(r'\\"node\\":\{\\"text\\":\\"(.*?)\\"', text)
        if caption_match:
            raw_caption = caption_match.group(1).replace("\\n", "\n")
            try:
                caption = raw_caption.encode("utf-8").decode("unicode_escape")
            except Exception:
                caption = raw_caption

    # Final fix for any remaining unicode escapes in URL
    if video_url:
        video_url = video_url.replace("\\u0026", "&")
        # Just in case python backslash string literal made a difference
        video_url = video_url.replace("\\/", "/")
        # And another check because the backslashes are escaping correctly in the regex
        video_url = video_url.replace(r"\/", "/")

    return video_url, caption


async def _download_file(url: str, filepath: Path) -> bool:
    """Downloads a file using curl_cffi to bypass blocks"""
    try:
        async with AsyncSession(impersonate="chrome110") as session:
            resp = await session.get(url)
            if resp.status_code == 200:
                filepath.write_bytes(resp.content)
                return True
            else:
                logger.error(f"Failed to download video, status code: {resp.status_code}")
                return False
    except Exception as e:
        logger.error(f"Error downloading file: {e}")
        return False


async def download_reel(url: str, cookies_path: Optional[str] = None) -> DownloadResult:
    """
    Downloads an Instagram Reel by URL.
    Attempts without cookies first, falls back to cookies if provided.
    """
    shortcode_match = re.search(r"/(?:p|reel|tv)/([\w-]+)", url)
    if not shortcode_match:
        return DownloadResult(
            success=False, files=[], error="Could not extract shortcode from URL", downloader_used=DownloaderType.CUSTOM
        )

    shortcode = shortcode_match.group(1)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Upgrade-Insecure-Requests": "1",
    }

    video_url = None
    caption = None

    # STRATEGY 1: No Cookies
    try:
        async with AsyncSession(impersonate="chrome110") as session:
            # 1. Try normal URL
            logger.info(f"Trying to fetch Instagram Reel without cookies: {url}")
            resp = await session.get(url, headers=headers)
            video_url, caption = await _extract_from_html(resp.text)

            # 2. Try embed URL as fallback
            if not video_url:
                logger.info(f"Video URL not found on main page, trying embed page for {shortcode}")
                embed_url = f"https://www.instagram.com/p/{shortcode}/embed/captioned/"
                resp_embed = await session.get(embed_url, headers=headers)

                embed_video_url, embed_caption = await _extract_from_html(resp_embed.text)
                if embed_video_url:
                    video_url = embed_video_url
                if embed_caption and not caption:
                    caption = embed_caption

    except RequestException as e:
        logger.error(f"Error fetching Instagram without cookies: {e}")

    # STRATEGY 2: Cookie Fallback
    if not video_url and cookies_path and Path(cookies_path).exists():
        logger.info("Falling back to authenticated request using cookies")
        try:
            cj = http.cookiejar.MozillaCookieJar(cookies_path)
            cj.load(ignore_discard=True, ignore_expires=True)
            cookies = {cookie.name: cookie.value for cookie in cj}

            async with AsyncSession(impersonate="chrome110", cookies=cookies) as session:
                resp = await session.get(url, headers=headers)
                video_url, caption = await _extract_from_html(resp.text)
        except Exception as e:
            logger.error(f"Error in cookie fallback: {e}")

    if not video_url:
        return DownloadResult(
            success=False,
            files=[],
            error="Failed to extract video URL",
            downloader_used=DownloaderType.CUSTOM,
        )

    # Implement Download
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    video_path = DOWNLOADS_DIR / f"{shortcode}.mp4"

    logger.info(f"Downloading video to {video_path}")
    download_success = await _download_file(video_url, video_path)

    if not download_success:
        return DownloadResult(
            success=False,
            files=[],
            error="Failed to download video file from extracted URL",
            downloader_used=DownloaderType.CUSTOM,
        )

    files_to_return = [video_path]

    # Implement Caption Save
    if caption:
        caption_path = DOWNLOADS_DIR / f"{shortcode}.txt"
        caption_path.write_text(caption, encoding="utf-8")
        files_to_return.append(caption_path)

    return DownloadResult(
        success=True, files=files_to_return, caption=caption, error=None, downloader_used=DownloaderType.CUSTOM
    )
