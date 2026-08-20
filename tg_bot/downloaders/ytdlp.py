import asyncio
import os
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

from core.config import DOWNLOADS_DIR
from core.logger import logger
from core.memory import FFMPEG_MEMORY_MB, MAX_MEDIA_BYTES, trim_memory
from tg_bot.utils.cookies_manager import cookies_manager

MAX_SIZE_MB = max(1, MAX_MEDIA_BYTES // (1024 * 1024))
# Leave headroom so video+audio after remux stays under Telegram's 50MB.
VIDEO_MB = max(1, MAX_SIZE_MB - 8)
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm"}
# Merge best video+audio. Prefer AVC+AAC so ffmpeg only remuxes (-c copy), never recodes.
FORMAT_SELECTOR = (
    f"bv*[vcodec^=avc1][height<=1080][filesize<{VIDEO_MB}M]+ba[ext=m4a]/"
    f"bv*[ext=mp4][height<=1080][filesize<{VIDEO_MB}M]+ba[ext=m4a]/"
    f"bv*[height<=1080][filesize<{VIDEO_MB}M]+ba[filesize<8M]/"
    f"bv*[vcodec^=avc1][height<=1080][filesize_approx<{VIDEO_MB}M]+ba[ext=m4a]/"
    f"bv*[height<=720][filesize<{VIDEO_MB}M]+ba/"
    f"bv*[height<=480]+ba[ext=m4a]/"
    f"b[ext=mp4][filesize<{MAX_SIZE_MB}M]/"
    f"b[filesize<{MAX_SIZE_MB}M]"
)


def _clean_error_text(error: str) -> str:
    return ANSI_ESCAPE_RE.sub("", error).strip()


def _run_ytdlp(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("MALLOC_ARENA_MAX", "2")
    env.setdefault("OMP_NUM_THREADS", "1")
    env.setdefault("OPENBLAS_NUM_THREADS", "1")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.setdefault("FFMPEG_MEMORY_MB", str(FFMPEG_MEMORY_MB))
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


async def download_with_ytdlp(
    url: str,
    download_path: Path = DOWNLOADS_DIR,
    use_cookies: bool = False,
) -> tuple[list[Path], str | None, str | None]:
    """Download media with yt-dlp in a child process so RAM is returned to the OS."""
    download_path.mkdir(parents=True, exist_ok=True)

    files: list[Path] = []
    title: str | None = None
    error: str | None = None
    cookies_path: Path | None = None
    tmp = download_path / f"ydl_{secrets.token_hex(8)}"
    tmp.mkdir()

    if use_cookies:
        site_name = cookies_manager.get_site_name(url)
        cookies_path = await cookies_manager.get_cookies(site_name)
        if not cookies_path:
            shutil.rmtree(tmp, ignore_errors=True)
            return [], None, "No cookies available"

    ffmpeg_cap = Path(__file__).resolve().parents[2] / "core" / "ffmpeg_cap.py"
    try:
        ffmpeg_cap.chmod(0o755)
    except OSError:
        pass

    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-playlist",
        "--no-cache-dir",
        "--no-warnings",
        "--quiet",
        "--no-progress",
        "--newline",
        "--concurrent-fragments",
        "1",
        "--buffer-size",
        "16K",
        "--http-chunk-size",
        "1M",
        "--socket-timeout",
        "20",
        "--retries",
        "1",
        "--fragment-retries",
        "1",
        "--extractor-retries",
        "1",
        "--no-write-comments",
        "--no-mtime",
        "--no-keep-video",
        "--ffmpeg-location",
        str(ffmpeg_cap),
        "--merge-output-format",
        "mp4",
        "--remux-video",
        "mp4",
        "--postprocessor-args",
        "ffmpeg:-nostdin -threads 1 -filter_threads 1 -probesize 32k -analyzeduration 500000 "
        "-max_muxing_queue_size 128 -c copy -movflags +faststart",
        "--max-filesize",
        f"{MAX_SIZE_MB}M",
        "-f",
        FORMAT_SELECTOR,
        "--extractor-args",
        "generic:impersonate=chrome",
        "--print",
        "after_move:%(title)s",
        "-o",
        str(tmp / "%(id)s.%(ext)s"),
        url,
    ]
    if cookies_path:
        cmd.extend(("--cookies", str(cookies_path)))
        logger.info(f"Using cookies for {cookies_manager.get_site_name(url)}")

    try:
        result = await asyncio.to_thread(_run_ytdlp, cmd, tmp)
        stdout = (result.stdout or "").strip()
        stderr = _clean_error_text((result.stderr or "")[-2000:])

        if stdout:
            title = stdout.splitlines()[0].strip() or None

        collected: list[Path] = []
        for src in sorted((p for p in tmp.rglob("*") if p.is_file()), key=lambda path: str(path)):
            if src.stat().st_size > MAX_MEDIA_BYTES:
                logger.warning(f"Drop {src.name}: {src.stat().st_size / (1024 * 1024):.1f}MB > {MAX_SIZE_MB}MB")
                continue
            dest = download_path / src.name
            if dest.exists():
                dest = download_path / f"{src.stem}_{secrets.token_hex(4)}{src.suffix}"
            shutil.move(str(src), dest)
            collected.append(dest)

        videos = [p for p in collected if p.suffix.lower() in VIDEO_EXTS]
        files = videos or collected

        if not files:
            error = stderr or f"yt-dlp exited with code {result.returncode}"
            if cookies_manager.has_cookies_error(error):
                logger.warning(f"Cookies required for {cookies_manager.get_site_name(url)}")
        elif use_cookies and title:
            title = f"{title} cookies_used"
    except Exception as e:  # noqa: BLE001
        logger.error(f"yt-dlp subprocess error: {e}")
        error = _clean_error_text(str(e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if cookies_path and cookies_path.exists():
            cookies_path.unlink(missing_ok=True)
        trim_memory()

    return files, title, error
