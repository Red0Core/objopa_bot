"""Minimal Instagram Android JSON API adapter.

This module is additive: the existing browser downloader remains the fallback.
Only observed, VPS-tested GET endpoints are used.  No credentials are logged.
The existing InstagramWebDownloader handles CDN safety, size limits, and files.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

from core.config import DOWNLOADS_DIR
from core.redis_client import get_redis
from tg_bot.downloaders.instagram_web_parser import (
    InstagramMedia,
    InstagramPost,
    InstagramWebDownloader,
    _parse_target,
)

TOKEN_KEY = "instagram:native:bearer"
GLOBAL_COOLDOWN_KEY = "instagram:global:cooldown"
NATIVE_PRESET = "chrome-155-android"  # Not a verified fingerprint of the native app.
NATIVE_USER_AGENT = (
    "Instagram 453.0.0.0.56 Android (36/16; 560dpi; 1240x2772; "
    "realme; RMX3709; REE2B2L1; qcom; ru_RU; 1095477034)"
)
APP_ID = "567067343352427"
MAX_API_BYTES = 4_000_000
USER_RE = re.compile(r"[A-Za-z0-9._]{1,30}\Z")
SHORTCODE_RE = re.compile(r"[A-Za-z0-9_-]{1,32}\Z")


class NativeInstagramError(Exception):
    """Sanitized, safe-to-log native metadata/CDN failure."""


class NativeRateLimited(NativeInstagramError):
    """Must NOT trigger immediate fallback/retry against Instagram."""


class NativeUnsupported(NativeInstagramError):
    """Unsupported by native adapter; try browser implementation."""


def _headers(bearer: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {bearer}",
        "User-Agent": NATIVE_USER_AGENT,
        "X-IG-App-ID": APP_ID,
        "Accept": "application/json",
        "Accept-Language": "ru-RU, en-US",
    }


def _safe_media_url(url: Any) -> bool:
    # The existing downloader enforces the same boundary again before egress.
    from tg_bot.downloaders.instagram_web_parser import _cdn_host

    return isinstance(url, str) and _cdn_host(url)


def _best_url(versions: Any) -> str | None:
    if not isinstance(versions, list):
        return None
    candidates = [v for v in versions if isinstance(v, dict) and _safe_media_url(v.get("url"))]
    if not candidates:
        return None
    def score(item: dict[str, Any]) -> tuple[int, int]:
        try:
            width, height = int(item.get("width") or 0), int(item.get("height") or 0)
            return max(width, 0) * max(height, 0), int(item.get("bandwidth") or 0)
        except (ValueError, TypeError):
            return (0, 0)
    return max(candidates, key=score)["url"]


def _extract_item(node: Any, index: int) -> InstagramMedia:
    if not isinstance(node, dict):
        raise NativeInstagramError("Malformed Instagram media item")
    media_type = node.get("media_type")
    # Media type=8 is only meaningful as a container. Avoid downloading its cover.
    if media_type == 2:
        url = _best_url(node.get("video_versions"))
        if not url:
            raise NativeInstagramError("Video metadata has no supported CDN URL")
        return InstagramMedia(url=url, is_video=True, index=index)
    if media_type == 1:
        images = node.get("image_versions2")
        url = _best_url(images.get("candidates") if isinstance(images, dict) else None)
        if not url:
            raise NativeInstagramError("Image metadata has no supported CDN URL")
        return InstagramMedia(url=url, is_video=False, index=index)
    raise NativeInstagramError("Unsupported Instagram media type")


def _extract_media(node: Any) -> list[InstagramMedia]:
    if not isinstance(node, dict):
        raise NativeInstagramError("Missing Instagram media")
    if node.get("media_type") == 8:
        children = node.get("carousel_media")
        if not isinstance(children, list) or not children:
            raise NativeInstagramError("Empty Instagram carousel")
        # Fail atomically instead of silently returning an incomplete carousel.
        return [_extract_item(child, i) for i, child in enumerate(children, start=1)]
    return [_extract_item(node, 1)]


def _caption(node: dict[str, Any]) -> str | None:
    caption = node.get("caption")
    value = caption.get("text") if isinstance(caption, dict) else caption
    return value[:8000] if isinstance(value, str) and value else None


def parse_post(data: Any, shortcode: str, canonical_url: str) -> InstagramPost:
    if not isinstance(data, dict) or data.get("status") != "ok":
        raise NativeInstagramError("Instagram post API declined request")
    node = data.get("media")
    if not isinstance(node, dict) or str(node.get("code") or "") != shortcode:
        raise NativeInstagramError("Instagram post identity mismatch")
    return InstagramPost(
        shortcode=shortcode,
        canonical_url=canonical_url,
        caption=_caption(node),
        media=_extract_media(node),
        complete_metadata=True,
    )


def parse_story(data: Any, story_id: str, canonical_url: str) -> InstagramPost:
    if not isinstance(data, dict) or data.get("status") != "ok":
        raise NativeInstagramError("Instagram story API declined request")
    reel = data.get("reel")
    items = reel.get("items") if isinstance(reel, dict) else None
    if not isinstance(items, list):
        raise NativeInstagramError("Instagram returned no story list")
    node = next((item for item in items if isinstance(item, dict) and str(item.get("pk")) == story_id), None)
    if node is None:
        raise NativeInstagramError("Requested Story not present in available stories")
    # Story ID may be very long; it is safe because target parser accepts digits only.
    return InstagramPost(
        shortcode=story_id,
        canonical_url=canonical_url,
        caption=_caption(node),
        media=_extract_media(node),
        complete_metadata=True,
    )


class NativeInstagramClient:
    def __init__(self, session: Any, bearer: str):
        self.session = session
        self.headers = _headers(bearer)

    async def _get(self, path: str) -> dict[str, Any]:
        # Paths are internally built from validated input. Never forward arbitrary URLs.
        if not path.startswith("/api/v1/") or "//" in path or "\\" in path:
            raise NativeInstagramError("Invalid API path")
        try:
            response = await self.session.get_async(
                "https://i.instagram.com" + path,
                headers=self.headers,
                allow_redirects=False,
            )
            status = int(response.status_code)
            if status == 429:
                raise NativeRateLimited("Instagram Native API HTTP 429")
            if status != 200:
                raise NativeInstagramError(f"Native API HTTP {status}")
            content = response.content
            if len(content) > MAX_API_BYTES:
                raise NativeInstagramError("Instagram metadata response too large")
            data = json.loads(content)
            if not isinstance(data, dict):
                raise NativeInstagramError("Unexpected Instagram API payload")
            if data.get("status") != "ok":
                # A 200 response can still mean status=fail/rate-limited.
                reason = str(data.get("error_type") or "").lower()
                if any(marker in reason for marker in ("rate_limit", "throttl", "spam")):
                    raise NativeRateLimited("Instagram native rate limit")
                raise NativeInstagramError("Native API responded with status != ok")
            return data
        except (NativeInstagramError, NativeRateLimited):
            raise
        except (ValueError, TypeError, UnicodeDecodeError):
            raise NativeInstagramError("Invalid Instagram JSON response") from None
        except Exception as exc:
            # Do not log the exception: transports can include request headers/Authorization.
            raise NativeInstagramError("Instagram native transport failure") from None

    async def post(self, shortcode: str, canonical_url: str) -> InstagramPost:
        if not SHORTCODE_RE.fullmatch(shortcode):
            raise NativeUnsupported("Unsupported shortcode")
        body = await self._get("/api/v1/clips/item/?clips_media_shortcode=" + quote(shortcode, safe=""))
        return parse_post(body, shortcode, canonical_url)

    async def story(self, username: str, story_id: str, canonical_url: str) -> InstagramPost:
        if (
            not USER_RE.fullmatch(username)
            or username.startswith(".") or username.endswith(".") or ".." in username
            or not story_id.isascii() or not story_id.isdecimal() or len(story_id) > 32
        ):
            raise NativeUnsupported("Unsupported Story URL")
        profile = await self._get(f"/api/v1/users/{quote(username, safe='')}/usernameinfo/")
        user = profile.get("user")
        if not isinstance(user, dict):
            raise NativeInstagramError("Instagram user not found")
        user_id = str(user.get("pk") or user.get("pk_id") or user.get("id") or "")
        if not user_id.isascii() or not user_id.isdecimal():
            raise NativeInstagramError("Instagram user ID missing")
        data = await self._get(f"/api/v1/feed/user/{user_id}/story/")
        return parse_story(data, story_id, canonical_url)


async def get_native_bearer() -> str | None:
    try:
        redis = await get_redis()
        token = await redis.get(TOKEN_KEY)
    except Exception:
        # Redis failure should not break the existing Web downloader.
        return None
    if isinstance(token, bytes):
        token = token.decode("utf-8", errors="ignore")
    return token if isinstance(token, str) and 10 <= len(token) <= 4096 and not any(c.isspace() for c in token) else None


async def download_instagram_native_media(url: str, token: str) -> str:
    """Return unique file prefix compatible with DownloaderManager's collector.

    Raises NativeUnsupported for shares, Highlights, and story trays so Web can
    handle them unchanged. Use the existing WebDownloader's CDN sink only.
    """
    from httpcloak import Session

    target = _parse_target(url)
    if target.kind not in {"reel", "post", "story"}:
        raise NativeUnsupported("No confirmed native endpoint for this URL")
    # This is an Android Chromium TLS/H2 preset, not a claim to reproduce the
    # native Instagram networking stack's unknown TLS fingerprint.
    session = Session(
        preset=NATIVE_PRESET, timeout=20, retry=0, allow_redirects=False,
        prefer_ipv4=True, disable_http3=True
    )
    try:
        client = NativeInstagramClient(session, token)
        if target.kind == "story":
            post = await client.story(target.username, target.code, target.url)
        else:
            post = await client.post(target.code, target.url)
        # Retain the battle-tested Web sink: CDN allowlist, streaming, byte cap,
        # file signatures, unique names, and cleanup. Only metadata source changes.
        sink = InstagramWebDownloader(user_agent=NATIVE_USER_AGENT, http_preset=NATIVE_PRESET)
        try:
            saved = await sink._download_files(session, post, DOWNLOADS_DIR)
        except NativeRateLimited:
            raise
        except Exception as exc:
            # Preserve CDN 429 as a non-fallback condition.
            from tg_bot.downloaders.instagram_web_parser import InstagramRateLimitedError
            if isinstance(exc, InstagramRateLimitedError):
                raise NativeRateLimited("Instagram CDN HTTP 429") from None
            raise NativeInstagramError("Native media CDN download failed") from None
        if not saved.download_id:
            raise NativeInstagramError("Native download returned no file prefix")
        return saved.download_id
    finally:
        session.close()


async def mark_native_rate_limited() -> None:
    try:
        redis = await get_redis()
        await redis.setex(GLOBAL_COOLDOWN_KEY, 900, "1")
    except Exception:
        pass
