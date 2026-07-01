import html
import http.cookiejar
import re
from pathlib import Path
from typing import Optional, Tuple

import aiofiles
from httpcloak import Session
from httpcloak.client import HTTPCloakError

from core.config import DOWNLOADS_DIR
from core.logger import logger
from tg_bot.downloaders.downloader_types import DownloaderType, DownloadResult


def _extract_from_html(text: str) -> Tuple[list[list[str]], Optional[str]]:
    """Extracts video URLs (sorted by quality) and caption from Instagram HTML.
    Returns a list of lists, where each sublist represents a video and contains URLs sorted by quality."""
    import json

    # List of lists, each sublist is a video's qualities
    video_urls_lists = []
    caption = None

    # 1. Try meta tags for caption
    meta_desc = re.search(r'<meta property="og:description" content="([^"]+)"', text)
    if meta_desc:
        caption = html.unescape(meta_desc.group(1))

    # 2. Extract from video_versions which contains the best quality videos
    scripts = re.findall(r'<script type="application/json"[^>]*>(.*?)</script>', text, re.DOTALL)

    # Instagram can have multiple videos in a carousel, so we collect URLs from each video_versions object
    extracted_urls_sets = []

    for script in scripts:
        if "video_versions" in script:
            try:
                data = json.loads(script)

                def search_urls(obj):
                    if isinstance(obj, dict):
                        if "video_versions" in obj:
                            versions = obj["video_versions"]
                            if versions:
                                # Get all qualities sorted by width
                                sorted_versions = sorted(versions, key=lambda x: x.get("width", 0) or 0, reverse=True)
                                current_vid_urls = []
                                for v in sorted_versions:
                                    if "url" in v and v["url"] not in current_vid_urls:
                                        current_vid_urls.append(v["url"])
                                if current_vid_urls and current_vid_urls not in extracted_urls_sets:
                                    extracted_urls_sets.append(current_vid_urls)
                                    video_urls_lists.append(current_vid_urls)
                        for v in obj.values():
                            search_urls(v)
                    elif isinstance(obj, list):
                        for v in obj:
                            search_urls(v)

                search_urls(data)
            except Exception:
                pass

    # 3. Try meta tags for video if video_versions failed
    if not video_urls_lists:
        meta_video = re.search(r'<meta property="og:video" content="([^"]+)"', text)
        if meta_video:
            video_urls_lists.append([html.unescape(meta_video.group(1))])

    # 4. Try embed json structures if all else failed
    if not video_urls_lists:
        for match in re.finditer(r'"video_url":"(.*?)"', text):
            video_urls_lists.append([match.group(1).replace("\\/", "/")])
            break

        if not video_urls_lists:
            for match in re.finditer(r'VideoURL\\":\\"(.*?)\\"', text):
                video_urls_lists.append([match.group(1).replace("\\/", "/")])
                break

        if not video_urls_lists:
            for match in re.finditer(r'video_url\\":\\"(.*?)\\"', text):
                video_urls_lists.append([match.group(1).replace("\\/", "/")])
                break

    if not caption:
        caption_match = re.search(r"\"node\":\{\"text\":\"(.*?)\"", text)
        if caption_match:
            raw_caption = caption_match.group(1).replace("\\n", "\n")
            try:
                caption = raw_caption.encode("utf-8").decode("unicode_escape")
            except Exception:
                caption = raw_caption

    # Final fix for any remaining unicode escapes in URLs
    final_urls = []
    for quality_list in video_urls_lists:
        clean_q_list = []
        for u in quality_list:
            u = u.replace("\\u0026", "&")
            u = u.replace("\\/", "/")
            while "\\/" in u:
                u = u.replace("\\/", "/")
            while "\\u0026" in u:
                u = u.replace("\\u0026", "&")
            clean_q_list.append(u)
        final_urls.append(clean_q_list)

    return final_urls, caption


async def _download_file(url: str, filepath: Path, session: Optional[Session] = None) -> bool:
    """Downloads a file using httpcloak to bypass blocks"""

    try:
        is_own_session = session is None
        if is_own_session:
            session = Session(preset="chrome-149")
        try:
            resp = await session.get_async(url)
            if resp.status_code == 200:
                async with aiofiles.open(filepath, "wb") as f:
                    await f.write(resp.body)
                return True
            else:
                logger.error(f"Failed to download video, status code: {resp.status_code}")
                return False
        finally:
            if is_own_session:
                session.close()
    except Exception as e:
        logger.error(f"Error downloading file: {e}")
        return False


async def download_reel(url: str, cookies_path: Path | None = None) -> DownloadResult:
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
        "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Mobile Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Upgrade-Insecure-Requests": "1",
    }

    video_urls = []
    caption = None

    # STRATEGY 1: No Cookies
    try:
        # We use run_in_executor for the context manager or just initialize session carefully
        session = Session(preset="chrome-149")
        try:
            # 1. Try normal URL
            logger.info(f"Trying to fetch Instagram Reel without cookies: {url}")
            resp = await session.get_async(url, headers=headers)
            video_urls, caption = _extract_from_html(resp.text)

            # 2. Try embed URL as fallback
            if not video_urls:
                logger.info(f"Video URL not found on main page, trying embed page for {shortcode}")
                embed_url = f"https://www.instagram.com/p/{shortcode}/embed/captioned/"
                resp_embed = await session.get_async(embed_url, headers=headers)

                embed_video_urls, embed_caption = _extract_from_html(resp_embed.text)
                if embed_video_urls:
                    video_urls = embed_video_urls
                if embed_caption and not caption:
                    caption = embed_caption
        finally:
            session.close()

    except HTTPCloakError as e:
        logger.error(f"Error fetching Instagram without cookies: {e}")

    # STRATEGY 2: Cookie Fallback
    if not video_urls and cookies_path and cookies_path.exists():
        logger.info("Falling back to authenticated request using cookies")
        try:
            cj = http.cookiejar.MozillaCookieJar(cookies_path)
            cj.load(ignore_discard=True, ignore_expires=True)
            # Format cookies for httpcloak
            httpcloak_cookies = []
            for cookie in cj:
                httpcloak_cookies.append(
                    {"name": cookie.name, "value": cookie.value, "domain": cookie.domain, "path": cookie.path}
                )

            session = Session(preset="chrome-149")
            try:
                # Add cookies manually to session
                for cookie in httpcloak_cookies:
                    session.cookies.set(cookie["name"], cookie["value"], domain=cookie["domain"], path=cookie["path"])

                resp = await session.get_async(url, headers=headers)
                video_urls, caption = _extract_from_html(resp.text)
            finally:
                session.close()
        except Exception as e:
            logger.error(f"Error in cookie fallback: {e}")

    if not video_urls:
        return DownloadResult(
            success=False,
            files=[],
            error="Failed to extract video URL",
            downloader_used=DownloaderType.CUSTOM,
        )

    # Implement Download with size limits (max 50 MB)
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    files_to_return = []

    session = Session(preset="chrome-149")
    try:
        # Download all unique extracted videos (useful for carousels)
        for i, quality_urls in enumerate(video_urls):
            video_path = (
                DOWNLOADS_DIR / f"{shortcode}_{i}.mp4" if len(video_urls) > 1 else DOWNLOADS_DIR / f"{shortcode}.mp4"
            )

            vid_downloaded = False
            for vid_url in quality_urls:
                # Check size via HEAD
                try:
                    head_resp = await session.head_async(vid_url)
                    size_raw = head_resp.headers.get("content-length", 0)
                    size = int(size_raw[0] if isinstance(size_raw, list) else size_raw)
                    # 50 MB in bytes limit
                    if size > 50 * 1024 * 1024:
                        logger.info(f"Video size {size} exceeds 50MB limit, trying lower quality...")
                        continue
                except Exception as e:
                    logger.warning(f"Failed to get video size via HEAD: {e}")

                logger.info(f"Downloading video to {video_path}")
                if await _download_file(vid_url, video_path):
                    files_to_return.append(video_path)
                    vid_downloaded = True
                    break

            if not vid_downloaded:
                logger.error(f"Could not download any quality for video index {i} under 50MB.")

    finally:
        session.close()

    if not files_to_return:
        return DownloadResult(
            success=False,
            files=[],
            error="Failed to download video files or all files exceeded 50MB limit",
            downloader_used=DownloaderType.CUSTOM,
        )

    # Implement Caption Save
    if caption:
        caption_path = DOWNLOADS_DIR / f"{shortcode}.txt"
        async with aiofiles.open(caption_path, "w", encoding="utf-8") as f:
            await f.write(caption)
        files_to_return.append(caption_path)

    return DownloadResult(
        success=True, files=files_to_return, caption=caption, error=None, downloader_used=DownloaderType.CUSTOM
    )
