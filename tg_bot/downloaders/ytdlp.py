"""Download with yt-dlp and mux separate tracks without transcoding."""

import asyncio
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from core.config import DOWNLOADS_DIR
from core.ffmpeg_cap import FFmpegError, probe_streams, run_ffmpeg
from core.logger import logger
from core.memory import MAX_MEDIA_BYTES, MediaTooLargeError, trim_memory
from tg_bot.utils.cookies_manager import cookies_manager

# Telegram/file checks may permit 50 MiB, but keep this downloader at a
# strict decimal 50 MB, including audio and MP4 muxing overhead.
MAX_OUTPUT_BYTES = min(MAX_MEDIA_BYTES, 50_000_000)
RESOLUTION_STEPS = (1080, 720, 480, 360)
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm"}
AUDIO_EXTS = {".m4a", ".mp3", ".opus", ".ogg", ".flac", ".wav"}
MEDIA_TEMPLATE = (
    '{"media":%(.{title,filepath,vcodec,acodec})j,"tracks":%(requested_formats.:.{filepath,vcodec,acodec})j}'
)


def _format_selector(max_height: int) -> str:
    """Prefer H.264/AAC and video+audio, never request > max_height."""
    return (
        f"bv[vcodec^=avc1][ext=mp4][height<={max_height}]+ba[ext=m4a]/"
        f"bv[ext=mp4][height<={max_height}]+ba[ext=m4a]/"
        f"b[ext=mp4][height<={max_height}]/"
        f"bv[height<={max_height}]+ba/"
        f"b[height<={max_height}]"
    )


def _clean_error_text(error: str) -> str:
    return ANSI_ESCAPE_RE.sub("", error).strip()


def _is_youtube(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"youtu.be", "youtube.com", "youtube-nocookie.com"} or host.endswith(
        (".youtube.com", ".youtube-nocookie.com")
    )


def _file_path(info: dict, directory: Path) -> Path | None:
    filename = info.get("filepath")
    if isinstance(filename, str) and filename:
        path = Path(filename).resolve()
        if path.parent == directory:
            return path
    return None


def _has_codec(info: dict, field: str) -> bool:
    value = info.get(field)
    return isinstance(value, str) and value not in ("", "none", "unknown")


def _download_records(stdout: str) -> list[dict]:
    records = []
    for line in stdout.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and isinstance(record.get("media"), dict):
            records.append(record)
    return records


def _ready_file(record: dict, directory: Path, *, youtube: bool) -> Path | None:
    media = record["media"]
    expected = _file_path(media, directory)
    if expected is None:
        return None
    tracks = record.get("tracks") or []
    if tracks:
        if len(tracks) != 2:
            return None
        video = next((_file_path(track, directory) for track in tracks if _has_codec(track, "vcodec")), None)
        audio = next((_file_path(track, directory) for track in tracks if _has_codec(track, "acodec")), None)
        if video is None or audio is None or video == audio or not video.is_file() or not audio.is_file():
            return None
        video_size, audio_size = video.stat().st_size, audio.stat().st_size
        if not video_size or not audio_size:
            return None
        size = video_size + audio_size
        if size > MAX_OUTPUT_BYTES:
            raise MediaTooLargeError(size)
        output = expected.with_suffix(".mp4")
        if output in (video, audio):
            return None
        run_ffmpeg(
            [
                "-protocol_whitelist",
                "file",
                "-threads",
                "1",
                "-i",
                str(video),
                "-protocol_whitelist",
                "file",
                "-threads",
                "1",
                "-i",
                str(audio),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                "-n",
                str(output),
            ]
        )
        return output

    if not expected.is_file() or expected.suffix.lower() not in (VIDEO_EXTS | AUDIO_EXTS):
        return None
    if youtube and (expected.suffix.lower() not in VIDEO_EXTS or not {"video", "audio"} <= probe_streams(expected)):
        return None
    return expected


def _run_ytdlp(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("MALLOC_ARENA_MAX", "2")
    env.setdefault("OMP_NUM_THREADS", "1")
    env.setdefault("OPENBLAS_NUM_THREADS", "1")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
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
    download_path = download_path.resolve()
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

        base_cmd = [
            sys.executable,
            "-m",
            "yt_dlp",
            "--ignore-config",
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
            "--no-abort-on-error",
            "--fixup",
            "never",
            "--downloader",
            "native",
            # An explicit nonexistent location prevents discovery of system FFmpeg.
            "--ffmpeg-location",
            str(tmp / "no-local-media-tools"),
            # Best-effort early abort; only stat() below enforces final filesize.
            "--max-filesize",
            str(MAX_OUTPUT_BYTES),
            "--extractor-args",
            "generic:impersonate=chrome",
            "--print",
            f"after_move:{MEDIA_TEMPLATE}",
        ]
        if cookies_path:
            base_cmd.extend(("--cookies", str(cookies_path)))
            logger.info(f"Using cookies for {cookies_manager.get_site_name(url)}")

        # Each attempt has its own output directory, so an oversized/partial
        # 1080p download can never be mistaken for a successful 720p download.
        for max_height in RESOLUTION_STEPS:
            attempt_dir = tmp / f"height_{max_height}"
            attempt_dir.mkdir()
            cmd = base_cmd + [
                "-f",
                _format_selector(max_height),
                "-o",
                str(attempt_dir / "%(id)s.%(ext)s"),
                url,
            ]
            result = await asyncio.to_thread(_run_ytdlp, cmd, tmp)
            stderr = _clean_error_text((result.stderr or "")[-2000:])

            if result.returncode != 0:
                error = stderr or f"yt-dlp exited with code {result.returncode}"
                # Authentication, network/HTTP, and extractor failures are not
                # improved by immediately retrying the same URL four times.
                fatal = (
                    "http error 403",
                    "http error 429",
                    "login required",
                    "sign in",
                    "authentication",
                    "unable to extract",
                    "unsupported url",
                    "timed out",
                    "connection error",
                    "this video is unavailable",
                    "private video",
                )
                if cookies_manager.has_cookies_error(error):
                    logger.warning(f"Cookies required for {cookies_manager.get_site_name(url)}")
                    break
                if any(marker in error.lower() for marker in fatal):
                    break
                # A missing format or an oversized stream may work at a lower resolution.
                logger.info(f"yt-dlp {max_height}p attempt failed; trying a lower resolution")
                shutil.rmtree(attempt_dir, ignore_errors=True)
                continue

            candidates = []
            error = None
            for record in _download_records(result.stdout or ""):
                if title is None:
                    title = record["media"].get("title")
                try:
                    candidate = await asyncio.to_thread(_ready_file, record, attempt_dir, youtube=youtube)
                except (FFmpegError, MediaTooLargeError) as exc:
                    error = str(exc)
                    continue
                if candidate is not None:
                    candidates.append(candidate)

            videos = [path for path in candidates if path.suffix.lower() in VIDEO_EXTS]
            for src in dict.fromkeys(videos or candidates):
                actual_bytes = src.stat().st_size
                if not 0 < actual_bytes <= MAX_OUTPUT_BYTES:
                    logger.warning(
                        f"Drop {src.name}: {actual_bytes} bytes > {MAX_OUTPUT_BYTES} bytes "
                        f"(after merging; {max_height}p attempt)"
                    )
                    continue
                dest = download_path / src.name
                if dest.exists():
                    dest = download_path / f"{src.stem}_{secrets.token_hex(4)}{src.suffix}"
                shutil.move(str(src), dest)
                files.append(dest)

            if files:
                logger.info(f"yt-dlp download OK: <= {max_height}p, all files <= {MAX_OUTPUT_BYTES} bytes")
                error = None
                break
            requirement = "combined audio+video" if youtube else "media"
            error = error or f"No valid {requirement} <= {MAX_OUTPUT_BYTES} bytes at {max_height}p"
            shutil.rmtree(attempt_dir, ignore_errors=True)

        if not files:
            error = error or f"yt-dlp could not produce media within {MAX_OUTPUT_BYTES} bytes"
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
