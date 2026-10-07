import asyncio
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

from core.config import DOWNLOADS_DIR
from core.logger import logger
from core.memory import MAX_MEDIA_BYTES, trim_memory
from tg_bot.utils.cookies_manager import cookies_manager


def _run_gallery_dl(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("MALLOC_ARENA_MAX", "2")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        cmd,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


async def download_with_gallery_dl(
    url: str, download_path: Path = DOWNLOADS_DIR, use_cookies: bool = False
) -> Tuple[List[Path], str | None, str | None]:
    """Download media using gallery-dl in a child process and return file paths."""
    download_path.mkdir(exist_ok=True)

    files: List[Path] = []
    title: str | None = None
    error: str | None = None
    cookies_path: Path | None = None

    tmp = download_path / f"gdl_{secrets.token_hex(8)}"
    tmp.mkdir()

    if use_cookies:
        site_name = cookies_manager.get_site_name(url)
        cookies_path = await cookies_manager.get_cookies(site_name)

    max_mb = max(1, MAX_MEDIA_BYTES // (1024 * 1024))
    cmd = [
        sys.executable,
        "-m",
        "gallery_dl",
        "-D",
        str(tmp),
        "--range",
        "1-12",
        "--filter",
        f"filesize is None or filesize < {MAX_MEDIA_BYTES}",
        "--filesize-max",
        f"{max_mb}M",
    ]
    if cookies_path:
        cmd.extend(("--cookies", str(cookies_path)))
    cmd.append(url)

    try:
        result = await asyncio.to_thread(_run_gallery_dl, cmd)
        for p in sorted((p for p in tmp.rglob("*") if p.is_file()), key=lambda path: str(path)):
            if p.stat().st_size > MAX_MEDIA_BYTES:
                p.unlink(missing_ok=True)
                continue
            dest = download_path / p.name
            if dest.exists():
                dest = download_path / f"{p.stem}_{secrets.token_hex(4)}{p.suffix}"
            shutil.move(str(p), dest)
            files.append(dest)

        if not files:
            output = (result.stderr or "").strip()[-1500:]
            error = output or f"gallery-dl exited with code {result.returncode}"
    except Exception as e:  # noqa: BLE001
        logger.error(f"gallery-dl download error: {e}")
        error = str(e)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if cookies_path and cookies_path.exists():
            cookies_path.unlink(missing_ok=True)
        trim_memory()

    return files, title, error
