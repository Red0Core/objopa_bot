"""Low-resource yt-dlp downloader with reliable FFmpeg stream-copy merging."""

import asyncio
import os
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from core.config import DOWNLOADS_DIR
from core.logger import logger
from core.memory import FFMPEG_MEMORY_MB, MAX_MEDIA_BYTES, trim_memory
from tg_bot.utils.cookies_manager import cookies_manager

MAX_SIZE_MB = max(1, MAX_MEDIA_BYTES // (1024 * 1024))
VIDEO_MB = max(1, MAX_SIZE_MB - 8)
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm"}

# Prefer H.264 video + AAC audio for MP4 stream-copy without transcoding.
# Favor size-known streams first. Unknown-size streams fall back to 480p;
# downloading 720p of unknown size often exceeds the Telegram limit.
FORMAT_SELECTOR = (
    f"bv[vcodec^=avc1][ext=mp4][height<=720][filesize<{VIDEO_MB}M]+ba[ext=m4a][filesize<8M]/"
    f"bv[vcodec^=avc1][ext=mp4][height<=720][filesize_approx<{VIDEO_MB}M]+ba[ext=m4a]/"
    f"bv[vcodec^=avc1][ext=mp4][height<=480]+ba[ext=m4a]/"
    f"b[ext=mp4][filesize<?{MAX_SIZE_MB}M]/"
    f"b[ext=mp4]/"
    f"b[filesize<?{MAX_SIZE_MB}M]"
)


def _clean_error_text(error: str) -> str:
    return ANSI_ESCAPE_RE.sub("", error).strip()


def _is_youtube(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"youtu.be", "youtube.com", "youtube-nocookie.com"} or host.endswith(
        (".youtube.com", ".youtube-nocookie.com")
    )


def _prepare_ffmpeg_tools(tmp: Path) -> Path:
    """Provide FFmpeg and FFprobe in a directory yt-dlp can actually discover.

    yt-dlp expects a binary named ffmpeg plus sibling ffprobe. Pointing it to
    core/ffmpeg_cap.py leaves FFprobe undiscoverable and may silently skip merging.
    """
    ffmpeg_bin = shutil.which("ffmpeg")
    ffprobe_bin = shutil.which("ffprobe")
    if not ffmpeg_bin or not ffprobe_bin:
        raise RuntimeError("FFmpeg and FFprobe are required for combined audio/video downloads")

    launcher = Path(__file__).resolve().parents[2] / "core" / "ffmpeg_cap.py"
    if not launcher.is_file():
        raise RuntimeError(f"FFmpeg resource limiter not found: {launcher}")

    # Git checkout may not preserve executable mode on the Python launcher.
    launcher.chmod(launcher.stat().st_mode | 0o111)

    tools = tmp / "ffmpeg_tools"
    tools.mkdir()
    # Resolves to the existing Python launcher; yt-dlp sees standard executable names.
    (tools / "ffmpeg").symlink_to(launcher)
    (tools / "ffprobe").symlink_to(Path(ffprobe_bin))
    return tools


def _probe_streams(path: Path) -> set[str]:
    """Only accept a YouTube video when both streams are present."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        return set()
    return set(result.stdout.split())


def _run_ytdlp(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("MALLOC_ARENA_MAX", "2")
    env.setdefault("OMP_NUM_THREADS", "1")
    env.setdefault("OPENBLAS_NUM_THREADS", "1")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # RLIMIT_AS limits *virtual address space*, not resident RAM. 256 MiB
    # prevents FFmpeg from even starting on common Linux builds (pthread_create).
    # 512 MiB virtual is a conservative floor for reliable stream-copy remuxing.
    env["FFMPEG_MEMORY_MB"] = str(max(512, FFMPEG_MEMORY_MB))
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
    """Download and merge into one playable video without transcoding."""
    download_path.mkdir(parents=True, exist_ok=True)
    tmp = download_path / f"ydl_{secrets.token_hex(8)}"
    tmp.mkdir()

    files: list[Path] = []
    title: str | None = None
    error: str | None = None
    cookies_path: Path | None = None
    youtube = _is_youtube(url)

    try:
        if use_cookies:
            site_name = cookies_manager.get_site_name(url)
            cookies_path = await cookies_manager.get_cookies(site_name)
            if not cookies_path:
                return [], None, "No cookies available"

        tools_dir = _prepare_ffmpeg_tools(tmp)
        cmd = [
            sys.executable,
            "-m",
            "yt_dlp",
            "--no-playlist",
            "--no-cache-dir",
            "--no-warnings",
            "--quiet",
            "--no-progress",
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
            str(tools_dir),
            "--merge-output-format",
            "mp4",
            # Merger already chooses streamcopy. Do not run an additional
            # --remux-video pass or +faststart (which rewrites the MP4 again).
            "--postprocessor-args",
            "Merger+ffmpeg:-nostdin -threads 1 -filter_threads 1 -c copy",
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
        ]
        if cookies_path:
            cmd.extend(("--cookies", str(cookies_path)))
            logger.info(f"Using cookies for {cookies_manager.get_site_name(url)}")
        cmd.append(url)

        result = await asyncio.to_thread(_run_ytdlp, cmd, tmp)
        stdout = (result.stdout or "").strip()
        stderr = _clean_error_text((result.stderr or "")[-2000:])
        if stdout:
            title = stdout.splitlines()[0].strip() or None

        if result.returncode != 0:
            error = stderr or f"yt-dlp exited with code {result.returncode}"
            if cookies_manager.has_cookies_error(error):
                logger.warning(f"Cookies required for {cookies_manager.get_site_name(url)}")
            return [], title, error

        candidates = sorted(
            (p for p in tmp.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS),
            key=lambda path: path.name,
        )
        if not youtube and not candidates:
            candidates = sorted(
                (
                    p
                    for p in tmp.iterdir()
                    if p.is_file() and p.suffix.lower() in {".m4a", ".mp3", ".opus", ".ogg", ".flac", ".wav"}
                ),
                key=lambda path: path.name,
            )
        for src in candidates:
            if src.stat().st_size > MAX_MEDIA_BYTES:
                logger.warning(f"Drop {src.name}: {src.stat().st_size / (1024 * 1024):.1f} MiB is too large")
                continue
            if youtube and not {"audio", "video"}.issubset(await asyncio.to_thread(_probe_streams, src)):
                logger.warning(f"Reject YouTube media {src.name}: missing audio or video stream")
                continue
            dest = download_path / src.name
            if dest.exists():
                dest = download_path / f"{src.stem}_{secrets.token_hex(4)}{src.suffix}"
            shutil.move(str(src), dest)
            files.append(dest)

        if not files:
            error = "yt-dlp could not produce a combined audio+video file within the Telegram size limit"
        elif use_cookies and title:
            title = f"{title} cookies_used"
    except Exception as exc:  # noqa: BLE001
        logger.error(f"yt-dlp download/merge failed: {exc}")
        error = _clean_error_text(str(exc))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if cookies_path and cookies_path.exists():
            cookies_path.unlink(missing_ok=True)
        trim_memory()

    return files, title, error
