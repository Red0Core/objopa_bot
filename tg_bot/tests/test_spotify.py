import json
import shutil
import subprocess
import wave
from unittest.mock import AsyncMock, Mock, call

import pytest

from core.memory import MediaTooLargeError
from tg_bot.downloaders import DownloaderType, spotify, spotify_audio
from tg_bot.downloaders.downloader_manager import DownloaderManager
from tg_bot.downloaders.spotify_audio import prepare_spotify_audio
from tg_bot.utils.media_sender import media_sender

API = spotify.MAKPLAY_API
TRACK_ID = "4rs059owSVfY29UDvQBE3a"
ALBUM_ID = "1MmLboI3jFxbvt3ECL092V"
COVER = "https://i.scdn.co/image/example"
COVER_IMAGE = {"url": COVER, "width": 300, "height": 300}
OGG = b"OggS\x00\x04" + bytes(20) + b"\x01\x03abc"
requires_media_tools = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="requires FFmpeg with libmp3lame and FFprobe"
)


def probe_audio(path, *options):
    result = subprocess.check_output(
        ["ffprobe", "-v", "error", *options, "-of", "json", str(path)],
        encoding="utf-8",
    )
    return json.loads(result)


def json_response(data):
    return Mock(json=Mock(return_value=data))


def stream_response(payload, *, headers=None, status=200):
    response = Mock(
        status_code=status,
        headers=headers or {},
        iter_content=Mock(return_value=iter((payload[:5], payload[5:]))),
    )
    return Mock(__enter__=Mock(return_value=response), __exit__=Mock(return_value=False))


@pytest.fixture
def session(monkeypatch):
    session = Mock()
    factory = Mock(return_value=Mock(__enter__=Mock(return_value=session), __exit__=Mock(return_value=False)))
    monkeypatch.setattr(spotify, "Session", factory)
    return session


@pytest.mark.parametrize(
    ("payload", "suffix", "stream"),
    [(OGG, ".ogg", "/stream/example"), (b"fLaCsample", ".flac", f"{API}/stream/example")],
)
def test_download_track_uses_320_and_preserves_container(session, tmp_path, payload, suffix, stream):
    session.get.return_value = json_response(
        {
            "name": "Home / Demo",
            "artists": [{"name": "Artist"}],
            "album": {"images": [{"url": f"{COVER}/large", "width": 640, "height": 640}, COVER_IMAGE]},
        }
    )
    session.post.return_value = json_response({"stream_url": stream})
    session.get_stream.side_effect = [
        stream_response(payload, headers={"content-length": [str(len(payload))]}),
        stream_response(b"\xff\xd8\xffcover", headers={"Content-Type": ["image/jpeg"]}),
    ]

    track = spotify.download_spotify_track(TRACK_ID, tmp_path)

    session.get.assert_called_once_with(f"{API}/getTrack", params={"track_id": TRACK_ID})
    session.post.assert_called_once_with(
        f"{API}/download", json={"id": TRACK_ID, "name": "Home / Demo", "artist": "Artist", "quality": 320}
    )
    assert [request.args[0] for request in session.get_stream.call_args_list] == [f"{API}/stream/example", COVER]
    assert track.local_path.name == f"Artist - Home _ Demo{suffix}"
    assert track.local_path.read_bytes() == payload
    assert track.local_cover_path.name == f"{track.local_path.parent.name}.jpg"
    assert track.local_cover_path.read_bytes() == b"\xff\xd8\xffcover"


def test_album_paginates_and_reuses_track_metadata(session, tmp_path):
    album = {"name": "Demo Album", "images": [COVER_IMAGE]}
    session.get.side_effect = [
        json_response({**album, "tracks": {"items": [{"id": TRACK_ID, "name": "First"}], "next": "next-page"}}),
        json_response({**album, "tracks": {"items": [{"track_id": "second", "name": "Second"}], "next": None}}),
    ]
    session.post.side_effect = [
        json_response({"stream_url": "/stream/first"}),
        json_response({"stream_url": "/stream/second"}),
    ]
    session.get_stream.side_effect = [stream_response(OGG), stream_response(b"cover"), stream_response(OGG)]

    title, tracks = spotify.download_spotify_album(ALBUM_ID, tmp_path)

    assert session.get.call_args_list == [
        call(f"{API}/getAlbum", params={"album_id": ALBUM_ID, "limit": 50, "offset": 0}),
        call(f"{API}/getAlbum", params={"album_id": ALBUM_ID, "limit": 50, "offset": 1}),
    ]
    assert [request.kwargs["json"]["id"] for request in session.post.call_args_list] == [TRACK_ID, "second"]
    assert [request.kwargs["json"]["quality"] for request in session.post.call_args_list] == [320, 320]
    assert [request.args[0] for request in session.get_stream.call_args_list] == [
        f"{API}/stream/first",
        COVER,
        f"{API}/stream/second",
    ]
    assert title == "Demo Album"
    assert [track.title for track in tracks] == ["First", "Second"]
    assert all(track.local_path.read_bytes() == OGG for track in tracks)


def test_album_cleans_up_when_download_fails(session, tmp_path):
    session.get.return_value = json_response(
        {"name": "Demo Album", "tracks": {"items": [{"id": TRACK_ID}, {"id": "second"}]}}
    )
    session.post.side_effect = [json_response({"stream_url": "/stream/first"}), json_response({})]
    session.get_stream.return_value = stream_response(OGG)

    with pytest.raises(ValueError, match="did not return a stream URL"):
        spotify.download_spotify_album(ALBUM_ID, tmp_path)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("payload", "headers", "status", "error", "message"),
    [
        (OGG, {"Content-Length": ["500"]}, 200, spotify.MediaTooLargeError, "exceeds cap"),
        (b"x" * 101, {}, 200, spotify.MediaTooLargeError, "exceeds cap"),
        (OGG, {}, 206, ValueError, "complete media response"),
        (b"error", {"Content-Type": ["text/html"]}, 200, ValueError, "Expected media"),
        (OGG[:-1], {"Content-Length": [str(len(OGG))]}, 200, ValueError, "empty or incomplete"),
        (OGG[:-1], {}, 200, ValueError, "incomplete Ogg"),
        (b"invalid audio", {}, 200, ValueError, "unsupported audio"),
    ],
)
def test_failed_transfer_leaves_no_audio(session, monkeypatch, tmp_path, payload, headers, status, error, message):
    monkeypatch.setattr(spotify, "MAX_MEDIA_BYTES", 100)
    session.get.return_value = json_response({})
    session.post.return_value = json_response({"stream_url": "/stream/example"})
    session.get_stream.return_value = stream_response(payload, headers=headers, status=status)

    with pytest.raises(error, match=message):
        spotify.download_spotify_track(TRACK_ID, tmp_path)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
@requires_media_tools
@pytest.mark.parametrize(("kind", "item_id"), [("track", TRACK_ID), ("album", ALBUM_ID)])
async def test_manager_routes_spotify_urls(monkeypatch, tmp_path, kind, item_id):
    source = tmp_path / "Демо.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setparams((2, 2, 44_100, 0, "NONE", "not compressed"))
        audio.writeframes(bytes(44_100 * 4))
    track = spotify.TrackInfo("Home Demo", "Artist", None, f"{API}/stream/example", source, None, None)
    downloader = Mock(return_value=track if kind == "track" else ("Demo Album", [track]))
    monkeypatch.setattr(spotify, f"download_spotify_{kind}", downloader)

    result = await DownloaderManager().download_media(f"https://open.spotify.com/{kind}/{item_id}?si=example")

    downloader.assert_called_once_with(item_id)
    assert result.success
    assert result.downloader_used is DownloaderType.SPOTIFY
    assert result.files == [track.local_path]
    assert result.spotify_tracks == [track]
    assert result.caption == ("Home Demo - Artist" if kind == "track" else "Demo Album")
    assert not source.exists()
    assert track.local_path.suffix == ".mp3"
    media = probe_audio(track.local_path, "-show_entries", "stream=codec_name,bit_rate:format_tags=title,artist")
    assert media["streams"][0]["codec_name"] == "mp3"
    assert round(int(media["streams"][0]["bit_rate"]) / 1000) == 320
    assert media["format"]["tags"] == {"title": "Home Demo", "artist": "Artist"}


@requires_media_tools
@pytest.mark.parametrize(("suffix", "encoder"), [(".mp3", "libmp3lame"), (".m4a", "aac")])
def test_tags_and_cover_preserve_encoded_audio_packets(tmp_path, suffix, encoder):
    source = tmp_path / f"track{suffix}"
    cover = tmp_path / "cover.jpg"
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
            encoder,
            "-threads",
            "1",
            str(source),
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-filter_threads",
            "1",
            "-f",
            "lavfi",
            "-i",
            "color=blue:s=64x64",
            "-frames:v",
            "1",
            "-threads",
            "1",
            str(cover),
        ],
        check=True,
    )
    packet_options = (
        "-select_streams",
        "a:0",
        "-show_packets",
        "-show_data_hash",
        "sha256",
        "-show_entries",
        "packet=data_hash",
    )
    before = probe_audio(source, *packet_options)["packets"]
    track = spotify.TrackInfo("Название", "Artist", None, "", source, None, cover)

    prepare_spotify_audio([track])

    assert track.local_path == source and track.local_cover_path == cover
    assert before and probe_audio(source, *packet_options)["packets"] == before
    media = probe_audio(
        source, "-show_entries", "stream=codec_name:stream_disposition=attached_pic:format_tags=title,artist"
    )
    assert media["format"]["tags"] == {"title": "Название", "artist": "Artist"}
    assert any(stream["codec_name"] == "mjpeg" and stream["disposition"]["attached_pic"] for stream in media["streams"])
    assert sorted(tmp_path.iterdir()) == sorted([source, cover])


@requires_media_tools
def test_oversized_mp3_is_not_returned_as_a_partial_track(monkeypatch, tmp_path):
    source = tmp_path / "track.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setparams((2, 2, 44_100, 0, "NONE", "not compressed"))
        audio.writeframes(bytes(3 * 44_100 * 4))
    track = spotify.TrackInfo("Title", "Artist", None, "", source, None, None)
    monkeypatch.setattr(spotify_audio, "_MAX_AUDIO_BYTES", 8192)

    with pytest.raises(MediaTooLargeError):
        prepare_spotify_audio([track])

    assert track.local_path == source
    assert list(tmp_path.iterdir()) == [source]


@pytest.mark.asyncio
@pytest.mark.parametrize("tracks_count", [1, 2])
async def test_sender_uses_telegram_music_with_metadata(tmp_path, tracks_count):
    cover_path = tmp_path / "album.jpg"
    cover_path.write_bytes(b"\xff\xd8\xffcover")
    tracks = []
    for index in range(tracks_count):
        path = tmp_path / f"{index}.mp3"
        path.write_bytes(b"sample")
        tracks.append(spotify.TrackInfo(f"Track {index}", "Artist", COVER, "", path, 320, cover_path))
    message = AsyncMock()
    files = [track.local_path for track in tracks] + [cover_path]

    assert await media_sender.send(message, files, "Demo Album", spotify_tracks=tracks)

    if tracks_count == 1:
        message.reply_media_group.assert_not_awaited()
        message.reply_audio.assert_awaited_once()
        args, options = message.reply_audio.await_args
        assert args[0].path == tracks[0].local_path
        assert options["title"] == "Track 0"
        assert options["performer"] == "Artist"
        assert options["thumbnail"].path == cover_path
    else:
        message.reply_audio.assert_not_awaited()
        message.reply_media_group.assert_awaited_once()
        media = message.reply_media_group.await_args.kwargs["media"]
        assert [item.type for item in media] == ["audio", "audio"]
        assert [item.title for item in media] == ["Track 0", "Track 1"]
        assert all(item.performer == "Artist" and item.thumbnail.path == cover_path for item in media)
    assert not any(path.exists() for path in files)
