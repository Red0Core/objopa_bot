import asyncio
import html
import json
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpcloak

from core.config import DOWNLOADS_DIR
from core.logger import logger
from core.memory import MAX_MEDIA_BYTES, MediaTooLargeError, trim_memory, unlink_quietly

INSTAGRAM_WEB_APP_ID = "936619743392459"
INSTAGRAM_ANDROID_APP_ID = "567067343352427"
REQUEST_TIMEOUT = 20
SHORTCODE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".webm")
REQUEST_RETRIES = 1
INSTAGRAM_ANDROID_HTTP_PRESET = "android-chrome-latest"
SESSION_COOKIE_NAMES = {
    "sessionid",
    "ds_user_id",
    "ds_user",
    "csrftoken",
    "mid",
    "ig_did",
    "datr",
    "rur",
    "ig_nrcb",
}
ANDROID_APP_USER_AGENT = (
    "Instagram 386.0.0.46.84 Android (34/14; 640dpi; 1440x2912; realme; RMX3301; RED8ACL1; qcom; en_US; 727763711)"
)
STORIES_HIGHLIGHT_RE = re.compile(r"/stories/highlights/(\d+)", re.I)
STORIES_ITEM_RE = re.compile(r"/stories/([A-Za-z0-9._]+)/(\d+)", re.I)
STORIES_USER_RE = re.compile(r"/stories/([A-Za-z0-9._]+)/?(?:[?#]|$)", re.I)


class InstagramDownloadError(Exception):
    """Base Instagram downloader error."""


class InstagramAuthRequiredError(InstagramDownloadError):
    """Instagram requires authentication for the requested media."""


class InstagramRateLimitedError(InstagramDownloadError):
    """Instagram blocked or rate-limited the request."""


class InstagramUnsupportedUrlError(InstagramDownloadError):
    """The Instagram URL shape is not supported by this downloader."""


class InstagramNoMediaError(InstagramDownloadError):
    """No downloadable media was found in Instagram responses."""


@dataclass(slots=True)
class InstagramMedia:
    url: str
    is_video: bool
    index: int


@dataclass(slots=True)
class InstagramPost:
    shortcode: str
    canonical_url: str
    caption: str | None
    media: list[InstagramMedia]


@dataclass(slots=True)
class InstagramHttpResponse:
    status_code: int
    url: str
    text: str
    content: bytes
    headers: dict[str, str]


class InstagramHttpSession:
    def __init__(self, preset: str = INSTAGRAM_ANDROID_HTTP_PRESET):
        self._session = httpcloak.Session(
            preset=preset,
            timeout=REQUEST_TIMEOUT,
            retry=REQUEST_RETRIES,
            prefer_ipv4=True,
            local_address="0.0.0.0",
            http_version="auto",
        )

    async def __aenter__(self) -> "InstagramHttpSession":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    async def get(
        self,
        url: str,
        *,
        allow_redirects: bool = True,
        headers: dict[str, str] | None = None,
    ) -> InstagramHttpResponse:
        request_headers = headers or {}
        try:
            response = await self._session.get_async(
                url,
                headers=request_headers,
                allow_redirects=allow_redirects,
            )
        except TypeError:
            response = await self._session.get_async(url, headers=request_headers)
        except Exception as exc:
            raise InstagramDownloadError(f"Instagram request failed: {exc}") from exc
        return self._normalize_response(response)

    def stream_to_file(
        self,
        url: str,
        dest: Path,
        *,
        headers: dict[str, str] | None = None,
        max_bytes: int = MAX_MEDIA_BYTES,
    ) -> int:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.name}.{secrets.token_hex(3)}.part")
        written = 0
        response = self._session.get_stream(url, headers=headers or {})
        try:
            status = int(getattr(response, "status_code", 0) or 0)
            if status >= 400:
                raise InstagramDownloadError(f"CDN HTTP {status}")
            raw_headers = self._normalize_headers(getattr(response, "headers", {}) or {})
            content_length = raw_headers.get("content-length")
            if content_length and int(content_length) > max_bytes:
                raise MediaTooLargeError(int(content_length))
            with tmp.open("wb") as handle:
                for chunk in response.iter_content(64 * 1024):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > max_bytes:
                        raise MediaTooLargeError(written)
                    handle.write(chunk)
            if written <= 0:
                raise InstagramDownloadError("empty media body")
            tmp.replace(dest)
            return written
        except Exception:
            unlink_quietly(tmp)
            raise
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

    def close(self) -> None:
        self._session.close()

    def load_netscape_cookies(self, cookies_path: Path) -> None:
        try:
            lines = cookies_path.read_text(encoding="utf-8").splitlines()
        except Exception as exc:
            logger.warning(f"Failed to read Instagram cookies for HTTPCloak session: {exc}")
            return

        for line in lines:
            if not line.strip() or (line.startswith("#") and not line.startswith("#HttpOnly_")):
                continue
            http_only = line.startswith("#HttpOnly_")
            if http_only:
                line = line.removeprefix("#HttpOnly_")
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            domain, _, path, secure, _, name, value = parts[:7]
            if name not in SESSION_COOKIE_NAMES:
                continue
            try:
                self._session.set_cookie(
                    name,
                    value,
                    domain=domain,
                    path=path or "/",
                    secure=secure.upper() == "TRUE",
                    http_only=http_only,
                )
            except Exception as exc:
                logger.debug(f"Failed to load Instagram cookie {name!r}: {exc}")

    def _normalize_response(self, response: Any) -> InstagramHttpResponse:
        headers = self._normalize_headers(getattr(response, "headers", {}) or {})
        return InstagramHttpResponse(
            status_code=int(response.status_code),
            url=str(response.url),
            text=str(response.text or ""),
            content=b"",
            headers=headers,
        )

    def _normalize_headers(self, headers: Any) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for key, value in dict(headers).items():
            if isinstance(value, list):
                normalized[str(key).lower()] = value[-1] if value else ""
            else:
                normalized[str(key).lower()] = str(value)
        return normalized


def get_httpcloak_preset_name(user_agent: str | None = None) -> str:
    # TLS fingerprint must stay Android Chrome. Do not mix in Windows Chrome.
    _ = user_agent
    return INSTAGRAM_ANDROID_HTTP_PRESET


class InstagramWebDownloader:
    """Instagram downloader via httpcloak android-chrome-latest. Guest first, cookies on demand."""

    def __init__(self, user_agent: str, http_preset: str = INSTAGRAM_ANDROID_HTTP_PRESET):
        self.user_agent = user_agent or ANDROID_APP_USER_AGENT
        self.http_preset = http_preset or INSTAGRAM_ANDROID_HTTP_PRESET
        self._cookie_slot: str | None = None

    async def download(self, url: str, download_path: Path = DOWNLOADS_DIR, use_cookies: bool = False) -> InstagramPost:
        download_path.mkdir(parents=True, exist_ok=True)
        cookie_path: Path | None = None
        self._cookie_slot = None

        try:
            async with InstagramHttpSession(self.http_preset) as session:
                await self._warmup(session)
                target = self._parse_target(url)
                if target["kind"] == "unknown":
                    target = await self._resolve_share_target(session, url)

                post = await self._load_target(session, target)
                needs_auth = self._needs_auth(target, post)
                if needs_auth and use_cookies:
                    from tg_bot.utils.cookies_manager import cookies_manager

                    pooled = await cookies_manager.get_pooled_cookies("instagram")
                    if pooled:
                        self._cookie_slot, cookie_path = pooled
                        session.load_netscape_cookies(cookie_path)
                        logger.info(f"Instagram web parser using cookie slot {self._cookie_slot}")
                        post = await self._load_target(session, target)
                        needs_auth = self._needs_auth(target, post)

                if not post.media:
                    if needs_auth:
                        raise InstagramAuthRequiredError(
                            "Instagram не отдал медиа без авторизации. "
                            "Загрузи cookies через /set_cookies и повтори /d <url>."
                        )
                    raise InstagramNoMediaError("Instagram did not expose downloadable media for this post.")

                self._cleanup_old_files(download_path, post.shortcode)
                downloaded = await self._download_media_files(session, post, download_path)
                if not downloaded:
                    raise InstagramNoMediaError("Instagram media links were found, but files were not downloaded.")
                if post.caption:
                    (download_path / f"{post.shortcode}.txt").write_text(post.caption, encoding="utf-8")
                return post
        except (InstagramAuthRequiredError, InstagramRateLimitedError) as exc:
            if self._cookie_slot and isinstance(exc, InstagramRateLimitedError):
                from tg_bot.utils.cookies_manager import cookies_manager

                await cookies_manager.mark_cookie_cooldown(self._cookie_slot)
            if self._cookie_slot and "HTTP 403" in str(exc):
                from tg_bot.utils.cookies_manager import cookies_manager

                await cookies_manager.mark_cookie_cooldown(self._cookie_slot)
            raise
        finally:
            if cookie_path and cookie_path.exists():
                cookie_path.unlink(missing_ok=True)

    def _parse_target(self, url: str) -> dict[str, str]:
        highlight = STORIES_HIGHLIGHT_RE.search(url)
        if highlight:
            return {"kind": "highlight", "id": highlight.group(1), "url": url}
        story_item = STORIES_ITEM_RE.search(url)
        if story_item and story_item.group(1).lower() != "highlights":
            return {
                "kind": "story_item",
                "user": story_item.group(1),
                "id": story_item.group(2),
                "url": url,
            }
        story_user = STORIES_USER_RE.search(url)
        if story_user and story_user.group(1).lower() != "highlights":
            return {"kind": "story_user", "user": story_user.group(1), "url": url}

        match = re.search(r"/(p|reel|tv)/([\w-]+)", url)
        if match:
            media_type, shortcode = match.groups()
            return {
                "kind": "post",
                "type": media_type,
                "shortcode": shortcode,
                "url": f"https://www.instagram.com/{media_type}/{shortcode}/",
            }
        return {"kind": "unknown", "url": url}

    async def _resolve_share_target(self, session: InstagramHttpSession, url: str) -> dict[str, str]:
        response = await self._request(session, url, allow_redirects=True)
        parsed = self._parse_target(str(response.url))
        if parsed["kind"] == "unknown":
            raise InstagramUnsupportedUrlError("Could not extract Instagram shortcode from the URL.")
        return parsed

    def _needs_auth(self, target: dict[str, str], post: InstagramPost) -> bool:
        if not post.media:
            return True
        if target["kind"] in {"story_item", "story_user", "highlight"}:
            return not post.media
        if target.get("type") == "reel" and not any(item.is_video for item in post.media):
            return True
        return False

    async def _warmup(self, session: InstagramHttpSession) -> None:
        try:
            await session.get("https://www.instagram.com/", headers=self._html_headers())
        except Exception as exc:
            logger.debug(f"Instagram warmup failed: {exc}")

    async def _load_target(self, session: InstagramHttpSession, target: dict[str, str]) -> InstagramPost:
        if target["kind"] in {"story_item", "story_user", "highlight"}:
            return await self._load_stories(session, target)
        return await self._load_post(session, target["shortcode"], target["url"])

    async def _load_stories(self, session: InstagramHttpSession, target: dict[str, str]) -> InstagramPost:
        reel_ids: str
        prefix: str
        if target["kind"] == "highlight":
            reel_ids = f"highlight:{target['id']}"
            prefix = f"highlight_{target['id']}"
            canonical = f"https://www.instagram.com/stories/highlights/{target['id']}/"
        else:
            username = target["user"]
            user_id = await self._lookup_user_id(session, username)
            if not user_id:
                raise InstagramAuthRequiredError(
                    "Не удалось получить user id для сторис. Нужны cookies через /set_cookies."
                )
            reel_ids = user_id
            prefix = f"stories_{username}"
            if target.get("id"):
                prefix = target["id"]
            canonical = f"https://www.instagram.com/stories/{username}/"

        items = await self._fetch_reel_items(session, reel_ids, canonical)
        if target.get("id") and target["kind"] == "story_item":
            wanted = target["id"]
            items = [item for item in items if str(item.get("pk") or item.get("id") or "").startswith(wanted)]

        media: list[InstagramMedia] = []
        caption: str | None = None
        for node in items:
            if caption is None:
                caption = self._extract_caption(node)
            media.extend(self._extract_media_from_node(node))

        unique = self._dedupe_media(media)
        return InstagramPost(shortcode=prefix, canonical_url=canonical, caption=caption, media=unique)

    async def _lookup_user_id(self, session: InstagramHttpSession, username: str) -> str | None:
        url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
        try:
            response = await self._request(
                session, url, headers=self._json_headers(f"https://www.instagram.com/{username}/")
            )
        except InstagramDownloadError as exc:
            logger.debug(f"web_profile_info failed for {username}: {exc}")
            return None
        parsed = self._try_load_json(response.text)
        if not isinstance(parsed, dict):
            return None
        user = (parsed.get("data") or {}).get("user") or {}
        user_id = user.get("id") or user.get("pk")
        return str(user_id) if user_id else None

    async def _fetch_reel_items(
        self, session: InstagramHttpSession, reel_ids: str, referer: str
    ) -> list[dict[str, Any]]:
        urls = (
            f"https://www.instagram.com/api/v1/feed/reels_media/?reel_ids={reel_ids}",
            f"https://i.instagram.com/api/v1/feed/reels_media/?reel_ids={reel_ids}",
        )
        for api_url in urls:
            headers = self._json_headers(referer)
            if "i.instagram.com" in api_url:
                headers = self._android_app_headers(referer)
            try:
                response = await self._request(session, api_url, headers=headers)
            except InstagramDownloadError as exc:
                logger.debug(f"reels_media failed {api_url}: {exc}")
                continue
            parsed = self._try_load_json(response.text)
            items = self._reel_items_from_payload(parsed)
            if items:
                return items
        return []

    def _reel_items_from_payload(self, parsed: Any) -> list[dict[str, Any]]:
        if not isinstance(parsed, dict):
            return []
        items: list[dict[str, Any]] = []
        reels = parsed.get("reels")
        if isinstance(reels, dict):
            for reel in reels.values():
                if isinstance(reel, dict):
                    batch = reel.get("items") or reel.get("media_ids") or []
                    if isinstance(batch, list):
                        items.extend(item for item in batch if isinstance(item, dict))
        tray = parsed.get("items")
        if isinstance(tray, list):
            items.extend(item for item in tray if isinstance(item, dict))
        return items

    async def _load_post(self, session: InstagramHttpSession, shortcode: str, canonical_url: str) -> InstagramPost:
        is_reel = "/reel/" in canonical_url
        empty = InstagramPost(shortcode=shortcode, canonical_url=canonical_url, caption=None, media=[])

        for embed_path in (
            f"https://www.instagram.com/reel/{shortcode}/embed/captioned/",
            f"https://www.instagram.com/p/{shortcode}/embed/captioned/",
            f"https://www.instagram.com/p/{shortcode}/embed/",
        ):
            try:
                html_text = (await self._request(session, embed_path)).text
            except InstagramDownloadError as exc:
                logger.debug(f"Instagram embed failed {embed_path}: {exc}")
                continue
            post = self._post_from_embed(html_text, shortcode, canonical_url)
            if self._usable_post(post, is_reel):
                return post

        media_id = self._shortcode_to_media_id(shortcode)
        for api_url, headers in (
            (
                f"https://www.instagram.com/api/v1/media/{media_id}/info/",
                self._json_headers(canonical_url),
            ),
            (
                f"https://i.instagram.com/api/v1/media/{media_id}/info/",
                self._android_app_headers(canonical_url),
            ),
        ):
            try:
                response = await self._request(session, api_url, headers=headers)
            except InstagramDownloadError as exc:
                logger.debug(f"media info failed {api_url}: {exc}")
                continue
            parsed = self._try_load_json(response.text)
            post = self._safe_post_from_json(parsed, shortcode, canonical_url)
            if self._usable_post(post, is_reel):
                return post

        try:
            html_text = await self._load_html(session, canonical_url)
        except InstagramDownloadError as exc:
            logger.debug(f"Instagram HTML page failed for {shortcode}: {exc}")
            html_text = None
        if html_text:
            if self._is_instagram_error_page(html_text):
                raise InstagramNoMediaError(
                    "Instagram returned an error page for this post. "
                    "It may be deleted, private, age-restricted, or unavailable for the current account."
                )
            meta_post = self._post_from_meta_tags(html_text, shortcode, canonical_url)
            raw_video_post = self._post_from_raw_video_urls(html_text, shortcode, canonical_url)
            for candidate in self._extract_json_candidates(html_text):
                post = self._safe_post_from_json(candidate, shortcode, canonical_url)
                post = self._with_fallback_caption(post, meta_post.caption)
                if self._usable_post(post, is_reel):
                    return post
            if self._usable_post(raw_video_post, is_reel):
                return self._with_fallback_caption(raw_video_post, meta_post.caption)
            if self._usable_post(meta_post, is_reel):
                return meta_post

        return empty

    def _usable_post(self, post: InstagramPost | None, is_reel: bool) -> bool:
        if not post or not post.media:
            return False
        if is_reel and not any(item.is_video for item in post.media):
            return False
        return True

    def _post_from_embed(self, html_text: str, shortcode: str, canonical_url: str) -> InstagramPost:
        raw = self._post_from_raw_video_urls(html_text, shortcode, canonical_url)
        meta = self._post_from_meta_tags(html_text, shortcode, canonical_url)
        json_post = InstagramPost(shortcode=shortcode, canonical_url=canonical_url, caption=None, media=[])
        for candidate in self._extract_json_candidates(html_text):
            parsed = self._safe_post_from_json(candidate, shortcode, canonical_url)
            if parsed.media:
                json_post = parsed
                break
        caption = json_post.caption or meta.caption
        media = json_post.media or raw.media or meta.media
        return InstagramPost(shortcode=shortcode, canonical_url=canonical_url, caption=caption, media=media)

    def _is_instagram_error_page(self, html_text: str) -> bool:
        error_markers = (
            "PolarisErrorRoot",
            "PolarisErrorRoute",
            '"pageID":"httpErrorPage"',
        )
        return any(marker in html_text for marker in error_markers)

    def _safe_post_from_json(self, data: Any, shortcode: str, canonical_url: str) -> InstagramPost:
        try:
            return self._post_from_json(data, shortcode, canonical_url)
        except (AttributeError, KeyError, TypeError, ValueError, IndexError) as exc:
            logger.debug(f"Skipping malformed Instagram JSON candidate for {shortcode}: {exc}")
            return InstagramPost(shortcode=shortcode, canonical_url=canonical_url, caption=None, media=[])

    async def _load_html(self, session: InstagramHttpSession, canonical_url: str) -> str:
        response = await self._request(session, canonical_url)
        return response.text

    async def _request(
        self,
        session: InstagramHttpSession,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        allow_redirects: bool = True,
    ) -> InstagramHttpResponse:
        try:
            response = await session.get(url, allow_redirects=allow_redirects, headers=headers)
        except InstagramDownloadError:
            raise
        except Exception as exc:
            raise InstagramDownloadError(f"Instagram request failed: {exc}") from exc

        if response.status_code in (401, 403):
            raise InstagramAuthRequiredError(f"Instagram returned HTTP {response.status_code}.")
        if response.status_code == 429:
            raise InstagramRateLimitedError("Instagram returned HTTP 429.")
        if response.status_code >= 400:
            raise InstagramDownloadError(f"Instagram returned HTTP {response.status_code}.")
        return response

    async def _download_media_files(
        self,
        session: InstagramHttpSession,
        post: InstagramPost,
        download_path: Path,
    ) -> list[Path]:
        downloaded_files: list[Path] = []
        media_headers = self._media_headers(post.canonical_url)

        for media in post.media:
            extension = self._guess_extension(media.url, None, media.is_video)
            final_path = download_path / f"{post.shortcode}_{media.index:02d}{extension}"
            try:
                await asyncio.to_thread(
                    session.stream_to_file,
                    media.url,
                    final_path,
                    headers=media_headers,
                    max_bytes=MAX_MEDIA_BYTES,
                )
            except MediaTooLargeError as exc:
                logger.warning(
                    f"Skipping Instagram media item {media.index}: {exc.size / (1024 * 1024):.1f}MB exceeds cap"
                )
                unlink_quietly(final_path)
                continue
            except Exception as exc:
                logger.warning(f"Failed to download Instagram media item {media.index}: {exc}")
                unlink_quietly(final_path)
                continue

            if not final_path.exists() or final_path.stat().st_size == 0:
                unlink_quietly(final_path)
                continue
            downloaded_files.append(final_path)

        trim_memory()
        return downloaded_files

    def _html_headers(self) -> dict[str, str]:
        # Let android-chrome-latest fill UA + client hints. Windows Sec-CH-UA was a fingerprint leak.
        return {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.instagram.com/",
            "Upgrade-Insecure-Requests": "1",
        }

    def _json_headers(self, referer: str) -> dict[str, str]:
        return {
            "Accept": "application/json,text/plain,*/*",
            "Referer": referer,
            "X-IG-App-ID": INSTAGRAM_WEB_APP_ID,
            "X-ASBD-ID": "129477",
            "X-Requested-With": "XMLHttpRequest",
        }

    def _android_app_headers(self, referer: str) -> dict[str, str]:
        return {
            "Accept": "application/json,text/plain,*/*",
            "User-Agent": self.user_agent if "Instagram" in self.user_agent else ANDROID_APP_USER_AGENT,
            "X-IG-App-ID": INSTAGRAM_ANDROID_APP_ID,
            "Referer": referer,
        }

    def _media_headers(self, referer: str) -> dict[str, str]:
        return {
            "Accept": "*/*",
            "Referer": referer or "https://www.instagram.com/",
        }

    def _post_from_json(self, data: Any, shortcode: str, canonical_url: str) -> InstagramPost:
        root_nodes = self._find_shortcode_nodes(data, shortcode)
        if not root_nodes:
            root_nodes = self._find_media_roots(data)

        media_items: list[InstagramMedia] = []
        caption: str | None = None
        for node in root_nodes:
            if caption is None:
                caption = self._extract_caption(node) or self._extract_caption_from_children(node)
            media_items.extend(self._extract_media_from_node(node))
        return InstagramPost(
            shortcode=shortcode,
            canonical_url=canonical_url,
            caption=caption,
            media=self._dedupe_media(media_items),
        )

    def _find_shortcode_nodes(self, data: Any, shortcode: str) -> list[dict[str, Any]]:
        nodes: list[dict[str, Any]] = []

        def walk(value: Any, depth: int = 0) -> None:
            if depth > 12:
                return
            if isinstance(value, dict):
                shortcode_media = value.get("shortcode_media") or value.get("xdt_shortcode_media")
                if isinstance(shortcode_media, dict):
                    nodes.append(shortcode_media)
                if value.get("shortcode") == shortcode or value.get("code") == shortcode:
                    nodes.append(value)
                for child in value.values():
                    walk(child, depth + 1)
            elif isinstance(value, list) and depth < 10:
                for item in value[:40]:
                    walk(item, depth + 1)

        walk(data)
        return nodes

    def _find_media_roots(self, data: Any) -> list[dict[str, Any]]:
        roots: list[dict[str, Any]] = []

        def walk(value: Any, depth: int = 0) -> None:
            if depth > 12:
                return
            if isinstance(value, dict):
                if self._node_has_media(value):
                    roots.append(value)
                    return
                for child in value.values():
                    walk(child, depth + 1)
            elif isinstance(value, list) and depth < 10:
                for item in value[:40]:
                    walk(item, depth + 1)

        walk(data)
        return roots

    def _extract_media_from_node(self, node: dict[str, Any]) -> list[InstagramMedia]:
        media_nodes = self._flatten_media_nodes(node)
        media_items: list[InstagramMedia] = []
        for index, media_node in enumerate(media_nodes, start=1):
            duration = self._safe_float(media_node.get("video_duration") or media_node.get("duration"))
            media_url = self._pick_video_url(media_node, duration=duration)
            if media_url:
                media_items.append(InstagramMedia(url=media_url, is_video=True, index=index))
                continue
            image_url = self._pick_image_url(media_node)
            if image_url:
                media_items.append(InstagramMedia(url=image_url, is_video=False, index=index))
        return media_items

    def _flatten_media_nodes(self, node: dict[str, Any]) -> list[dict[str, Any]]:
        for key in ("edge_sidecar_to_children", "edge_web_media_to_related_media"):
            edges = ((node.get(key) or {}).get("edges")) if isinstance(node.get(key), dict) else None
            if isinstance(edges, list) and edges:
                children = [edge.get("node") for edge in edges if isinstance(edge, dict)]
                return [child for child in children if isinstance(child, dict)]
        carousel_media = node.get("carousel_media")
        if isinstance(carousel_media, list) and carousel_media:
            return [item for item in carousel_media if isinstance(item, dict)]
        items = node.get("items")
        if isinstance(items, list) and items:
            return [item for item in items if isinstance(item, dict)]
        return [node]

    def _pick_video_url(self, node: dict[str, Any], *, duration: float = 0.0) -> str | None:
        video_versions = node.get("video_versions")
        if isinstance(video_versions, list):
            picked = self._pick_best_candidate_url(video_versions, duration=duration)
            if picked:
                return picked
        video_url = node.get("video_url")
        if isinstance(video_url, str):
            return html.unescape(video_url)
        return None

    def _pick_image_url(self, node: dict[str, Any]) -> str | None:
        image_versions2 = node.get("image_versions2")
        image_versions = image_versions2.get("candidates") if isinstance(image_versions2, dict) else None
        if isinstance(image_versions, list):
            candidate = self._pick_best_candidate_url(image_versions)
            if candidate:
                return candidate
        display_resources = node.get("display_resources") or node.get("thumbnail_resources")
        if isinstance(display_resources, list):
            candidate = self._pick_best_candidate_url(display_resources)
            if candidate:
                return candidate
        for key in ("display_url", "thumbnail_src", "url"):
            value = node.get(key)
            if isinstance(value, str) and self._looks_like_media_url(value):
                return html.unescape(value)
        return None

    def _pick_best_candidate_url(self, candidates: list[Any], *, duration: float = 0.0) -> str | None:
        ranked: list[tuple[int, int, str]] = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            url = candidate.get("url") or candidate.get("src")
            if not isinstance(url, str):
                continue
            width = self._safe_int(candidate.get("width") or candidate.get("config_width"))
            height = self._safe_int(candidate.get("height") or candidate.get("config_height"))
            bandwidth = self._safe_int(candidate.get("bandwidth") or candidate.get("bitrate"))
            estimated = 0
            if duration and bandwidth:
                estimated = int(bandwidth / 8 * duration)
            elif duration and width and height:
                estimated = int(width * height * duration * 0.07)
            if estimated and estimated > MAX_MEDIA_BYTES:
                continue
            ranked.append((width * height, estimated, html.unescape(url)))
        if not ranked:
            return None
        ranked.sort(key=lambda item: item[0], reverse=True)
        under_1080 = [item for item in ranked if item[0] <= 1920 * 1080]
        pool = under_1080 or ranked
        return pool[0][2]

    def _extract_caption(self, node: dict[str, Any]) -> str | None:
        caption = node.get("caption")
        if isinstance(caption, str):
            return caption.strip() or None
        if isinstance(caption, dict):
            caption_text = caption.get("text")
            if isinstance(caption_text, str):
                return caption_text.strip() or None
        for key in ("caption_text", "edge_media_to_caption"):
            value = node.get(key)
            if isinstance(value, str):
                return value.strip() or None
            if isinstance(value, dict):
                edges = value.get("edges")
                if isinstance(edges, list):
                    for edge in edges:
                        text = ((edge or {}).get("node") or {}).get("text") if isinstance(edge, dict) else None
                        if isinstance(text, str) and text.strip():
                            return text.strip()
        return None

    def _extract_caption_from_children(self, node: dict[str, Any]) -> str | None:
        for child in self._flatten_media_nodes(node):
            caption = self._extract_caption(child)
            if caption:
                return caption
        return None

    def _extract_json_candidates(self, html_text: str):
        yielded = 0
        max_blob = 1_500_000
        for match in re.finditer(
            r"<script[^>]*type=[\"']application/(?:json|ld\+json)[\"'][^>]*>(.*?)</script>",
            html_text,
            flags=re.IGNORECASE | re.DOTALL,
        ):
            raw = match.group(1)
            if not raw or len(raw) > max_blob:
                continue
            parsed = self._try_load_json(html.unescape(raw).strip())
            if parsed is not None:
                yield parsed
                yielded += 1
                if yielded >= 8:
                    return
        for marker in ("window._sharedData", "shortcode_media", "xdt_shortcode_media"):
            for json_text in self._extract_balanced_json_after_marker(html_text, marker):
                if len(json_text) > max_blob:
                    continue
                parsed = self._try_load_json(html.unescape(json_text))
                if parsed is not None:
                    yield parsed
                    yielded += 1
                    if yielded >= 8:
                        return

    def _extract_balanced_json_after_marker(self, text: str, marker: str) -> list[str]:
        chunks: list[str] = []
        start = 0
        while True:
            marker_index = text.find(marker, start)
            if marker_index == -1:
                break
            brace_index = text.find("{", marker_index)
            if brace_index == -1:
                break
            chunk = self._balanced_json_object(text, brace_index)
            if chunk:
                chunks.append(chunk)
            start = marker_index + len(marker)
            if len(chunks) >= 4:
                break
        return chunks

    def _balanced_json_object(self, text: str, start: int) -> str | None:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, min(len(text), start + 1_500_000)):
            char = text[index]
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start : index + 1]
        return None

    def _post_from_raw_video_urls(self, html_text: str, shortcode: str, canonical_url: str) -> InstagramPost:
        media_items: list[InstagramMedia] = []
        patterns = (
            r'"video_url"\s*:\s*"([^"]+)"',
            r'\\"video_url\\"\s*:\s*\\"(.+?)\\"',
            r'"playback_url"\s*:\s*"([^"]+)"',
            r'\\"playback_url\\"\s*:\s*\\"(.+?)\\"',
        )
        for pattern in patterns:
            for match in re.finditer(pattern, html_text, flags=re.IGNORECASE):
                video_url = self._decode_media_url(match.group(1))
                if self._looks_like_video_url(video_url):
                    media_items.append(InstagramMedia(url=video_url, is_video=True, index=len(media_items) + 1))
        return InstagramPost(
            shortcode=shortcode,
            canonical_url=canonical_url,
            caption=None,
            media=self._dedupe_media(media_items),
        )

    def _post_from_meta_tags(self, html_text: str, shortcode: str, canonical_url: str) -> InstagramPost:
        media: list[InstagramMedia] = []
        caption: str | None = None
        for match in re.finditer(
            r"<meta\s+[^>]*(?:property|name)=[\"'](?:og:|twitter:)?(video|image|description)[\"'][^>]*>",
            html_text,
            flags=re.IGNORECASE,
        ):
            content_match = re.search(r"content=[\"']([^\"']+)[\"']", match.group(0), flags=re.IGNORECASE)
            if not content_match:
                continue
            value = html.unescape(content_match.group(1))
            media_type = match.group(1).lower()
            if media_type == "video" and self._looks_like_media_url(value):
                media.append(InstagramMedia(url=value, is_video=True, index=len(media) + 1))
            elif media_type == "image" and self._looks_like_media_url(value):
                media.append(InstagramMedia(url=value, is_video=False, index=len(media) + 1))
            elif media_type == "description":
                caption = value.strip() or None
        return InstagramPost(
            shortcode=shortcode,
            canonical_url=canonical_url,
            caption=caption,
            media=self._dedupe_media(media),
        )

    def _with_fallback_caption(self, post: InstagramPost, caption: str | None) -> InstagramPost:
        if post.caption or not caption:
            return post
        return InstagramPost(
            shortcode=post.shortcode,
            canonical_url=post.canonical_url,
            caption=caption,
            media=post.media,
        )

    def _dedupe_media(self, media_items: list[InstagramMedia]) -> list[InstagramMedia]:
        unique_items: list[InstagramMedia] = []
        seen_urls: set[str] = set()
        for media in media_items:
            dedupe_key = media.url.split("?", maxsplit=1)[0]
            if dedupe_key in seen_urls:
                continue
            seen_urls.add(dedupe_key)
            unique_items.append(InstagramMedia(url=media.url, is_video=media.is_video, index=len(unique_items) + 1))
        return unique_items

    def _node_has_media(self, value: dict[str, Any]) -> bool:
        return any(
            key in value
            for key in (
                "video_url",
                "video_versions",
                "display_url",
                "display_resources",
                "image_versions2",
                "carousel_media",
                "edge_sidecar_to_children",
            )
        )

    def _looks_like_media_url(self, value: str) -> bool:
        value_lower = value.lower()
        return value_lower.startswith("http") and any(
            marker in value_lower for marker in ("cdninstagram", "fbcdn", ".cdn", "scontent")
        )

    def _looks_like_video_url(self, value: str) -> bool:
        return self._looks_like_media_url(value) and ".mp4" in value.lower()

    def _decode_media_url(self, value: str) -> str:
        decoded = html.unescape(value)
        for _ in range(4):
            previous = decoded
            try:
                decoded = json.loads(f'"{decoded}"')
            except json.JSONDecodeError:
                decoded = decoded.replace("\\/", "/").replace("\\u0026", "&").replace("\\u003d", "=")
            decoded = html.unescape(decoded)
            if decoded == previous:
                break
        return decoded

    def _try_load_json(self, text: str) -> Any | None:
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    def _shortcode_to_media_id(self, shortcode: str) -> int:
        media_id = 0
        for char in shortcode:
            media_id = media_id * 64 + SHORTCODE_ALPHABET.index(char)
        return media_id

    def _safe_int(self, value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _safe_float(self, value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _guess_extension(self, url: str, content_type: str | None, is_video: bool) -> str:
        content_type = (content_type or "").split(";", maxsplit=1)[0].lower()
        if content_type == "video/mp4":
            return ".mp4"
        if content_type == "image/webp":
            return ".webp"
        if content_type in ("image/jpeg", "image/jpg"):
            return ".jpg"
        if content_type == "image/png":
            return ".png"
        path_suffix = Path(urlparse(url).path).suffix.lower()
        if path_suffix in (".mp4", ".mov", ".jpg", ".jpeg", ".png", ".webp"):
            return ".jpg" if path_suffix == ".jpeg" else path_suffix
        return ".mp4" if is_video else ".jpg"

    def _cleanup_old_files(self, download_path: Path, shortcode: str) -> None:
        for file_path in download_path.iterdir():
            if file_path.is_file() and file_path.name.startswith(shortcode):
                file_path.unlink(missing_ok=True)


async def get_instagram_shortcode(url: str) -> str | None:
    try:
        client = await get_instagram_web_parser()
        parsed = client._parse_target(url)
        if parsed.get("shortcode"):
            return parsed["shortcode"]
        if parsed.get("id"):
            return parsed["id"]
        async with InstagramHttpSession(client.http_preset) as session:
            target = await client._resolve_share_target(session, url)
            return target.get("shortcode") or target.get("id")
    except Exception as exc:
        logger.error(f"Ошибка при получении Instagram shortcode: {exc}")
        return None


async def init_instagram_web_parser() -> InstagramWebDownloader:
    try:
        from tg_bot.services.instagram_ua_service import instagram_ua_service

        user_agent = await instagram_ua_service.get_user_agent()
    except Exception as exc:
        logger.warning(f"Failed to get dynamic User-Agent for Instagram web parser: {exc}, using Android app UA")
        user_agent = ANDROID_APP_USER_AGENT
    http_preset = get_httpcloak_preset_name(user_agent)
    logger.info(f"Initializing Instagram web parser preset={http_preset} ua={user_agent[:48]}")
    return InstagramWebDownloader(user_agent=user_agent, http_preset=http_preset)


async def get_instagram_web_parser() -> InstagramWebDownloader:
    return await init_instagram_web_parser()


async def reset_instagram_web_parser() -> None:
    logger.info("Instagram web parser reset requested")


async def download_instagram_web_media(url: str, use_cookies: bool = False) -> tuple[str | None, str | None]:
    try:
        client = await get_instagram_web_parser()
        post = await client.download(url, DOWNLOADS_DIR, use_cookies=use_cookies)
        return post.shortcode, None
    except InstagramUnsupportedUrlError as exc:
        return None, f"❌ Ошибка: {exc}"
    except InstagramAuthRequiredError as exc:
        message = str(exc) or "Требуется авторизация в Instagram для этого контента."
        return None, f"❌ Ошибка: {message}"
    except InstagramRateLimitedError:
        await reset_instagram_web_parser()
        return None, "❌ Ошибка: Instagram ограничил доступ. Попробуйте позже или обнови cookies."
    except InstagramNoMediaError as exc:
        return None, f"❌ Ошибка: Не удалось найти медиа в посте. {exc}"
    except InstagramDownloadError as exc:
        return None, f"❌ Ошибка Instagram: {exc}"
    except Exception as exc:
        logger.exception(f"Unexpected Instagram downloader error: {exc}")
        return None, "❌ Ошибка: Instagram вернул неожиданный формат ответа."


DOWNLOADS_DIR.mkdir(exist_ok=True)


async def select_instagram_media(shortcode: str, download_path: Path = DOWNLOADS_DIR) -> dict[str, list[Path] | str]:
    files = [file_path for file_path in download_path.iterdir() if file_path.name.startswith(shortcode)]
    images: list[Path] = []
    videos: list[Path] = []
    caption = ""
    for file_path in files:
        suffix = file_path.suffix.lower()
        if suffix in (".jpg", ".jpeg", ".png", ".webp"):
            images.append(file_path)
        elif suffix in (".mp4", ".mov"):
            videos.append(file_path)
        elif suffix == ".txt":
            caption = await asyncio.to_thread(file_path.read_text, encoding="utf-8")
    return {"images": images, "videos": videos, "caption": caption}
