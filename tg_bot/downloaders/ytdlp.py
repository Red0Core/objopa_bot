import traceback
from pathlib import Path

from yt_dlp import YoutubeDL

from core.config import DOWNLOADS_DIR
from core.logger import logger
from tg_bot.utils.cookies_manager import cookies_manager

MAX_SIZE_MB = 200  # 200 MB soft limit
MAX_SIZE_BYTES = MAX_SIZE_MB * 1024 * 1024
MAX_HEIGHT = 1440  # 2K max resolution
MIN_HEIGHT = 480  # Minimum height


async def download_with_ytdlp(
    url: str,
    download_path: Path = DOWNLOADS_DIR,
    use_cookies: bool = False,
) -> tuple[list[Path], str | None, str | None]:
    """Download media using yt-dlp with size-aware format selection."""
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
        nonlocal files, title, error

        current_opts = dict(ydl_opts)
        cookies_path = None

        if with_cookies:
            site_name = cookies_manager.get_site_name(url)
            cookies_path = await cookies_manager.get_cookies(site_name)
            if cookies_path:
                current_opts["cookiefile"] = str(cookies_path)
                logger.info(f"Using cookies for {site_name}")
            else:
                logger.info(f"No cookies available for {site_name}")
                return False

        try:
            with YoutubeDL(current_opts) as ydl:  # type: ignore
                info: dict | None = ydl.extract_info(url, download=False)  # type: ignore
                if not info:
                    error = "❌ Failed to extract video information."
                    return False

                duration = info.get("duration") or 0
                formats = info.get("formats", [])

                # Filter video formats by resolution
                video_formats = [
                    fmt
                    for fmt in formats
                    if fmt.get("vcodec") != "none"
                    and fmt.get("height")
                    and MIN_HEIGHT <= fmt.get("height", 0) <= MAX_HEIGHT
                ]

                if not video_formats:
                    error = f"❌ No formats found within {MIN_HEIGHT}p-{MAX_HEIGHT}p range."
                    return False

                # Sort by quality (best first)
                video_formats.sort(key=lambda f: (f.get("height", 0), f.get("tbr", 0) or 0), reverse=True)

                # Find suitable format
                selected_fmt = None
                selected_size = 0

                for fmt in video_formats:
                    # Estimate size
                    if "filesize" in fmt and fmt["filesize"]:
                        base_size = fmt["filesize"]
                    elif "filesize_approx" in fmt and fmt["filesize_approx"]:
                        base_size = fmt["filesize_approx"]
                    else:
                        tbr = fmt.get("tbr", 0)
                        if not tbr or duration <= 0:
                            continue
                        base_size = int((tbr * 1000 / 8) * duration)

                    # Add audio (128kbps) if video-only
                    if fmt.get("acodec") == "none" and duration > 0:
                        base_size += int((128 * 1000 / 8) * duration)

                    # Add 30% safety margin
                    estimated_size = int(base_size * 1.3)

                    size_mb = estimated_size / (1024 * 1024)
                    logger.info(f"Format {fmt['format_id']} ({fmt.get('height')}p): ~{size_mb:.1f}MB")

                    # Skip if too large (250MB hard limit with margin)
                    if estimated_size > MAX_SIZE_BYTES * 1.25:
                        logger.info("  ✗ Too large, skipping")
                        continue

                    # Take first suitable (best quality that fits)
                    selected_fmt = fmt
                    selected_size = estimated_size
                    logger.info("  ✓ Selected")
                    break

                if not selected_fmt:
                    title = info.get("title", "Video")
                    error = f"❌ No suitable format found for {title}. All exceed {MAX_SIZE_MB}MB limit."
                    return False

                # Download
                format_selector = (
                    f"{selected_fmt['format_id']}+bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio[ext=aac]/bestaudio"
                )
                ydl.params["format"] = format_selector
                ydl.params["merge_output_format"] = "mp4"
                ydl.params["postprocessor_args"] = {"ffmpeg": ["-movflags", "faststart"]}

                logger.info(f"Downloading {selected_fmt['format_id']} (~{selected_size / (1024 * 1024):.1f}MB)...")
                downloaded: dict | None = ydl.extract_info(url, download=True)  # type: ignore
                if not downloaded:
                    error = "❌ Download failed."
                    return False

                title = downloaded.get("title")
                entries = downloaded["entries"] if "entries" in downloaded else [downloaded]

                for entry in entries:
                    file_path = Path(ydl.prepare_filename(entry))  # type: ignore
                    files.append(file_path)

                    # Check actual size
                    if file_path.exists():
                        actual_size = file_path.stat().st_size
                        actual_mb = actual_size / (1024 * 1024)
                        logger.info(f"Downloaded: {actual_mb:.1f}MB")

                        # Delete if way too large (>400MB absolute limit)
                        if actual_size > MAX_SIZE_BYTES * 2:
                            logger.error(f"File {actual_mb:.1f}MB exceeds 400MB limit, deleting")
                            try:
                                file_path.unlink()
                                files.remove(file_path)
                            except Exception as e:
                                logger.error(f"Failed to delete: {e}")

                            error = f"❌ Downloaded file ({actual_mb:.1f}MB) too large."
                            return False

                # Mark cookies usage in title
                if with_cookies and title:
                    resolution = selected_fmt.get("resolution") or f"{selected_fmt.get('height')}p"
                    title = f"{title} (used={resolution})cookies_used"

                return True

        except Exception as e:
            error = str(e)

            if with_cookies:
                logger.exception(f"Download with cookies failed: {e}")
                return False

            if cookies_manager.has_cookies_error(error):
                site_name = cookies_manager.get_site_name(url)
                logger.warning(f"Cookies required for {site_name}")
                await cookies_manager.mark_cookies_expired(site_name)
                return False

            logger.error(f"yt-dlp error: {traceback.format_exc()}")
            return False

        finally:
            if cookies_path and cookies_path.exists():
                try:
                    cookies_path.unlink()
                except Exception:
                    pass

    # Try download
    if use_cookies and await _download_attempt(with_cookies=True):
        return files, title, error

    if await _download_attempt(with_cookies=False):
        return files, title, error

    return files, title, error
