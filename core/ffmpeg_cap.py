#!/usr/bin/env python3
"""Run media tools with shared concurrency and Linux resource limits."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from threading import Lock

MIN_FFMPEG_MEMORY_MB = 512
_MEDIA_TOOL_LOCK = Lock()


class FFmpegError(RuntimeError):
    """FFmpeg or FFprobe failed to process the input."""


def ffmpeg_memory_mb() -> int:
    # Common Linux builds cannot start below 512 MiB of virtual address space.
    return max(MIN_FFMPEG_MEMORY_MB, int(os.getenv("FFMPEG_MEMORY_MB", str(MIN_FFMPEG_MEMORY_MB))))


def _run_media_tool(tool: str, arguments: list[str], timeout: float) -> str:
    env = os.environ.copy()
    binary = env.get(f"{tool.upper()}_BIN") or tool
    command = [binary, *arguments]
    if sys.platform == "linux":
        env["FFMPEG_BIN"] = binary
        command = [sys.executable, str(Path(__file__).resolve()), *arguments]
    env.setdefault("MALLOC_ARENA_MAX", "2")
    env.setdefault("OMP_NUM_THREADS", "1")

    try:
        with _MEDIA_TOOL_LOCK:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=timeout,
                creationflags=getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0),
                check=False,
            )
    except subprocess.TimeoutExpired:
        raise FFmpegError(f"{tool} timed out after {timeout:g} seconds") from None
    except OSError as exc:
        raise FFmpegError(f"Could not start {tool}: {exc}") from exc
    if result.returncode:
        raise FFmpegError(result.stderr.strip()[-1000:] or f"{tool} exited with code {result.returncode}")
    return result.stdout


def run_ffmpeg(arguments: list[str]) -> None:
    _run_media_tool(
        "ffmpeg", ["-nostdin", "-hide_banner", "-loglevel", "error", "-filter_threads", "1", *arguments], 120
    )


def probe_streams(path: Path) -> set[str]:
    stdout = _run_media_tool(
        "ffprobe",
        [
            "-v",
            "error",
            "-protocol_whitelist",
            "file",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        15,
    )
    return set(stdout.split())


def _apply_limits() -> None:
    cap = ffmpeg_memory_mb() * 1024 * 1024
    try:
        import resource

        _, inherited_cap = resource.getrlimit(resource.RLIMIT_AS)
        if inherited_cap != resource.RLIM_INFINITY:
            cap = min(cap, inherited_cap)
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
    except (ImportError, ValueError, OSError):
        pass
    try:
        os.nice(10)
    except (AttributeError, OSError):
        pass
    try:
        # Keep the worker within one of the CPUs available to this service.
        os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
    except (AttributeError, OSError):
        pass


def main() -> None:
    _apply_limits()
    binary = os.getenv("FFMPEG_BIN", "ffmpeg")
    try:
        os.execvp(binary, [binary, *sys.argv[1:]])
    except OSError as exc:
        sys.stderr.write(f"{binary} exec failed: {exc}\n")
        sys.exit(127)


if __name__ == "__main__":
    main()
