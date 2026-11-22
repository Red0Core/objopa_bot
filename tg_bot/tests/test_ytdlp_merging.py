import json
import shutil
import subprocess
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from core.ffmpeg_cap import FFmpegError, probe_streams
from tg_bot.downloaders import ytdlp

SHORT = "https://www.youtube.com/shorts/xyz"


def _attempt_directory(cmd: list[str]) -> Path:
    return Path(cmd[cmd.index("-o") + 1]).parent


def _record(path: Path, *, tracks=()) -> str:
    return json.dumps({"media": {"title": "Clip 🎬", "filepath": str(path)}, "tracks": tracks})


def _split_result(cmd, _cwd):
    attempt = _attempt_directory(cmd)
    video = attempt / "clip.f137.mp4"
    audio = attempt / "clip.f140.m4a"
    video.write_bytes(b"video")
    audio.write_bytes(b"audio")
    tracks = [
        {"filepath": str(audio), "acodec": "mp4a", "vcodec": "none"},
        {"filepath": str(video), "acodec": "none", "vcodec": "avc1"},
    ]
    return subprocess.CompletedProcess(cmd, 0, _record(attempt / "clip.mp4", tracks=tracks), "")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "mux", "size"])
async def test_split_tracks_are_muxed_and_size_is_enforced(monkeypatch, tmp_path, failure):
    attempts = []

    def mux(args):
        output = Path(args[-1])
        attempts.append(output.parent.name)
        assert args[args.index("-i") + 1].endswith(".f137.mp4")
        assert args[args.index("-c") + 1] == "copy"
        assert args[args.index("-map") + 1] == "0:v:0"
        assert "1:a:0" in args and args.count("-threads") == 2
        if output.parent.name == "height_1080" and failure == "mux":
            raise FFmpegError("mux failed")
        length = 21 if output.parent.name == "height_1080" and failure == "size" else 13
        output.write_bytes(b"x" * length)

    monkeypatch.setattr(ytdlp, "MAX_OUTPUT_BYTES", 20)
    monkeypatch.setattr(ytdlp, "_run_ytdlp", _split_result)
    monkeypatch.setattr(ytdlp, "run_ffmpeg", mux)
    files, title, error = await ytdlp.download_with_ytdlp(SHORT, tmp_path)

    assert attempts == (["height_1080", "height_720"] if failure else ["height_1080"])
    assert files == [tmp_path / "clip.mp4"] and files[0].stat().st_size == 13
    assert title == "Clip 🎬" and error is None
    assert list(tmp_path.iterdir()) == files


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "filename", "streams", "expected"),
    [
        (SHORT, "clip.mp4", {"video", "audio"}, True),
        (SHORT, "clip.mp4", {"video"}, False),
        ("https://example.com/audio", "song.m4a", {"audio"}, True),
    ],
)
async def test_already_downloaded_media_checks_youtube_streams(monkeypatch, tmp_path, url, filename, streams, expected):
    def download(cmd, _cwd):
        path = _attempt_directory(cmd) / filename
        path.write_bytes(b"ready media")
        return subprocess.CompletedProcess(cmd, 0, _record(path), "")

    monkeypatch.setattr(ytdlp, "_run_ytdlp", download)
    monkeypatch.setattr(ytdlp, "probe_streams", lambda _: streams)
    monkeypatch.setattr(ytdlp, "run_ffmpeg", lambda _: pytest.fail("Single file must not be converted"))

    files, title, error = await ytdlp.download_with_ytdlp(url, tmp_path)

    assert title == "Clip 🎬"
    if expected:
        assert files == [tmp_path / filename] and error is None
    else:
        assert not files and "combined audio+video" in error
    assert list(tmp_path.iterdir()) == files


def _make_media(path, input_type, source_filter, *options):
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-filter_threads",
            "1",
            "-f",
            input_type,
            "-i",
            source_filter,
            "-t",
            "1",
            *options,
            "-threads",
            "1",
            str(path),
        ],
        check=True,
    )


def _packet_hashes(path, stream):
    result = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            stream,
            "-show_packets",
            "-show_data_hash",
            "sha256",
            "-show_entries",
            "packet=data_hash",
            "-of",
            "json",
            str(path),
        ],
        text=True,
    )
    return [packet["data_hash"] for packet in json.loads(result)["packets"]]


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="requires FFmpeg and FFprobe")
@pytest.mark.asyncio
async def test_native_ytdlp_and_ffmpeg_copy_both_packets(monkeypatch, tmp_path):
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    video = server_dir / "video.mp4"
    audio = server_dir / "audio.m4a"
    _make_media(video, "lavfi", "color=black:s=180x320:r=10", "-an", "-c:v", "libx264")
    _make_media(audio, "lavfi", "sine=frequency=440:duration=1", "-c:a", "aac")
    original_download = ytdlp._run_ytdlp

    with ThreadingHTTPServer(("127.0.0.1", 0), partial(_QuietHandler, directory=server_dir)) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        info = {
            "id": "xyz",
            "title": "Separate media",
            "webpage_url": SHORT,
            "extractor": "generic",
            "formats": [
                {
                    "url": f"{base}/video.mp4",
                    "format_id": "137",
                    "acodec": "none",
                    "vcodec": "avc1",
                    "ext": "mp4",
                    "height": 320,
                },
                {"url": f"{base}/audio.m4a", "format_id": "140", "vcodec": "none", "acodec": "mp4a", "ext": "m4a"},
            ],
        }
        info_path = tmp_path / "info.json"
        info_path.write_text(json.dumps(info), encoding="utf-8")

        def download(cmd, cwd):
            assert cmd[cmd.index("--downloader") + 1] == "native"
            assert not Path(cmd[cmd.index("--ffmpeg-location") + 1]).exists()
            return original_download(cmd[:-1] + ["--load-info-json", str(info_path)], cwd)

        monkeypatch.setattr(ytdlp, "_run_ytdlp", download)
        try:
            files, title, error = await ytdlp.download_with_ytdlp(SHORT, tmp_path / "downloads")
        finally:
            server.shutdown()
            thread.join()

    assert len(files) == 1 and title == "Separate media" and error is None
    result = files[0]
    assert probe_streams(result) == {"video", "audio"}
    for source, stream in ((video, "v:0"), (audio, "a:0")):
        hashes = _packet_hashes(source, stream)
        assert hashes and _packet_hashes(result, stream) == hashes
