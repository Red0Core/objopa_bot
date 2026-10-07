#!/usr/bin/env python3
"""Run ffmpeg with a hard address-space cap so a mux cannot trip the OOM killer."""

from __future__ import annotations

import os
import sys

DEFAULT_FFMPEG_MEMORY_MB = 256


def ffmpeg_memory_mb() -> int:
    return max(64, int(os.getenv("FFMPEG_MEMORY_MB", str(DEFAULT_FFMPEG_MEMORY_MB))))


def apply_ffmpeg_memory_limit() -> None:
    cap = ffmpeg_memory_mb() * 1024 * 1024
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
    except (ImportError, ValueError, OSError):
        pass
    try:
        os.nice(10)
    except OSError:
        pass


def main() -> None:
    apply_ffmpeg_memory_limit()
    binary = os.getenv("FFMPEG_BIN", "ffmpeg")
    try:
        os.execvp(binary, [binary, *sys.argv[1:]])
    except OSError as exc:
        sys.stderr.write(f"ffmpeg exec failed: {exc}\n")
        sys.exit(127)


if __name__ == "__main__":
    main()
