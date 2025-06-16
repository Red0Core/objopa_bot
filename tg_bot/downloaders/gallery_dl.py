import asyncio
import subprocess
import secrets
import shutil
from pathlib import Path
from typing import List, Tuple

from core.logger import logger
from core.config import DOWNLOADS_PATH


async def download_with_gallery_dl(
    url: str, download_path: Path = DOWNLOADS_PATH
) -> Tuple[List[Path], str | None, str | None]:
    """Download media using gallery-dl and return file paths, title and error."""
    download_path.mkdir(exist_ok=True)

    files: List[Path] = []
    title: str | None = None
    error: str | None = None

    tmp = download_path / f"gdl_{secrets.token_hex(8)}"
    tmp.mkdir()

    def _download() -> None:
        nonlocal title
        cmd = [
            "gallery-dl",
            "-D",
            str(tmp),
            url,
        ]
        subprocess.run(cmd, check=False)

    try:
        await asyncio.to_thread(_download)
        for p in tmp.iterdir():
            if p.is_file():
                dest = download_path / p.name
                shutil.move(str(p), dest)
                files.append(dest)
        tmp.rmdir()
    except Exception as e:  # noqa: BLE001
        logger.error(f"gallery-dl download error: {e}")
        error = str(e)

    return files, title, error
