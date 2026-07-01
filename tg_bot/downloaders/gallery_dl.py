import asyncio
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

from core.config import DOWNLOADS_DIR
from core.logger import logger
from tg_bot.utils.cookies_manager import cookies_manager


async def download_with_gallery_dl(
    url: str, download_path: Path = DOWNLOADS_DIR, use_cookies: bool = False
) -> Tuple[List[Path], str | None, str | None]:
    """Download media using gallery-dl and return file paths, title and error."""
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

    def _download() -> None:
        nonlocal title
        cmd = [
            sys.executable,
            "-m",
            "gallery_dl",
            "-D",
            str(tmp),
        ]
        if cookies_path:
            cmd.extend(("--cookies", str(cookies_path)))
        cmd.append(url)
        subprocess.run(cmd, check=False)

    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _download)
        for p in tmp.iterdir():
            if p.is_file():
                dest = download_path / p.name
                shutil.move(str(p), dest)
                files.append(dest)
        tmp.rmdir()
    except Exception as e:  # noqa: BLE001
        logger.error(f"gallery-dl download error: {e}")
        error = str(e)
    finally:
        if cookies_path and cookies_path.exists():
            cookies_path.unlink(missing_ok=True)

    return files, title, error
