import re
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp
from urllib.parse import urljoin

from httpcloak import Session

from core.config import DOWNLOADS_DIR
from core.logger import logger
from core.memory import MAX_MEDIA_BYTES, STREAM_CHUNK_BYTES, MediaTooLargeError

SPOTIFY_URL_PREFIX = r"^(?:https?://)?(?:open\.)?spotify\.com/"
SPOTIFY_URL_SUFFIX = r"([A-Za-z0-9]+)/?(?:[?#]\S*)?$"
SPOTIFY_TRACK_REGEX = re.compile(f"{SPOTIFY_URL_PREFIX}track/{SPOTIFY_URL_SUFFIX}")
SPOTIFY_ALBUM_REGEX = re.compile(f"{SPOTIFY_URL_PREFIX}album/{SPOTIFY_URL_SUFFIX}")

MAKPLAY_API = "https://api.makplay.com"
QUALITY = 320


def extract_track_id(url: str) -> str | None:
    match = SPOTIFY_TRACK_REGEX.fullmatch(url.strip())
    return match.group(1) if match else None


@dataclass
class TrackInfo:
    title: str | None
    artist: str | None
    cover_url: str | None
    stream_url: str
    local_path: Path
    bitrate_kbps: int | None
    local_cover_path: Path | None


def _session() -> Session:
    # HTTPCloak's streaming request to Makplay fails over HTTP/2 on Windows.
    return Session(preset="chrome-latest-windows", http_version="h1", timeout=30, retry=0)


def _cover_url(album: dict) -> str | None:
    images = album.get("images") or []
    thumbnails = [
        image for image in images if 0 < (image.get("width") or 0) <= 320 and 0 < (image.get("height") or 0) <= 320
    ]
    cover = max(thumbnails, key=lambda image: image["width"], default={})
    return cover.get("url")


def _download_binary(session: Session, url: str, path: Path) -> None:
    with session.get_stream(url, timeout=120_000) as response:
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError(f"Expected a complete media response, got HTTP {response.status_code}")

        headers = {
            name.lower(): value[0] if isinstance(value, list) and value else value or ""
            for name, value in response.headers.items()
        }
        content_type = headers.get("content-type", "").lower()
        if content_type.startswith("text/") or "json" in content_type:
            raise ValueError(f"Expected media, got {content_type}")
        length = headers.get("content-length", "")
        expected = int(length) if length.isdecimal() else None
        if expected is not None and expected > MAX_MEDIA_BYTES:
            raise MediaTooLargeError(expected)

        written = 0
        with path.open("wb") as file:
            for chunk in response.iter_content(STREAM_CHUNK_BYTES):
                written += len(chunk)
                if written > MAX_MEDIA_BYTES:
                    raise MediaTooLargeError(written)
                file.write(chunk)

        if not written or (expected is not None and written != expected):
            raise ValueError("Media response was empty or incomplete")


def _audio_extension(path: Path) -> str:
    with path.open("rb") as file:
        header = file.read(12)
        if header.startswith(b"OggS"):
            # Progressive responses can omit Content-Length, so check the final Ogg page.
            file.seek(max(0, path.stat().st_size - (27 + 255 + 255 * 255)))
            tail = file.read()
            page_start = len(tail)
            while (page_start := tail.rfind(b"OggS", 0, page_start)) >= 0:
                page = tail[page_start:]
                if len(page) < 27 or not page[5] & 4:
                    continue
                segments = 27 + page[26]
                if segments <= len(page) and segments + sum(page[27:segments]) == len(page):
                    return ".ogg"
            raise ValueError("Makplay returned an incomplete Ogg stream")
    if header.startswith(b"fLaC"):
        return ".flac"
    if header[4:8] == b"ftyp":
        return ".m4a"
    if header.startswith(b"ID3") or (len(header) >= 2 and header[0] == 0xFF and header[1] & 0xE6 == 0xE2):
        return ".mp3"
    raise ValueError("Makplay returned an unsupported audio format")


def _download_track(session: Session, track_id: str, track: dict, cover_url: str | None, base_dir: Path) -> TrackInfo:
    if not re.fullmatch(r"[A-Za-z0-9]+", track_id):
        raise ValueError("Invalid Spotify track ID")

    title = track.get("name")
    artist = ", ".join(entry["name"] for entry in (track.get("artists") or []) if entry.get("name")) or None
    response = session.post(
        f"{MAKPLAY_API}/download",
        json={"id": track_id, "name": title, "artist": artist, "quality": QUALITY},
    )
    response.raise_for_status()
    stream = response.json().get("stream_url")
    if not isinstance(stream, str) or not stream.strip():
        raise ValueError("Makplay did not return a stream URL")
    stream_url = urljoin(f"{MAKPLAY_API}/", stream.strip())
    if not stream_url.startswith("https://"):
        raise ValueError("Makplay returned an invalid stream URL")

    base_dir.mkdir(parents=True, exist_ok=True)
    track_dir = Path(mkdtemp(prefix=f"{track_id}_", dir=base_dir))
    partial_audio = track_dir / "audio.part"
    cover_path = track_dir / f"{track_dir.name}.jpg"
    try:
        _download_binary(session, stream_url, partial_audio)
        extension = _audio_extension(partial_audio)
        stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", f"{artist or 'Unknown'} - {title or track_id}")
        audio_path = partial_audio.replace(track_dir / f"{stem[:100].rstrip(' .')}{extension}")
    except BaseException:
        partial_audio.unlink(missing_ok=True)
        track_dir.rmdir()
        raise

    if cover_url:
        try:
            _download_binary(session, cover_url, cover_path)
        except Exception as exc:
            cover_path.unlink(missing_ok=True)
            logger.warning("Failed to download Spotify cover for {}: {}", track_id, exc)

    return TrackInfo(
        title, artist, cover_url, stream_url, audio_path, None, cover_path if cover_path.exists() else None
    )


def download_spotify_track(track_id: str, base_dir: Path = DOWNLOADS_DIR) -> TrackInfo:
    with _session() as session:
        response = session.get(f"{MAKPLAY_API}/getTrack", params={"track_id": track_id})
        response.raise_for_status()
        track = response.json()
        return _download_track(session, track_id, track, _cover_url(track.get("album") or {}), base_dir)


def download_spotify_album(album_id: str, base_dir: Path = DOWNLOADS_DIR) -> tuple[str, list[TrackInfo]]:
    tracks = []
    offset = 0
    try:
        with _session() as session:
            while True:
                response = session.get(
                    f"{MAKPLAY_API}/getAlbum", params={"album_id": album_id, "limit": 50, "offset": offset}
                )
                response.raise_for_status()
                album = response.json()
                page = album["tracks"]
                items = page["items"]
                if not items:
                    raise ValueError("Makplay returned an empty album track page")

                for track in items:
                    track_id = track.get("id") or track.get("track_id")
                    if not track_id:
                        raise ValueError("Makplay returned an album track without an ID")
                    cover_url = _cover_url(album) if not tracks else None
                    tracks.append(_download_track(session, track_id, track, cover_url, base_dir))

                if not page.get("next"):
                    return album["name"], tracks
                offset += len(items)
    except BaseException:
        for track in tracks:
            track.local_path.unlink(missing_ok=True)
            if track.local_cover_path:
                track.local_cover_path.unlink(missing_ok=True)
            track.local_path.parent.rmdir()
        raise


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Download a Spotify track or album through Makplay")
    parser.add_argument("url", help="Spotify track or album URL")
    args = parser.parse_args()

    if track_id := extract_track_id(args.url):
        downloaded = [download_spotify_track(track_id)]
    elif album_match := SPOTIFY_ALBUM_REGEX.fullmatch(args.url.strip()):
        _, downloaded = download_spotify_album(album_match.group(1))
    else:
        parser.error("Invalid Spotify track or album URL")

    for track in downloaded:
        print(f"Downloaded: {track.local_path}")
        if track.local_cover_path:
            print(f"Cover: {track.local_cover_path}")
