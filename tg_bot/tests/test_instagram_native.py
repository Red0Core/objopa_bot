"""Pure/offline contract tests for additive native API integration.

The original Web downloader stays untouched. Fixtures intentionally contain
no Bearer tokens, usernames or real CDN URLs from personal HAR captures.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import pytest


@pytest.fixture
def native(monkeypatch, tmp_path):
    root = Path(__file__).resolve().parents[1] / "downloaders" / "instagram_native.py"
    core_config = types.ModuleType("core.config")
    core_config.DOWNLOADS_DIR = tmp_path
    core_redis = types.ModuleType("core.redis_client")
    async def redis():
        raise RuntimeError("Redis offline")
    core_redis.get_redis = redis

    @dataclass
    class Media:
        url: str
        is_video: bool
        index: int

    @dataclass
    class Post:
        shortcode: str
        canonical_url: str
        caption: str | None
        media: list[Media]
        download_id: str | None = None
        complete_metadata: bool = False

    @dataclass
    class Target:
        kind: str
        code: str
        url: str
        username: str = ""

    def target(url):
        p = urlsplit(url)
        if p.hostname != "www.instagram.com":
            raise ValueError("invalid target")
        path = p.path.strip('/').split('/')
        if len(path) == 2 and path[0] in {'p', 'reel', 'tv'}:
            return Target('reel' if path[0] == 'reel' else 'post', path[1], url)
        if len(path) == 3 and path[0] == 'stories' and path[2].isdigit():
            return Target('story', path[2], url, path[1])
        return Target('share', '', url)

    def cdn(url):
        p = urlsplit(url)
        h = p.hostname or ''
        return p.scheme == 'https' and p.port in (None, 443) and h in {'cdninstagram.com', 'img.cdninstagram.com'} and not p.username and not p.password

    web = types.ModuleType("tg_bot.downloaders.instagram_web_parser")
    web.InstagramMedia = Media
    web.InstagramPost = Post
    web._parse_target = target
    web._cdn_host = cdn
    class Sink:
        last = None
        def __init__(self, **kwargs):
            self.settings = kwargs
        async def _download_files(self, session, post, directory):
            Sink.last = (session, post, directory, self.settings)
            post.download_id = post.shortcode + '_unique'
            return post
    web.InstagramWebDownloader = Sink
    monkeypatch.setitem(sys.modules, "core.config", core_config)
    monkeypatch.setitem(sys.modules, "core.redis_client", core_redis)
    monkeypatch.setitem(sys.modules, "tg_bot.downloaders.instagram_web_parser", web)
    spec = importlib.util.spec_from_file_location('instagram_native_isolated_test', root)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._sink = Sink
    return module


def photo(url="https://img.cdninstagram.com/x.jpg"):
    return {'media_type': 1, 'image_versions2': {'candidates': [{'url': url, 'width': 800, 'height': 800}]}}


def video():
    return {'media_type': 2, 'video_versions': [
        {'url': 'https://cdninstagram.com/a.mp4', 'width': 400, 'height': 600},
        {'url': 'https://cdninstagram.com/b.mp4', 'width': 1080, 'height': 1920},
        {'url': 'https://cdninstagram.com/unsafe.mp4', 'width': 8000, 'height': 8000},
    ]}


def test_reel_selects_video_not_thumbnail(native):
    node = video() | {'code': 'Short_123', 'image_versions2': photo()['image_versions2'], 'caption': {'text': 'ok'}}
    result = native.parse_post({'status': 'ok', 'media': node}, 'Short_123', 'https://www.instagram.com/reel/Short_123/')
    assert len(result.media) == 1
    assert result.media[0].is_video
    assert result.media[0].url.endswith('/unsafe.mp4')  # max resolution when safe
    assert result.caption == 'ok'


def test_reel_rejects_preview_only(native):
    node = {'media_type': 2, 'code': 'Abc', 'image_versions2': photo()['image_versions2']}
    with pytest.raises(native.NativeInstagramError):
        native.parse_post({'status': 'ok', 'media': node}, 'Abc', 'https://www.instagram.com/reel/Abc/')


def test_photo(native):
    result = native.parse_post({'status': 'ok', 'media': photo() | {'code': 'Abc'}}, 'Abc', 'x')
    assert len(result.media) == 1 and not result.media[0].is_video


def test_rejects_mismatched_shortcode(native):
    with pytest.raises(native.NativeInstagramError, match='identity mismatch'):
        native.parse_post({'status': 'ok', 'media': photo() | {'code': 'Wrong'}}, 'Right', 'x')


def test_carousel_preserves_order(native):
    node = {'code': 'Abc', 'media_type': 8, 'carousel_media': [photo(), video(), photo('https://img.cdninstagram.com/z.jpg')]}
    result = native.parse_post({'status': 'ok', 'media': node}, 'Abc', 'x')
    assert [m.is_video for m in result.media] == [False, True, False]
    assert [m.index for m in result.media] == [1, 2, 3]


def test_carousel_atomic_on_missing_item(native):
    node = {'code': 'Abc', 'media_type': 8, 'carousel_media': [photo(), {'media_type': 2}]}
    with pytest.raises(native.NativeInstagramError):
        native.parse_post({'status': 'ok', 'media': node}, 'Abc', 'x')


def test_single_story_only(native):
    items = [video() | {'pk': 111}, video() | {'pk': 222}, video() | {'pk': 333}]
    post = native.parse_story({'status': 'ok', 'reel': {'items': items}}, '222', 'x')
    assert post.shortcode == '222'
    assert len(post.media) == 1
    assert post.media[0].url.endswith('/unsafe.mp4')


def test_missing_story_never_picks_first(native):
    with pytest.raises(native.NativeInstagramError, match='not present'):
        native.parse_story({'status': 'ok', 'reel': {'items': [video() | {'pk': 111}]}}, '999', 'x')


def test_story_strict_identity_not_substring(native):
    with pytest.raises(native.NativeInstagramError):
        native.parse_story({'status': 'ok', 'reel': {'items': [video() | {'pk': 1234}]}}, '123', 'x')


def test_invalid_cdn_rejected(native):
    bad = {'media_type': 1, 'image_versions2': {'candidates': [{'url': 'https://localhost/internal'}]}}
    with pytest.raises(native.NativeInstagramError):
        native._extract_item(bad, 1)


class FakeResponse:
    def __init__(self, status_code=200, content=b'{"status": "ok"}'):
        self.status_code = status_code
        self.content = content


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []
    async def get_async(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_minimal_headers_and_fixed_domain(native):
    session = FakeSession(FakeResponse())
    client = native.NativeInstagramClient(session, 'dummy-not-real')
    assert asyncio.run(client._get('/api/v1/clips/item/?clips_media_shortcode=ABC'))['status'] == 'ok'
    url, kwargs = session.calls[0]
    assert url.startswith('https://i.instagram.com/api/v1/clips/item/')
    assert kwargs['allow_redirects'] is False
    assert kwargs['headers']['X-IG-App-ID'] == native.APP_ID
    assert kwargs['headers']['Authorization'] == 'Bearer dummy-not-real'


def test_http_429(native):
    client = native.NativeInstagramClient(FakeSession(FakeResponse(429)), 'test-token')
    with pytest.raises(native.NativeRateLimited):
        asyncio.run(client._get('/api/v1/feed/user/123/story/'))


def test_http_200_status_fail(native):
    client = native.NativeInstagramClient(FakeSession(FakeResponse(200, b'{"status":"fail"}')), 'test-token')
    with pytest.raises(native.NativeInstagramError):
        asyncio.run(client._get('/api/v1/feed/user/123/story/'))


def test_redirect_rejected(native):
    client = native.NativeInstagramClient(FakeSession(FakeResponse(302)), 'test-token')
    with pytest.raises(native.NativeInstagramError):
        asyncio.run(client._get('/api/v1/feed/user/123/story/'))


def test_error_does_not_echo_secret(native):
    client = native.NativeInstagramClient(FakeSession(RuntimeError('Bearer SENSITIVE_TEST_SECRET')), 'SENSITIVE_TEST_SECRET')
    with pytest.raises(native.NativeInstagramError) as err:
        asyncio.run(client._get('/api/v1/feed/user/123/story/'))
    assert 'SENSITIVE_TEST_SECRET' not in str(err.value)


def test_profile_then_exact_story(native):
    class Session:
        calls = []
        async def get_async(self, url, **kwargs):
            self.calls.append(url)
            if '/usernameinfo/' in url:
                return FakeResponse(200, b'{"status":"ok", "user":{"pk":48686554188}}')
            return FakeResponse(200, b'{"status":"ok", "reel":{"items":[{"pk":111,"media_type":1,"image_versions2":{"candidates":[{"url":"https://img.cdninstagram.com/x.jpg"}]}},{"pk":222,"media_type":1,"image_versions2":{"candidates":[{"url":"https://img.cdninstagram.com/y.jpg"}]}}]}}')
    s = Session()
    post = asyncio.run(native.NativeInstagramClient(s, 'test-token').story('test_user', '222', 'x'))
    assert post.media[0].url.endswith('y.jpg')
    assert '/users/test_user/usernameinfo/' in s.calls[0]
    assert '/feed/user/48686554188/story/' in s.calls[1]


def test_unsafe_user_identifier(native):
    client = native.NativeInstagramClient(FakeSession(FakeResponse()), 'token')
    with pytest.raises(native.NativeUnsupported):
        asyncio.run(client.story('../admin', '123', 'x'))


def test_no_bearer_redis_unavailable(native):
    assert asyncio.run(native.get_native_bearer()) is None


def test_shares_unsupported_without_session(native, monkeypatch):
    fake_http = types.ModuleType('httpcloak')
    class Session:
        def __init__(self, **kwargs):
            raise AssertionError('Session should never be created for unhandled links')
    fake_http.Session = Session
    monkeypatch.setitem(sys.modules, 'httpcloak', fake_http)
    with pytest.raises(native.NativeUnsupported):
        asyncio.run(native.download_instagram_native_media('https://www.instagram.com/share/reel/foo', 'test-token'))


def test_native_uses_existing_sink(native, monkeypatch):
    content = b'{"status":"ok", "media":{"code":"ABC","media_type":1,"image_versions2":{"candidates":[{"url":"https://img.cdninstagram.com/x.jpg"}]}}}'
    class Session(FakeSession):
        def __init__(self, **kwargs):
            super().__init__(FakeResponse(200, content))
            self.settings = kwargs
            self.closed = False
        def close(self):
            self.closed = True
    fake_http = types.ModuleType('httpcloak')
    fake_http.Session = Session
    monkeypatch.setitem(sys.modules, 'httpcloak', fake_http)
    prefix = asyncio.run(native.download_instagram_native_media('https://www.instagram.com/p/ABC/', 'test-token'))
    assert prefix == 'ABC_unique'
    session, post, directory, cfg = native._sink.last
    assert session.closed is True
    assert session.settings['preset'] == native.NATIVE_PRESET
    assert cfg['user_agent'] == native.NATIVE_USER_AGENT
