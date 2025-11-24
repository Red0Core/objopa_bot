import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from curl_cffi import requests
from lxml import html

from core.config import DOWNLOADS_DIR
from core.logger import logger

SPOTIFY_TRACK_REGEX = re.compile(
    r"^(?:https?://)?(?:open\.)?spotify\.com/track/[A-Za-z0-9]+(?:\S+)?$"
)
SPOTIFY_TRACK_ID_RE = re.compile(r"(?:https?://)?(?:open\.)?spotify\.com/track/([A-Za-z0-9]+)")

SPOTIFY_BASE = "https://open.spotify.com/track"
BACKEND_SPOTIFY_DOWNLOAD_BASE = "https://masterolic.xyz:5000"


def extract_track_id(url: str) -> str | None:
    """
    Возвращает Spotify track ID из ссылки или None, если это не трек.
    """
    m = SPOTIFY_TRACK_ID_RE.search(url.strip())
    if not m:
        return None
    return m.group(1)


def parse_track_html(html_text: str) -> dict:
    tree = html.fromstring(html_text)

    # Универсальный title: og:title может быть и в name, и в property
    title = tree.xpath('string(//meta[@name="og:title" or @property="og:title"]/@content)')
    title = title.strip() if title else None

    # Универсальный artist: music:musician_description может быть и в name, и в property
    artist = tree.xpath(
        'string(//meta[@name="music:musician_description" or '
        '@property="music:musician_description"]/@content)'
    )
    artist = artist.strip() if artist else None

    # Обложка трека: og:image тоже иногда бывает в name / property
    cover = tree.xpath('string(//meta[@name="og:image" or @property="og:image"]/@content)')
    cover = cover.strip() if cover else None

    logger.info(f"Parsed track metadata: title={title}, artist={artist}, cover={cover}")
    return {
        "title": title,
        "artist": artist,
        "cover_url": cover,
    }


@dataclass
class TrackInfo:
    title: str | None
    artist: str | None
    cover_url: str | None
    stream_url: str
    local_path: Path
    bitrate_kbps: int | None
    local_cover_path: Path | None


def fetch_spotify_html(track_id: str) -> str:
    url = f"{SPOTIFY_BASE}/{track_id}"
    resp = requests.get(url)
    resp.raise_for_status()
    return resp.text


def get_stream_url(track_id: str) -> str:
    url = f"{BACKEND_SPOTIFY_DOWNLOAD_BASE}/download"
    resp = requests.post(url, json={"id": track_id})
    resp.raise_for_status()
    data = resp.json()
    return data["stream_url"]


def download_binary(url: str, out_path: Path):
    resp = requests.get(url, impersonate="chrome", stream=True)
    resp.raise_for_status()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as f:
        for chunk in resp.iter_content(8192):
            if chunk:
                f.write(chunk)


def mux_audio_with_cover(
    ogg_path: Path,
    out_path: Path,
    title: str | None,
    artist: str | None,
):
    """
    Add metadata to OGG file. Cover is saved separately.
    """
    cmd = ["ffmpeg", "-y", "-i", str(ogg_path), "-c", "copy"]

    if title:
        cmd += ["-metadata", f"title={title}"]
    if artist:
        cmd += ["-metadata", f"artist={artist}"]
    cmd.append(str(out_path))

    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def get_bitrate_kbps(path: Path) -> int | None:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=bit_rate",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    val = proc.stdout.strip()
    if not val:
        return None
    try:
        return int(int(val) / 1000)
    except ValueError:
        return None


def download_spotify_track(track_id: str, base_dir: Path = DOWNLOADS_DIR) -> TrackInfo:
    html = fetch_spotify_html(track_id)
    meta = parse_track_html(html)

    stream_url = get_stream_url(track_id)
    full_stream_url = (
        f"{BACKEND_SPOTIFY_DOWNLOAD_BASE}{stream_url}" if stream_url.startswith("/") else stream_url
    )

    # имена файлов
    safe_title = (meta["title"] or track_id).replace("/", "_")
    safe_artist = (meta["artist"] or "Unknown").replace("/", "_")

    track_dir = base_dir / track_id
    ogg_path = track_dir / f"{track_id}.ogg"
    cover_path = track_dir / f"{track_id}.jpg"
    out_path = track_dir / f"{safe_artist} - {safe_title}.ogg"

    # качаем ogg
    download_binary(full_stream_url, ogg_path)

    # получаем битрейт входного файла
    input_bitrate = get_bitrate_kbps(ogg_path)

    # качаем обложку (если есть)
    if meta["cover_url"]:
        download_binary(meta["cover_url"], cover_path)
    else:
        cover_path = None

    # конвертируем с метаданными (без обложки)
    mux_audio_with_cover(ogg_path, out_path, meta["title"], meta["artist"])

    bitrate_kbps = get_bitrate_kbps(out_path)

    return TrackInfo(
        title=meta["title"],
        artist=meta["artist"],
        cover_url=meta["cover_url"],
        stream_url=full_stream_url,
        local_path=out_path,
        bitrate_kbps=bitrate_kbps,
        local_cover_path=cover_path,
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("Usage: python spotify.py <spotify_track_url>")
        sys.exit(1)

    track_url = sys.argv[1]
    track_id = extract_track_id(track_url)
    if not track_id:
        print("Invalid Spotify track URL")
        sys.exit(1)

    track_info = download_spotify_track(track_id)
    print(f"Downloaded: {track_info.local_path}")
    print(f"Title: {track_info.title}")
    print(f"Artist: {track_info.artist}")
    print(f"Bitrate: {track_info.bitrate_kbps} kbps")
    if track_info.local_cover_path:
        print(f"Cover: {track_info.local_cover_path}")
