from unittest.mock import Mock, call

import pytest

from tg_bot.downloaders import DownloaderType, spotify
from tg_bot.downloaders.downloader_manager import DownloaderManager

API = spotify.MAKPLAY_API
TRACK_ID = "4rs059owSVfY29UDvQBE3a"
ALBUM_ID = "1MmLboI3jFxbvt3ECL092V"
COVER = "https://i.scdn.co/image/example"
OGG = b"OggS\x00\x04" + bytes(20) + b"\x01\x03abc"


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
        {"name": "Home / Demo", "artists": [{"name": "Artist"}], "album": {"images": [{"url": COVER}]}}
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
    album = {"name": "Demo Album", "images": [{"url": COVER}]}
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
@pytest.mark.parametrize(("kind", "item_id"), [("track", TRACK_ID), ("album", ALBUM_ID)])
async def test_manager_routes_spotify_urls(monkeypatch, tmp_path, kind, item_id):
    track = spotify.TrackInfo("Home Demo", "Artist", None, f"{API}/stream/example", tmp_path / "demo.ogg", None, None)
    track.local_path.write_bytes(OGG)
    downloader = Mock(return_value=track if kind == "track" else ("Demo Album", [track]))
    monkeypatch.setattr(spotify, f"download_spotify_{kind}", downloader)

    result = await DownloaderManager().download_media(f"https://open.spotify.com/{kind}/{item_id}?si=example")

    downloader.assert_called_once_with(item_id)
    assert result.success
    assert result.downloader_used is DownloaderType.SPOTIFY
    assert result.files == [track.local_path]
    assert result.caption == ("Home Demo - Artist" if kind == "track" else "Demo Album")
