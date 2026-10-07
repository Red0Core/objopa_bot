"""RAM hygiene for long-running bot/backend processes."""

from __future__ import annotations

import asyncio
import gc
import os
from collections import OrderedDict
from pathlib import Path
from typing import Generic, Hashable, Mapping, TypeVar

from core.logger import logger

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")

MAX_CONCURRENT_DOWNLOADS = max(1, int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "1")))
MEMORY_TRIM_INTERVAL_SEC = max(60, int(os.getenv("MEMORY_TRIM_INTERVAL_SEC", "180")))
MAX_MEDIA_BYTES = int(os.getenv("MAX_MEDIA_BYTES", str(50 * 1024 * 1024)))
STREAM_CHUNK_BYTES = 64 * 1024
RSS_TRIM_THRESHOLD_MB = float(os.getenv("RSS_TRIM_THRESHOLD_MB", "280"))
MAX_GPT_SESSIONS = max(1, int(os.getenv("MAX_GPT_SESSIONS", "8")))
GPT_SESSION_TIMEOUT_SEC = max(60, int(os.getenv("GPT_SESSION_TIMEOUT_SEC", "1200")))
REDIS_MAX_CONNECTIONS = max(2, int(os.getenv("REDIS_MAX_CONNECTIONS", "8")))
AIOHTTP_CONNECTOR_LIMIT = max(4, int(os.getenv("AIOHTTP_CONNECTOR_LIMIT", "16")))
DOWNLOAD_TTL_SEC = max(60, int(os.getenv("DOWNLOAD_TTL_SEC", "1800")))
FFMPEG_MEMORY_MB = max(64, int(os.getenv("FFMPEG_MEMORY_MB", "256")))


class MediaTooLargeError(Exception):
    """Remote payload exceeded MAX_MEDIA_BYTES while streaming to disk."""

    def __init__(self, size: int):
        super().__init__(f"media exceeds cap: {size} bytes")
        self.size = size


class LRUCache(Generic[K, V]):
    """Tiny in-process LRU so metadata caches cannot grow without bound."""

    def __init__(self, maxsize: int = 32):
        self.maxsize = maxsize
        self._data: OrderedDict[K, V] = OrderedDict()

    def get(self, key: K) -> V | None:
        value = self._data.get(key)
        if value is not None:
            self._data.move_to_end(key)
        return value

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def __setitem__(self, key: K, value: V) -> None:
        if key in self._data:
            self._data.move_to_end(key)
        self._data[key] = value
        while len(self._data) > self.maxsize:
            self._data.popitem(last=False)

    def __len__(self) -> int:
        return len(self._data)

    def clear(self) -> None:
        self._data.clear()


DOWNLOAD_SLOT = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)


def current_rss_mb() -> float:
    try:
        with open("/proc/self/status", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    return 0.0


def current_peak_mb() -> float:
    try:
        with open("/proc/self/status", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    return 0.0


def _malloc_trim() -> None:
    try:
        import ctypes

        libc = ctypes.CDLL("libc.so.6")
        libc.malloc_trim(0)
    except Exception:
        pass


def trim_memory(*, freeze: bool = False) -> None:
    gc.collect()
    if freeze:
        try:
            gc.freeze()
        except Exception:
            pass
    _malloc_trim()


def maybe_trim_if_high() -> None:
    rss = current_rss_mb()
    if rss >= RSS_TRIM_THRESHOLD_MB:
        trim_memory()
        logger.info(
            f"RSS {rss:.1f} MB over {RSS_TRIM_THRESHOLD_MB:.0f} MB threshold, trimmed to {current_rss_mb():.1f} MB"
        )


def memory_report() -> str:
    rss = current_rss_mb()
    peak = current_peak_mb()
    collected = gc.get_count()
    return (
        f"RAM: RSS {rss:.1f} MB, peak {peak:.1f} MB\n"
        f"GC generations: {collected}\n"
        f"Download slots: {MAX_CONCURRENT_DOWNLOADS}\n"
        f"GPT sessions cap: {MAX_GPT_SESSIONS} / timeout {GPT_SESSION_TIMEOUT_SEC}s\n"
        f"Media cap: {MAX_MEDIA_BYTES / (1024 * 1024):.0f} MB\n"
        f"FFmpeg AS cap: {FFMPEG_MEMORY_MB} MB"
    )


def unlink_quietly(*paths: Path | None) -> int:
    removed = 0
    for path in paths:
        if path is None:
            continue
        try:
            if path.is_file():
                path.unlink()
                removed += 1
        except OSError:
            pass
    return removed


def cleanup_paths(paths: list[Path]) -> int:
    removed = 0
    for path in paths:
        removed += unlink_quietly(path)
    return removed


async def stream_url_to_file(
    url: str,
    dest: Path,
    *,
    max_bytes: int = MAX_MEDIA_BYTES,
    headers: Mapping[str, str] | None = None,
    timeout: float = 60.0,
    chunk_size: int = STREAM_CHUNK_BYTES,
) -> int:
    """Download `url` straight to disk. Never holds the full payload in RAM."""
    import httpx

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{os.urandom(3).hex()}.part")
    written = 0
    limits = httpx.Limits(max_connections=2, max_keepalive_connections=0)
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            limits=limits,
            follow_redirects=True,
            http2=False,
        ) as client:
            async with client.stream("GET", url, headers=dict(headers or {})) as resp:
                resp.raise_for_status()
                content_length = resp.headers.get("content-length")
                if content_length and int(content_length) > max_bytes:
                    raise MediaTooLargeError(int(content_length))
                with tmp.open("wb") as fh:
                    async for chunk in resp.aiter_bytes(chunk_size):
                        if not chunk:
                            continue
                        written += len(chunk)
                        if written > max_bytes:
                            raise MediaTooLargeError(written)
                        fh.write(chunk)
        if written <= 0:
            tmp.unlink(missing_ok=True)
            raise MediaTooLargeError(0)
        tmp.replace(dest)
        return written
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


async def memory_maintenance_loop() -> None:
    while True:
        await asyncio.sleep(MEMORY_TRIM_INTERVAL_SEC)
        rss_before = current_rss_mb()
        trim_memory()
        rss_after = current_rss_mb()
        logger.info(f"Memory trim: {rss_before:.1f} MB -> {rss_after:.1f} MB (peak {current_peak_mb():.1f} MB)")


async def start_memory_maintenance(bot=None) -> None:  # noqa: ARG001
    trim_memory(freeze=True)
    asyncio.create_task(memory_maintenance_loop())
    logger.info(
        f"Memory maintenance started (trim every {MEMORY_TRIM_INTERVAL_SEC}s, "
        f"downloads={MAX_CONCURRENT_DOWNLOADS}, rss_trim={RSS_TRIM_THRESHOLD_MB:.0f}MB)"
    )
