"""Regression tests for yt-dlp -> FFmpeg stream-copy -> Telegram file contract."""

import asyncio
import importlib.util
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "downloaders" / "ytdlp.py"


@pytest.fixture
def downloader(monkeypatch, tmp_path):
    # The downloader module is tested in isolation from the bot's Redis/config.
    logger = types.SimpleNamespace(info=lambda *_: None, warning=lambda *_: None, error=lambda *_: None)
    stub_modules = {
        "core": types.ModuleType("core"),
        "core.config": types.ModuleType("core.config"),
        "core.logger": types.ModuleType("core.logger"),
        "core.memory": types.ModuleType("core.memory"),
        "tg_bot": types.ModuleType("tg_bot"),
        "tg_bot.utils": types.ModuleType("tg_bot.utils"),
        "tg_bot.utils.cookies_manager": types.ModuleType("tg_bot.utils.cookies_manager"),
    }
    stub_modules["core.config"].DOWNLOADS_DIR = tmp_path
    stub_modules["core.logger"].logger = logger
    stub_modules["core.memory"].FFMPEG_MEMORY_MB = 256
    stub_modules["core.memory"].MAX_MEDIA_BYTES = 50 * 1024 * 1024
    stub_modules["core.memory"].trim_memory = lambda: None
    stub_modules["tg_bot.utils.cookies_manager"].cookies_manager = types.SimpleNamespace(
        get_site_name=lambda url: "youtube", get_cookies=lambda url: None, has_cookies_error=lambda s: False
    )
    for name, module in stub_modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    spec = importlib.util.spec_from_file_location("isolated_ytdlp", MODULE_PATH)
    instance = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(instance)
    return instance


def test_format_and_ffmpeg_commands(downloader, tmp_path):
    temp = tmp_path / "run"
    temp.mkdir()
    tools = downloader._prepare_ffmpeg_tools(temp)
    assert (tools / "ffmpeg").is_file()
    assert (tools / "ffprobe").is_file()
    assert os.access(tools / "ffmpeg", os.X_OK)
    for executable in ("ffmpeg", "ffprobe"):
        version = subprocess.run(
            [str(tools / executable), "-version"],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "FFMPEG_MEMORY_MB": "512"},
        )
        assert version.returncode == 0, version.stderr
    assert "avc1" in downloader.FORMAT_SELECTOR
    assert "ba[ext=m4a]" in downloader.FORMAT_SELECTOR


def test_ffmpeg_streamcopy_merges_under_memory_cap(downloader, tmp_path):
    temp = tmp_path / "run"
    temp.mkdir()
    tools = downloader._prepare_ffmpeg_tools(temp)
    video = tmp_path / "video.mp4"
    audio = tmp_path / "audio.m4a"
    merged = tmp_path / "merged.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=black:s=160x90:r=10",
            "-t",
            "1",
            "-an",
            "-c:v",
            "mpeg4",
            "-threads",
            "1",
            "-y",
            str(video),
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:a",
            "aac",
            "-y",
            str(audio),
        ],
        check=True,
    )
    result = subprocess.run(
        [
            str(tools / "ffmpeg"),
            "-v",
            "error",
            "-i",
            str(video),
            "-i",
            str(audio),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c",
            "copy",
            "-threads",
            "1",
            "-y",
            str(merged),
        ],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "FFMPEG_MEMORY_MB": "512"},
    )
    assert result.returncode == 0, result.stderr
    assert downloader._probe_streams(merged) == {"video", "audio"}


def test_youtube_only_returns_combined_video(downloader, tmp_path, monkeypatch):
    sample = tmp_path / "silent.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=black:s=160x90:r=10",
            "-t",
            "1",
            "-an",
            "-c:v",
            "mpeg4",
            "-threads",
            "1",
            "-y",
            str(sample),
        ],
        check=True,
    )
    original = sample.read_bytes()

    def fake_run(cmd, cwd):
        assert "--ffmpeg-location" in cmd
        assert (cwd / "ffmpeg_tools" / "ffprobe").exists()
        assert "--remux-video" not in cmd
        assert "--merge-output-format" in cmd
        (cwd / "silent.mp4").write_bytes(original)
        (cwd / "silent.m4a").write_bytes(b"audio sidecar")
        return subprocess.CompletedProcess(cmd, 0, stdout="test title\n", stderr="")

    monkeypatch.setattr(downloader, "_run_ytdlp", fake_run)
    files, title, error = asyncio.run(downloader.download_with_ytdlp("https://www.youtube.com/watch?v=xyz", tmp_path))
    assert files == []
    assert title == "test title"
    assert "combined audio+video" in error
    assert list(tmp_path.glob("ydl_*")) == []


def test_failed_merge_does_not_return_separate_tracks(downloader, tmp_path, monkeypatch):
    def fake_run(cmd, cwd):
        (cwd / "video.mp4").write_bytes(b"incomplete")
        (cwd / "audio.m4a").write_bytes(b"audio")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="ffmpeg failed to merge")

    monkeypatch.setattr(downloader, "_run_ytdlp", fake_run)
    files, title, error = asyncio.run(downloader.download_with_ytdlp("https://youtu.be/xyz", tmp_path))
    assert files == []
    assert title is None
    assert error == "ffmpeg failed to merge"


def test_non_youtube_audio_download_preserved(downloader, tmp_path, monkeypatch):
    def fake_run(cmd, cwd):
        (cwd / "song.mp3").write_bytes(b"music")
        return subprocess.CompletedProcess(cmd, 0, stdout="My song", stderr="")

    monkeypatch.setattr(downloader, "_run_ytdlp", fake_run)
    files, title, error = asyncio.run(downloader.download_with_ytdlp("https://example.com/song", tmp_path))
    assert len(files) == 1 and files[0].name == "song.mp3"
    assert title == "My song" and error is None


def test_invalid_non_youtube_not_moved_if_exit_failure(downloader, tmp_path, monkeypatch):
    def fake_run(cmd, cwd):
        (cwd / "partial.mp4").write_bytes(b"partial")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="extractor failed")

    monkeypatch.setattr(downloader, "_run_ytdlp", fake_run)
    files, _, error = asyncio.run(downloader.download_with_ytdlp("https://example.com/video", tmp_path))
    assert not files and error == "extractor failed"


def test_youtube_returns_one_merged_mp4(downloader, tmp_path, monkeypatch):
    video = tmp_path / "v.mp4"
    audio = tmp_path / "a.m4a"
    merged = tmp_path / "merged.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=black:s=160x90:r=10",
            "-t",
            "1",
            "-an",
            "-c:v",
            "mpeg4",
            "-threads",
            "1",
            "-y",
            str(video),
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:a",
            "aac",
            "-y",
            str(audio),
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(video),
            "-i",
            str(audio),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c",
            "copy",
            "-y",
            str(merged),
        ],
        check=True,
    )
    original = merged.read_bytes()

    def fake_run(cmd, cwd):
        (cwd / "merged.mp4").write_bytes(original)
        (cwd / "unused.m4a").write_bytes(b"unused")
        return subprocess.CompletedProcess(cmd, 0, stdout="merged title", stderr="")

    monkeypatch.setattr(downloader, "_run_ytdlp", fake_run)
    files, title, error = asyncio.run(downloader.download_with_ytdlp("https://youtube.com/watch?v=123", tmp_path))
    assert error is None and title == "merged title"
    assert len(files) == 1 and files[0].name.startswith("merged") and files[0].suffix == ".mp4"
    assert downloader._probe_streams(files[0]) == {"video", "audio"}
