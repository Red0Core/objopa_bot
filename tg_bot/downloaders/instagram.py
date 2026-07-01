import asyncio
import html
import json
import re
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from curl_cffi.requests import AsyncSession, Response

from core.config import DOWNLOADS_DIR
from core.logger import logger
from tg_bot.services.instagram_ua_service import InstagramUserAgentService, instagram_ua_service

INSTAGRAM_REGEX = re.compile(
    r"(https?:\/\/(?:www\.)?instagram\.com\/(?:(?:p|reel|tv)\/[\w\-]+|share\/(?:p\/|reel\/|tv\/)?[\w\-]+|stories\/[\w.\-]+(?:\/\d+)?))"
)

INSTAGRAM_APP_ID = "936619743392459"
REQUEST_TIMEOUT = 20
SHORTCODE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


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


class InstagramWebDownloader:
    """Small Instagram web downloader that does not depend on Instaloader."""

    def __init__(self, user_agent: str):
        self.user_agent = user_agent

    async def download(self, url: str, download_path: Path = DOWNLOADS_DIR) -> InstagramPost:
        download_path.mkdir(parents=True, exist_ok=True)

        async with AsyncSession() as session:
            shortcode, canonical_url = await self._resolve_shortcode(session, url)
            post = await self._load_post(session, shortcode, canonical_url)

            if not post.media:
                raise InstagramNoMediaError("Instagram did not expose downloadable media for this post.")

            self._cleanup_old_files(download_path, shortcode)
            downloaded_files = await self._download_media_files(session, post, download_path)
            if not downloaded_files:
                raise InstagramNoMediaError("Instagram media links were found, but files were not downloaded.")

            if post.caption:
                (download_path / f"{shortcode}.txt").write_text(post.caption, encoding="utf-8")

            return post

    async def _resolve_shortcode(self, session: AsyncSession, url: str) -> tuple[str, str]:
        match = re.search(r"/(p|reel|tv)/([\w-]+)", url)
        if match:
            media_type, shortcode = match.groups()
            return shortcode, f"https://www.instagram.com/{media_type}/{shortcode}/"

        if "/stories/" in url:
            raise InstagramUnsupportedUrlError("Stories are not supported by the custom Instagram downloader yet.")

        response = await self._request(session, url, allow_redirects=True)
        final_url = str(response.url)
        match = re.search(r"/(p|reel|tv)/([\w-]+)", final_url)
        if match:
            media_type, shortcode = match.groups()
            return shortcode, f"https://www.instagram.com/{media_type}/{shortcode}/"

        raise InstagramUnsupportedUrlError("Could not extract Instagram shortcode from the URL.")

    async def _load_post(self, session: AsyncSession, shortcode: str, canonical_url: str) -> InstagramPost:
        html_text: str | None = None
        is_reel = "/reel/" in canonical_url

        try:
            html_text = await self._load_html(session, canonical_url)
        except InstagramDownloadError as exc:
            logger.debug(f"Instagram HTML page failed for {shortcode}: {exc}")

        raw_video_post: InstagramPost | None = None
        if html_text:
            raw_video_post = self._post_from_raw_video_urls(html_text, shortcode, canonical_url)
            json_candidates = self._extract_json_candidates(html_text)
            for candidate in json_candidates:
                post = self._post_from_json(candidate, shortcode, canonical_url)
                if post.media:
                    if is_reel and any(media.is_video for media in post.media):
                        return post
                    if is_reel and raw_video_post.media and not any(media.is_video for media in post.media):
                        logger.debug(f"Using raw Instagram video URL fallback for reel {shortcode}")
                        return raw_video_post
                    if is_reel:
                        logger.debug(f"Ignoring image-only Instagram reel metadata for {shortcode}")
                        continue
                    return post

        json_candidates = await self._load_json_candidates(session, shortcode, canonical_url)
        for candidate in json_candidates:
            post = self._post_from_json(candidate, shortcode, canonical_url)
            if post.media:
                if is_reel and not any(media.is_video for media in post.media):
                    logger.debug(f"Ignoring image-only Instagram reel API metadata for {shortcode}")
                    continue
                return post

        if raw_video_post and raw_video_post.media:
            logger.debug(f"Using raw Instagram video URL fallback for {shortcode}")
            return raw_video_post

        if html_text:
            post = self._post_from_meta_tags(html_text, shortcode, canonical_url)
            if post.media:
                if is_reel and not any(media.is_video for media in post.media):
                    raise InstagramAuthRequiredError(
                        "Instagram не отдал video URL для этого reel без cookies. "
                        "Загрузи cookies через /set_cookies и попробуй /d_cookies <url>."
                    )
                return post

        raise InstagramNoMediaError("No media metadata found on Instagram page.")

    async def _load_json_candidates(self, session: AsyncSession, shortcode: str, canonical_url: str) -> list[Any]:
        candidates: list[Any] = []
        api_urls = [
            f"{canonical_url}?__a=1&__d=dis",
            f"https://www.instagram.com/api/v1/media/{self._shortcode_to_media_id(shortcode)}/info/",
        ]

        for api_url in api_urls:
            try:
                response = await self._request(session, api_url, headers=self._json_headers(canonical_url))
            except InstagramDownloadError as exc:
                logger.debug(f"Instagram JSON endpoint failed: {api_url} - {exc}")
                continue

            parsed = self._try_load_json(response.text)
            if parsed is not None:
                candidates.append(parsed)

        return candidates

    async def _load_html(self, session: AsyncSession, canonical_url: str) -> str:
        response = await self._request(session, canonical_url)
        return response.text

    async def _request(
        self,
        session: AsyncSession,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        allow_redirects: bool = True,
    ) -> Response:
        request_headers = self._html_headers()
        if headers:
            request_headers.update(headers)

        try:
            response = await session.get(
                url,
                allow_redirects=allow_redirects,
                headers=request_headers,
                impersonate="chrome",
                timeout=REQUEST_TIMEOUT,
            )
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
        session: AsyncSession,
        post: InstagramPost,
        download_path: Path,
    ) -> list[Path]:
        downloaded_files: list[Path] = []
        media_headers = self._media_headers(post.canonical_url)

        for media in post.media:
            try:
                response = await self._request(session, media.url, headers=media_headers)
            except InstagramDownloadError as exc:
                logger.warning(f"Failed to download Instagram media item {media.index}: {exc}")
                continue

            extension = self._guess_extension(media.url, response.headers.get("content-type"), media.is_video)
            final_path = download_path / f"{post.shortcode}_{media.index:02d}{extension}"
            tmp_path = download_path / f".{final_path.name}.{secrets.token_hex(4)}.part"

            tmp_path.write_bytes(response.content)
            if tmp_path.stat().st_size == 0:
                tmp_path.unlink(missing_ok=True)
                continue

            shutil.move(str(tmp_path), final_path)
            downloaded_files.append(final_path)

        return downloaded_files

    def _html_headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Referer": "https://www.instagram.com/",
            "Upgrade-Insecure-Requests": "1",
        }

    def _json_headers(self, referer: str) -> dict[str, str]:
        return {
            "Accept": "application/json,text/plain,*/*",
            "Referer": referer,
            "X-IG-App-ID": INSTAGRAM_APP_ID,
            "X-ASBD-ID": "129477",
            "X-Requested-With": "XMLHttpRequest",
        }

    def _media_headers(self, referer: str) -> dict[str, str]:
        return {
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,video/*,*/*;q=0.8",
            "Referer": referer,
        }

    def _post_from_json(self, data: Any, shortcode: str, canonical_url: str) -> InstagramPost:
        root_nodes = self._find_shortcode_nodes(data, shortcode)
        if not root_nodes:
            root_nodes = self._find_media_roots(data)

        media_items: list[InstagramMedia] = []
        caption: str | None = None

        for node in root_nodes:
            if caption is None:
                caption = self._extract_caption(node)
            if caption is None:
                caption = self._extract_caption_from_children(node)
            media_items.extend(self._extract_media_from_node(node))

        unique_media = self._dedupe_media(media_items)
        return InstagramPost(shortcode=shortcode, canonical_url=canonical_url, caption=caption, media=unique_media)

    def _find_shortcode_nodes(self, data: Any, shortcode: str) -> list[dict[str, Any]]:
        nodes: list[dict[str, Any]] = []

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                shortcode_media = value.get("shortcode_media") or value.get("xdt_shortcode_media")
                if isinstance(shortcode_media, dict):
                    nodes.append(shortcode_media)

                if value.get("shortcode") == shortcode or value.get("code") == shortcode:
                    nodes.append(value)

                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(data)
        return nodes

    def _find_media_roots(self, data: Any) -> list[dict[str, Any]]:
        roots: list[dict[str, Any]] = []

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                if self._node_has_media(value):
                    roots.append(value)
                    return
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(data)
        return roots

    def _extract_media_from_node(self, node: dict[str, Any]) -> list[InstagramMedia]:
        media_nodes = self._flatten_media_nodes(node)
        media_items: list[InstagramMedia] = []

        for index, media_node in enumerate(media_nodes, start=1):
            media_url = self._pick_video_url(media_node)
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

    def _pick_video_url(self, node: dict[str, Any]) -> str | None:
        video_url = node.get("video_url")
        if isinstance(video_url, str):
            return html.unescape(video_url)

        video_versions = node.get("video_versions")
        if isinstance(video_versions, list):
            return self._pick_best_candidate_url(video_versions)

        return None

    def _pick_image_url(self, node: dict[str, Any]) -> str | None:
        image_versions = (node.get("image_versions2") or {}).get("candidates")
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

    def _pick_best_candidate_url(self, candidates: list[Any]) -> str | None:
        best_candidate: dict[str, Any] | None = None
        best_score = -1

        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue

            url = candidate.get("url") or candidate.get("src")
            if not isinstance(url, str):
                continue

            width = self._safe_int(candidate.get("width") or candidate.get("config_width"))
            height = self._safe_int(candidate.get("height") or candidate.get("config_height"))
            score = width * height

            if score > best_score:
                best_candidate = candidate
                best_score = score

        if not best_candidate:
            return None

        url = best_candidate.get("url") or best_candidate.get("src")
        return html.unescape(url) if isinstance(url, str) else None

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

    def _extract_json_candidates(self, html_text: str) -> list[Any]:
        candidates: list[Any] = []

        for match in re.finditer(
            r"<script[^>]*type=[\"']application/(?:json|ld\+json)[\"'][^>]*>(.*?)</script>",
            html_text,
            flags=re.IGNORECASE | re.DOTALL,
        ):
            parsed = self._try_load_json(html.unescape(match.group(1)).strip())
            if parsed is not None:
                candidates.append(parsed)

        for marker in ("window._sharedData", "shortcode_media", "xdt_shortcode_media"):
            for json_text in self._extract_balanced_json_after_marker(html_text, marker):
                parsed = self._try_load_json(html.unescape(json_text))
                if parsed is not None:
                    candidates.append(parsed)

        return candidates

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

        return chunks

    def _balanced_json_object(self, text: str, start: int) -> str | None:
        depth = 0
        in_string = False
        escaped = False

        for index in range(start, len(text)):
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
            r'https?:\\?/\\?/[^"\'<>]+?\.mp4[^"\'<>]*',
        )

        for pattern in patterns:
            for match in re.finditer(pattern, html_text, flags=re.IGNORECASE):
                raw_url = match.group(1) if match.groups() else match.group(0)
                video_url = self._decode_media_url(raw_url)
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
            r"<meta\s+[^>]*(?:property|name)=[\"']og:(video|image|description)[\"'][^>]*>",
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
        value_lower = value.lower()
        return self._looks_like_media_url(value) and ".mp4" in value_lower

    def _decode_media_url(self, value: str) -> str:
        decoded = html.unescape(value)

        for _ in range(3):
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


_instagram_client: InstagramWebDownloader | None = None


async def get_instagram_shortcode(url: str) -> str | None:
    """
    Resolve an Instagram post shortcode.
    """
    try:
        client = await get_instaloader_session()
        async with AsyncSession() as session:
            shortcode, _ = await client._resolve_shortcode(session, url)
            return shortcode
    except Exception as exc:
        logger.error(f"Ошибка при получении Instagram shortcode: {exc}")
        return None


async def init_instaloader() -> InstagramWebDownloader:
    """Initialize the custom Instagram web downloader.

    The public name is kept for backwards compatibility with existing routers.
    """
    try:
        user_agent = await instagram_ua_service.get_user_agent()
        logger.info(f"Initializing custom Instagram downloader with UA: {user_agent[:50]}...")
    except Exception as exc:
        logger.warning(f"Failed to get dynamic User-Agent for Instagram downloader: {exc}, using fallback")
        user_agent = InstagramUserAgentService.DEFAULT_USER_AGENT

    return InstagramWebDownloader(user_agent=user_agent)


async def get_instaloader_session() -> InstagramWebDownloader:
    """Get or create the custom Instagram downloader instance.

    The public name is kept for backwards compatibility with existing routers.
    """
    global _instagram_client
    if _instagram_client is None:
        _instagram_client = await init_instaloader()
    return _instagram_client


async def reset_instaloader_session() -> None:
    """Reset the cached Instagram downloader so a fresh User-Agent is used."""
    global _instagram_client
    _instagram_client = None
    logger.info("Instagram downloader cache reset")


async def download_instagram_media(url: str) -> tuple[str | None, str | None]:
    """Download Instagram post media and return shortcode plus a user-facing error."""
    try:
        client = await get_instaloader_session()
        post = await client.download(url, DOWNLOADS_DIR)
        return post.shortcode, None
    except InstagramUnsupportedUrlError as exc:
        return None, f"❌ Ошибка: {exc}"
    except InstagramAuthRequiredError as exc:
        message = str(exc) or "Требуется авторизация в Instagram для этого контента."
        return None, f"❌ Ошибка: {message}"
    except InstagramRateLimitedError:
        await reset_instaloader_session()
        return None, "❌ Ошибка: Instagram ограничил доступ. Попробуйте обновить User-Agent командой /ua_set"
    except InstagramNoMediaError as exc:
        return None, f"❌ Ошибка: Не удалось найти медиа в посте. {exc}"
    except InstagramDownloadError as exc:
        return None, f"❌ Ошибка Instagram: {exc}"
    except Exception as exc:
        logger.exception(f"Unexpected Instagram downloader error: {exc}")
        return None, f"❌ Ошибка: {exc}"


DOWNLOADS_DIR.mkdir(exist_ok=True)


async def select_instagram_media(shortcode: str, download_path: Path = DOWNLOADS_DIR) -> dict[str, list[Path] | str]:
    """
    Select the files downloaded by the custom Instagram downloader.
    """
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
