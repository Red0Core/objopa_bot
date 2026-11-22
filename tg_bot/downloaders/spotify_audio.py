from pathlib import Path
from typing import TYPE_CHECKING

from core.ffmpeg_cap import run_ffmpeg
from core.memory import MAX_MEDIA_BYTES, MediaTooLargeError

if TYPE_CHECKING:
    from .spotify import TrackInfo

_MAX_AUDIO_BYTES = min(MAX_MEDIA_BYTES, 50_000_000)


def prepare_spotify_audio(tracks: list["TrackInfo"]) -> None:
    cover = next((track.local_cover_path for track in tracks if track.local_cover_path), None)
    thumbnail = None
    if cover and cover.stat().st_size < 200_000:
        with cover.open("rb") as image:
            if image.read(3) == b"\xff\xd8\xff":
                thumbnail = cover

    for track in tracks:
        _prepare_track(track, thumbnail)
    if cover and thumbnail is None:
        cover.unlink()
    for track in tracks:
        track.local_cover_path = thumbnail


def _prepare_track(track: "TrackInfo", cover: Path | None) -> None:
    source = track.local_path
    target = source if source.suffix.lower() in (".mp3", ".m4a") else source.with_suffix(".mp3")
    partial = target.with_suffix(f"{target.suffix}.part")
    try:
        _write_audio(track, target, partial, cover)
        size = partial.stat().st_size
        if size > _MAX_AUDIO_BYTES:
            raise MediaTooLargeError(size)
        partial.replace(target)
        if target != source:
            try:
                source.unlink()
            except OSError:
                target.unlink(missing_ok=True)
                raise
            track.local_path = target
    finally:
        partial.unlink(missing_ok=True)


def _write_audio(track: "TrackInfo", target: Path, partial: Path, cover: Path | None) -> None:
    source = track.local_path
    args = ["-protocol_whitelist", "file", "-threads", "1", "-i", str(source)]
    if cover:
        args.extend(("-protocol_whitelist", "file", "-threads", "1", "-i", str(cover)))

    args.extend(("-map", "0:a:0", "-map", "1:v:0" if cover else "0:v:0?", "-c", "copy"))
    if target != source:
        args.extend(
            (
                "-c:a",
                "libmp3lame",
                "-b:a",
                "320k",
                "-compression_level",
                "7",
                "-ar",
                "44100",
                "-ac",
                "2",
                "-threads:a",
                "1",
            )
        )
    if cover:
        args.extend(("-disposition:v:0", "attached_pic", "-metadata:s:v:0", "comment=Cover (front)"))
    if track.title:
        args.extend(("-metadata", f"title={track.title}"))
    if track.artist:
        args.extend(("-metadata", f"artist={track.artist}"))

    if target.suffix.lower() == ".mp3":
        args.extend(("-id3v2_version", "3", "-f", "mp3"))
    else:
        args.extend(("-f", "ipod"))
    # Leave room above the upload limit so a truncated encode is rejected.
    args.extend(("-fs", str(_MAX_AUDIO_BYTES + 64 * 1024), "-n", str(partial)))
    run_ffmpeg(args)
