import re
from dataclasses import dataclass
from pathlib import Path

from curl_cffi import requests
from lxml import html

from core.config import DOWNLOADS_DIR
from core.logger import logger
from core.memory import MAX_MEDIA_BYTES

SPOTIFY_TRACK_REGEX = re.compile(r"^(?:https?://)?(?:open\.)?spotify\.com/track/[A-Za-z0-9]+(?:\S+)?$")
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
        'string(//meta[@name="music:musician_description" or @property="music:musician_description"]/@content)'
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
    try:
        resp = requests.get(url, impersonate="chrome", stream=True)
        resp.raise_for_status()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("wb") as f:
            written = 0
            for chunk in resp.iter_content(8192):
                if not chunk:
                    continue
                written += len(chunk)
                if written > MAX_MEDIA_BYTES:
                    raise ValueError("Spotify file exceeds 50MB cap")
                f.write(chunk)
    except Exception:
        logger.exception(f"Failed to download binary: {url}")


def download_spotify_track(track_id: str, base_dir: Path = DOWNLOADS_DIR) -> TrackInfo:
    html = fetch_spotify_html(track_id)
    meta = parse_track_html(html)

    stream_url = get_stream_url(track_id)
    full_stream_url = f"{BACKEND_SPOTIFY_DOWNLOAD_BASE}{stream_url}" if stream_url.startswith("/") else stream_url

    safe_title = (meta["title"] or track_id).replace("/", "_")
    safe_artist = (meta["artist"] or "Unknown").replace("/", "_")

    track_dir = base_dir / track_id
    ogg_path = track_dir / f"{safe_artist} - {safe_title}.ogg"
    cover_path = track_dir / f"{track_id}.jpg"

    download_binary(full_stream_url, ogg_path)

    if meta["cover_url"]:
        download_binary(meta["cover_url"], cover_path)
    else:
        cover_path = None

    return TrackInfo(
        title=meta["title"],
        artist=meta["artist"],
        cover_url=meta["cover_url"],
        stream_url=full_stream_url,
        local_path=ogg_path,
        bitrate_kbps=None,
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
