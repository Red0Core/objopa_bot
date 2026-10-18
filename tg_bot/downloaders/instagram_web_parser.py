"""Instagram public/media downloader. Browser cookies only; no synthetic mobile-app login.

Compatibility: ``download_instagram_web_media`` returns (unique media prefix, error),
so DownloaderManager's existing file collector and Telegram media sender continue to work.
Instagram's undocumented endpoints can change; authentication is never guaranteed.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
import secrets
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from httpcloak import Session

from core.config import DOWNLOADS_DIR
from core.logger import logger
from core.memory import MAX_MEDIA_BYTES

WEB_APP_ID = "936619743392459"
# Actual navigation capture from Instagram 451's Chromium 155 Android WebView.
# The chrome-155-android TLS/H2 preset is retained. Never freeze captured JA3:
# real Chromium randomizes TLS extension order, and JA3 misses key share/ALPS/ECH.
WEBVIEW_155_SEC_CH_UA = '"Android WebView";v="155", "Chromium";v="155", "Not(A:Brand";v="24"'
WEBVIEW_155_ACCEPT_LANGUAGE = "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"
WEBVIEW_155_NAV_ACCEPT = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/jxl,image/avif,"
    "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
)
TIMEOUT = 20
# Verified with a real Instagram 451 / RMX3709 WebView capture and a VPS
# HTTPCloak probe. Cold TLS handshakes naturally have no PSK (JA4 count 16),
# while resuming connections match the captured JA4 with count 17.
REALME_WEBVIEW_PRESET = "instagram-realme-rmx3709-webview-155"
_REALME_PRESET_REGISTERED = False
_REALME_PRESET_LOCK = threading.Lock()
MAX_JSON_BYTES = 3_000_000
SHORTCODE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
DEFAULT_UA = (
    "Mozilla/5.0 (Linux; Android 16; RMX3709 Build/BP2A.250605.015; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
    "Chrome/155.0.8059.73 Mobile Safari/537.36 Instagram 451.0.0.53.80 "
    "Android (36/16; 560dpi; 1240x2772; realme; RMX3709; REE2B2L1; qcom; ru_RU; 1089931504; IABMV/1)"
)


def _register_realme_webview_preset() -> str:
    """Reproduce the measured WebView TLS without faking a PSK or freezing JA3.

    HTTPCloak's Chrome 155 preset includes trust_anchors (0xCA34), whereas
    the real RMX3709 Instagram WebView 451 capture does not. Preserve the
    inherited Chrome ClientHello, its valid resumption variant and HTTP/2.
    """
    global _REALME_PRESET_REGISTERED
    with _REALME_PRESET_LOCK:
        if _REALME_PRESET_REGISTERED:
            return REALME_WEBVIEW_PRESET
        import httpcloak

        try:
            desc = httpcloak.describe_preset("chrome-155-android")
            data = json.loads(desc) if isinstance(desc, str) else desc
            preset = data["preset"]
            if preset.get("based_on"):
                raise ValueError("HTTPCloak preset description must be fully resolved")
            tls = preset["tls"]
            anchors = tls.get("trust_anchors")
            if not isinstance(anchors, list) or not anchors:
                raise ValueError("Chrome 155 base preset has no trust_anchors to remove")
            if not tls.get("psk_client_hello"):
                raise ValueError("Chrome 155 preset does not support proper TLS resumption")
            del tls["trust_anchors"]
            preset["name"] = REALME_WEBVIEW_PRESET
            registered = httpcloak.load_preset_from_json(json.dumps(data))
            if registered != REALME_WEBVIEW_PRESET:
                raise ValueError("HTTPCloak returned a different registered preset name")
        except Exception as exc:
            raise InstagramDownloadError(
                "Не удалось настроить проверенный TLS-профиль Realme WebView; проверь httpcloak>=1.7.3."
            ) from exc
        _REALME_PRESET_REGISTERED = True
    return REALME_WEBVIEW_PRESET


class InstagramDownloadError(Exception):
    """A user-actionable Instagram download error."""


class InstagramAuthRequiredError(InstagramDownloadError):
    pass


class InstagramRateLimitedError(InstagramDownloadError):
    pass


class InstagramUnsupportedUrlError(InstagramDownloadError):
    pass


class InstagramNoMediaError(InstagramDownloadError):
    pass


class InstagramMediaTooLargeError(InstagramDownloadError):
    pass


class InstagramChallengeError(InstagramDownloadError):
    pass


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
    download_id: str | None = None
    # OpenGraph image/video can only describe the cover or first slide.
    # A structured JSON response can describe the full carousel.
    complete_metadata: bool = False


@dataclass(frozen=True, slots=True)
class InstagramTarget:
    kind: str
    code: str
    url: str
    username: str = ""


def _instagram_host(url: str) -> bool:
    try:
        parts = urlsplit(url)
        allowed_port = parts.port in (None, 443)
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and allowed_port
        and (parts.hostname or "").lower()
        in {"instagram.com", "www.instagram.com", "m.instagram.com", "l.instagram.com"}
        and not parts.username
        and not parts.password
    )


def _cdn_host(url: str) -> bool:
    try:
        parts = urlsplit(url)
        allowed_port = parts.port in (None, 443)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return (
        parts.scheme == "https"
        and allowed_port
        and not parts.username
        and not parts.password
        and (
            host == "cdninstagram.com"
            or host.endswith(".cdninstagram.com")
            or host == "fbcdn.net"
            or host.endswith(".fbcdn.net")
        )
    )


def _parse_target(url: str) -> InstagramTarget:
    if not _instagram_host(url):
        raise InstagramUnsupportedUrlError("Некорректный домен или протокол ссылки Instagram.")
    path = urlsplit(url).path.strip("/")
    m = re.fullmatch(r"(p|reel|tv)/([A-Za-z0-9_-]+)", path, re.I)
    if m:
        media_type, code = m.groups()
        return InstagramTarget(
            "reel" if media_type.lower() == "reel" else "post",
            code,
            f"https://www.instagram.com/{media_type.lower()}/{code}/",
        )
    m = re.fullmatch(r"stories/highlights/(\d+)", path, re.I)
    if m:
        return InstagramTarget("highlight", m.group(1), f"https://www.instagram.com/{path}/")
    m = re.fullmatch(r"stories/([\w.\-]+)/(\d+)", path, re.I)
    if m:
        return InstagramTarget("story", m.group(2), f"https://www.instagram.com/{path}/", m.group(1))
    m = re.fullmatch(r"stories/([\w.\-]+)", path, re.I)
    if m:
        return InstagramTarget("stories", m.group(1), f"https://www.instagram.com/{path}/", m.group(1))
    if re.fullmatch(r"share/(?:p/|reel/|tv/)?[\w-]+/?", path, re.I):
        return InstagramTarget("share", "", url)
    raise InstagramUnsupportedUrlError("Неподдерживаемый формат ссылки Instagram.")


def _shortcode_id(code: str) -> int:
    value = 0
    for char in code:
        if char not in SHORTCODE_ALPHABET:
            raise InstagramUnsupportedUrlError("Некорректный shortcode Instagram.")
        value = value * 64 + SHORTCODE_ALPHABET.index(char)
    return value


def _parse_netscape(text: str) -> list[tuple[str, str, str, str]]:
    """Keep encoded values intact, avoid expired or non-Instagram cookies."""
    import time

    out: list[tuple[str, str, str, str]] = []
    for line in text.splitlines():
        if line.startswith("#HttpOnly_"):
            line = line.removeprefix("#HttpOnly_")
        elif line.startswith("#") or not line.strip():
            continue
        parts = line.split("\t", 6)
        if len(parts) != 7:
            continue
        domain, _, path, secure, expiry, name, value = parts
        domain = domain.lstrip(".").lower()
        if domain != "instagram.com" and not domain.endswith(".instagram.com"):
            continue
        if not name or any(c in name for c in " \r\n;=") or "\r" in value or "\n" in value:
            continue
        try:
            if int(expiry) and int(expiry) < time.time():
                continue
        except ValueError:
            continue
        if secure.upper() != "TRUE":
            # Authentication cookies are HTTPS-only; do not import an insecure copy.
            continue
        out.append((name, value, "." + domain, path or "/"))
    return out


def _session_cookies_present(cookies: list[tuple[str, str, str, str]]) -> bool:
    return any(name == "sessionid" and value for name, value, _, _ in cookies)


def _load_cookies(session: Session, text: str) -> bool:
    """Import existing browser cookies without decoding their opaque values."""
    parsed = _parse_netscape(text)
    for name, value, domain, path in parsed:
        # Python HTTPCloak uses set_cookie(), NOT a requests-style cookie jar.
        http_only = any(
            row.startswith("#HttpOnly_")
            and (fields := row.removeprefix("#HttpOnly_").split("\t", 6))
            and len(fields) == 7
            and fields[0].lstrip(".").lower() == domain.lstrip(".").lower()
            and fields[2] == path
            and fields[5] == name
            for row in text.splitlines()
        )
        session.set_cookie(name, value, domain=domain, path=path, secure=True, http_only=http_only)
    return _session_cookies_present(parsed)


def _extract_caption(node: dict[str, Any]) -> str | None:
    caption = node.get("caption")
    if isinstance(caption, dict):
        caption = caption.get("text")
    if isinstance(caption, str):
        return caption.strip() or None
    for key in ("caption_text", "edge_media_to_caption"):
        found = node.get(key)
        if isinstance(found, str):
            return found.strip() or None
        if isinstance(found, dict):
            for edge in (found.get("edges") or [])[:1]:
                result = (edge.get("node") or {}).get("text") if isinstance(edge, dict) else None
                if isinstance(result, str):
                    return result.strip() or None
    return None


def _best_candidate(entries: Any) -> str | None:
    if not isinstance(entries, list):
        return None
    valid = [e for e in entries if isinstance(e, dict) and isinstance(e.get("url") or e.get("src"), str)]

    def pixels(entry: dict[str, Any]) -> int:
        try:
            return int(entry.get("width") or entry.get("config_width") or 0) * int(
                entry.get("height") or entry.get("config_height") or 0
            )
        except (TypeError, ValueError):
            return 0

    valid.sort(key=pixels, reverse=True)
    moderate = [entry for entry in valid if 0 < pixels(entry) <= 1920 * 1080]
    if moderate:
        valid = moderate
    for entry in valid:
        url = html.unescape(entry.get("url") or entry.get("src"))
        if _cdn_host(url):
            return url
    return None


def _media_nodes(node: dict[str, Any]) -> list[dict[str, Any]]:
    child = node.get("carousel_media") or node.get("items")
    if isinstance(child, list) and child:
        return [v for v in child if isinstance(v, dict)]
    for key in ("edge_sidecar_to_children", "edge_web_media_to_related_media"):
        wrapper = node.get(key)
        if isinstance(wrapper, dict) and isinstance(wrapper.get("edges"), list):
            return [v["node"] for v in wrapper["edges"] if isinstance(v, dict) and isinstance(v.get("node"), dict)]
    return [node]


def _is_target_node(node: dict[str, Any], code: str) -> bool:
    """Match the requested media, not a related-post recommendation or a story neighbour."""
    if any(str(node.get(key)) == code for key in ("code", "shortcode") if node.get(key) is not None):
        return True
    if code.isdigit():
        for key in ("pk", "id", "pk_id"):
            value = node.get(key)
            if value is not None and str(value).split("_", 1)[0] == code:
                return True
    return False


def _post_from_json(data: Any, code: str, url: str) -> InstagramPost:
    # Instagram embeds use nested Relay/require JSON. Walking just 11 levels
    # misses legitimate media. Keep a strict node budget to avoid unbounded scans.
    matches: list[dict[str, Any]] = []
    stack: list[tuple[Any, int]] = [(data, 0)]
    inspected = 0
    while stack and inspected < 12_000 and len(matches) < 30:
        node, depth = stack.pop()
        inspected += 1
        if depth > 30:
            continue
        if isinstance(node, list):
            stack.extend((entry, depth + 1) for entry in reversed(node[:100]))
        elif isinstance(node, dict):
            if _is_target_node(node, code):
                matches.append(node)
            for key in ("shortcode_media", "xdt_shortcode_media"):
                child = node.get(key)
                if isinstance(child, dict) and (
                    _is_target_node(child, code)
                    or not any(child.get(field) for field in ("code", "shortcode", "pk", "id"))
                ):
                    # Some server-rendered shortcode roots omit an explicit code.
                    matches.append(child)
            stack.extend((entry, depth + 1) for entry in reversed(list(node.values())))

    media: list[InstagramMedia] = []
    seen: set[str] = set()
    caption: str | None = None
    for match in matches:
        caption = caption or _extract_caption(match)
        for node in _media_nodes(match):
            vv = node.get("video_versions")
            video = _best_candidate(vv) or node.get("video_url")
            if isinstance(video, str) and _cdn_host(html.unescape(video)):
                candidate, is_video = html.unescape(video), True
            else:
                images = node.get("image_versions2")
                image_candidates = images.get("candidates") if isinstance(images, dict) else None
                candidate = (
                    _best_candidate(image_candidates)
                    or _best_candidate(node.get("display_resources"))
                    or node.get("display_url")
                )
                is_video = False
            if isinstance(candidate, str) and _cdn_host(html.unescape(candidate)):
                candidate = html.unescape(candidate)
                identity = candidate.split("?", 1)[0]
                if identity not in seen:
                    seen.add(identity)
                    media.append(InstagramMedia(candidate, is_video, len(media) + 1))
    return InstagramPost(code, url, caption, media, complete_metadata=bool(media))


def _extract_balanced_json(script: str, after: str) -> Any | None:
    """Parse bounded script-embedded JSON without evaluating arbitrary JavaScript."""
    idx = script.find(after)
    if idx < 0:
        return None
    for pos in range(idx + len(after), min(len(script), idx + len(after) + 200)):
        if script[pos] in "[{":
            try:
                payload, _ = json.JSONDecoder().raw_decode(script[pos:])
                return payload
            except (ValueError, TypeError):
                return None
    return None


def _post_from_html(source: str, code: str, url: str) -> InstagramPost:
    from_meta: list[InstagramMedia] = []
    caption: str | None = None
    for match in re.finditer(r"<meta\b[^>]+>", source, re.I):
        tag = match.group(0)
        key = re.search(r"\b(?:name|property)\s*=\s*['\"]([^'\"]+)['\"]", tag, re.I)
        value = re.search(r"\bcontent\s*=\s*['\"]([^'\"]+)['\"]", tag, re.I)
        if not key or not value:
            continue
        field = key.group(1).lower()
        data = html.unescape(value.group(1))
        if field in ("og:video", "og:video:secure_url", "twitter:player:stream") and _cdn_host(data):
            from_meta.append(InstagramMedia(data, True, len(from_meta) + 1))
        elif field in ("og:image", "og:image:secure_url", "twitter:image") and _cdn_host(data):
            from_meta.append(InstagramMedia(data, False, len(from_meta) + 1))
        elif field in ("og:description", "description"):
            caption = caption or data

    # Prefer structured post data over OpenGraph: OG only includes a cover for
    # many carousels. Instagram also publishes app hydration in JS blocks.
    for match in re.finditer(r"<script\b[^>]*>(.*?)</script>", source, re.I | re.S):
        raw = match.group(1)
        if len(raw) > MAX_JSON_BYTES:
            continue
        candidates: list[Any] = []
        kind = re.search(r"\btype\s*=\s*['\"]([^'\"]+)['\"]", match.group(0)[:150], re.I)
        if kind and kind.group(1).lower() in ("application/json", "application/ld+json"):
            try:
                candidates.append(json.loads(raw))
            except (ValueError, TypeError):
                pass
        for marker in (
            "window.__additionalDataLoaded(",
            "window._sharedData =",
            "window._sharedData=",
            "xdt_shortcode_media",
        ):
            candidate = _extract_balanced_json(raw, marker)
            if candidate is not None:
                candidates.append(candidate)
        for candidate in candidates:
            post = _post_from_json(candidate, code, url)
            if post.media:
                post.caption = post.caption or caption
                return post

    # Some public embeds include video_url outside JSON scripts; decode escaped
    # URL literals, without treating the OpenGraph thumbnail as actual video.
    for pattern in (r'"video_url"\s*:\s*"([^"\n]+)"', r'\\"video_url\\"\s*:\s*\\"(.*?)\\"'):
        for match in re.finditer(pattern, source[:MAX_JSON_BYTES]):
            raw = match.group(1)
            try:
                decoded = json.loads('"' + raw + '"')
            except ValueError:
                decoded = raw.replace(r"\/", "/").replace(r"\u0026", "&")
            decoded = html.unescape(decoded)
            if _cdn_host(decoded):
                return InstagramPost(code, url, caption, [InstagramMedia(decoded, True, 1)], complete_metadata=True)

    videos = [x for x in from_meta if x.is_video]
    images = [x for x in from_meta if not x.is_video]
    chosen = videos or images
    return InstagramPost(code, url, caption, chosen[:1], complete_metadata=False)


def _header(response: Any, name: str) -> str:
    """HTTPCloak headers may be case-insensitive dicts with list values."""
    get_header = getattr(response, "get_header", None)
    if callable(get_header):
        value = get_header(name)
        if isinstance(value, str):
            return value
    for key, value in (getattr(response, "headers", {}) or {}).items():
        if key.lower() == name.lower():
            if isinstance(value, (list, tuple)):
                return str(value[-1]) if value else ""
            return str(value)
    return ""


def _response_problem(response: Any) -> None:
    status = int(response.status_code)
    location = _header(response, "Location")
    if status in (301, 302, 303, 307, 308):
        if "/challenge/" in location or "/checkpoint/" in location:
            raise InstagramChallengeError("Instagram требует подтверждения входа через официальное приложение.")
        if "/accounts/login" in location:
            raise InstagramAuthRequiredError("Instagram перенаправил на страницу авторизации.")
    if status == 429:
        raise InstagramRateLimitedError("Instagram вернул HTTP 429. Повторные запросы временно остановлены.")
    if status == 401:
        raise InstagramAuthRequiredError("Instagram вернул HTTP 401: нужна действующая сессия.")
    if status == 403:
        raise InstagramDownloadError("Instagram вернул HTTP 403: доступ запрещён (не обязательно истекли cookies).")
    if status == 404:
        raise InstagramNoMediaError("Instagram вернул HTTP 404: медиа удалено либо недоступно.")
    if status >= 400:
        raise InstagramDownloadError(f"Instagram вернул HTTP {status}.")


def _payload_problem(body: Any) -> None:
    if not isinstance(body, dict):
        return
    joined = " ".join(str(body.get(key, "")) for key in ("message", "error_type", "status", "feedback_title"))
    lowered = joined.lower()
    if (
        body.get("checkpoint_url")
        or body.get("challenge")
        or "challenge_required" in lowered
        or "checkpoint_required" in lowered
    ):
        raise InstagramChallengeError("Instagram требует подтверждения входа через официальное приложение.")
    if "feedback_required" in lowered or "please wait" in lowered or "rate limit" in lowered:
        raise InstagramRateLimitedError("Instagram временно ограничил запросы (feedback_required).")
    if "login_required" in lowered or "not authorized" in lowered:
        raise InstagramAuthRequiredError("Instagram требует повторную авторизацию.")


def _is_login_wall(text: str) -> bool:
    sample = text[:100000].lower()
    # Public Instagram pages may contain links to /accounts/login/ and generic
    # "Log in to Instagram" calls to action; neither proves a login wall.
    if "polariserrorroot" in sample or '"pageid":"httperrorpage"' in sample:
        return True
    if re.search(r"<title\b[^>]*>\s*log in to instagram\s*</title>", sample, re.I):
        return True
    return '"pageid":"loginpage"' in sample or '"page_id":"loginpage"' in sample


def _file_extension(media: InstagramMedia) -> str:
    path = urlsplit(media.url).path.lower()
    if media.is_video:
        return ".mp4"
    if path.endswith(".webp"):
        return ".webp"
    if path.endswith(".png"):
        return ".png"
    return ".jpg"


def _check_file(path: Path, is_video: bool) -> bool:
    with path.open("rb") as f:
        head = f.read(32)
    if is_video:
        return head[4:8] == b"ftyp"
    return (
        head.startswith(b"\xff\xd8\xff")
        or head.startswith(b"\x89PNG\r\n\x1a\n")
        or (head.startswith(b"RIFF") and head[8:12] == b"WEBP")
    )


class InstagramWebDownloader:
    def __init__(self, user_agent: str = DEFAULT_UA, http_preset: str = "chrome-155-android"):
        self.user_agent = user_agent or DEFAULT_UA
        self.http_preset = http_preset
        # Only use the captured Client Hints for the corresponding WebView.
        # Preserve an explicit /ua_set override without claiming it's a WebView.
        self.is_webview_155 = (
            "; wv)" in self.user_agent
            and "Chrome/155." in self.user_agent
            and "Instagram 451." in self.user_agent
            and "Android" in self.user_agent
        )

    def _effective_http_preset(self) -> str:
        # The measured profile belongs to this precise Realme build only.
        # Preserve /ua_set and explicit http_preset overrides. Other devices
        # may advertise different trust anchors even on Chrome 155.
        if self.user_agent == DEFAULT_UA and self.http_preset == "chrome-155-android":
            return _register_realme_webview_preset()
        return self.http_preset

    def _webview_headers(self) -> dict[str, str]:
        if not self.is_webview_155:
            return {}
        return {
            "Sec-CH-UA": WEBVIEW_155_SEC_CH_UA,
            "Sec-CH-UA-Mobile": "?1",
            "Sec-CH-UA-Platform": '"Android"',
            "Accept-Language": WEBVIEW_155_ACCEPT_LANGUAGE,
        }

    def _headers(self, referer: str, session: Session, *, api: bool = False) -> dict[str, str]:
        headers = {
            "User-Agent": self.user_agent,
            "Referer": referer,
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
            "Accept": "application/json, text/plain, */*" if api else "text/html,application/xhtml+xml,*/*;q=0.8",
        }
        headers.update(self._webview_headers())
        if api:
            # Instagram's web XHR may use XMLHttpRequest; a navigation capture
            # alone does not establish headers for every JS API request.
            headers["X-IG-App-ID"] = WEB_APP_ID
            headers["X-Requested-With"] = "XMLHttpRequest"
            try:
                cookie = session.get_cookie("csrftoken")
                csrf = cookie.value if cookie else None
            except Exception:
                csrf = None
            if csrf:
                headers["X-CSRFToken"] = csrf
        elif self.is_webview_155:
            # Observed on a document navigation in the Instagram app WebView.
            headers["Accept"] = WEBVIEW_155_NAV_ACCEPT
            headers["X-Requested-With"] = "com.instagram.android"
        return headers

    def _cdn_headers(self, referer: str) -> dict[str, str]:
        # Captured Client Hints are valid WebView identity headers, but the
        # captured NAVIGATION-specific Accept/X-Requested-With are not reused.
        headers = {"Referer": referer, "Accept": "*/*", "User-Agent": self.user_agent}
        headers.update(self._webview_headers())
        return headers

    async def _get(self, session: Session, url: str, referer: str, *, api: bool = False) -> Any:
        if not _instagram_host(url):
            raise InstagramUnsupportedUrlError("Запрос к постороннему домену заблокирован.")
        try:
            # HTTPCloak get_async() uses a native asynchronous Go transport.
            # Session timeout is in seconds; this request uses the session default.
            response = await session.get_async(
                url, headers=self._headers(referer, session, api=api), allow_redirects=False
            )
        except Exception as exc:
            raise InstagramDownloadError(f"Сетевая ошибка Instagram: {type(exc).__name__}") from exc
        _response_problem(response)
        if int(response.status_code) in (301, 302, 303, 307, 308):
            # Redirects are inspected explicitly, not blindly followed outside Instagram.
            return response
        if len(response.content) > MAX_JSON_BYTES:
            raise InstagramDownloadError("Instagram прислал слишком большой ответ с метаданными.")
        return response

    async def _resolve(self, session: Session, target: InstagramTarget) -> InstagramTarget:
        if target.kind != "share":
            return target
        current = target.url
        for _ in range(4):
            response = await self._get(session, current, current)
            if 300 <= response.status_code < 400:
                location = _header(response, "Location")
                next_url = urljoin(current, location)
                if not _instagram_host(next_url):
                    raise InstagramUnsupportedUrlError("Ссылка ведёт за пределы Instagram.")
                current = next_url
            else:
                current = str(response.url)
            try:
                parsed = _parse_target(current)
                if parsed.kind != "share":
                    return parsed
            except InstagramUnsupportedUrlError:
                pass
        raise InstagramUnsupportedUrlError("Не удалось раскрыть Instagram share-ссылку.")

    async def _api(self, session: Session, url: str, referer: str) -> Any:
        response = await self._get(session, url, referer, api=True)
        if int(response.status_code) in (301, 302, 303, 307, 308):
            raise InstagramAuthRequiredError("Instagram требует вход в аккаунт.")
        try:
            payload = response.json()
        except (ValueError, TypeError):
            if _is_login_wall(response.text):
                raise InstagramAuthRequiredError("Instagram вернул страницу входа вместо данных.")
            raise InstagramDownloadError("Instagram вернул не-JSON ответ API.") from None
        _payload_problem(payload)
        return payload

    async def _browser_session_health(self, session: Session, referer: str) -> str:
        """Browser identity probe recovered and *live-tested* by the APK workbench.

        GET /api/v1/accounts/edit/web_form_data/ is for www browser cookies,
        whereas /accounts/current_user/ belongs to native Android sessions.
        A 200 alone is NOT proof: require the private form-data identity fields.
        Only use this probe after a failed media request, to avoid extra load.
        """
        try:
            response = await self._get(
                session, "https://www.instagram.com/api/v1/accounts/edit/web_form_data/", referer, api=True
            )
            if int(response.status_code) in (301, 302, 303, 307, 308):
                location = _header(response, "Location")
                if "/challenge/" in location or "/checkpoint/" in location:
                    raise InstagramChallengeError("Instagram требует подтверждения аккаунта.")
                return "invalid" if "/accounts/login" in location else "unknown"
            if "json" not in _header(response, "Content-Type").lower():
                return "unknown"
            payload = response.json()
            _payload_problem(payload)
            if not isinstance(payload, dict) or payload.get("status") != "ok":
                return "unknown"
            form = payload.get("form_data")
            if (
                isinstance(form, dict)
                and isinstance(form.get("username"), str)
                and form["username"].strip()
                and "email" in form
                and "phone_number" in form
            ):
                return "valid"
        except InstagramAuthRequiredError:
            return "invalid"
        except (InstagramRateLimitedError, InstagramChallengeError):
            raise
        except (InstagramDownloadError, ValueError, TypeError):
            pass
        return "unknown"

    async def _media_info(self, session: Session, target: InstagramTarget) -> InstagramPost:
        code = target.code
        media_id = code if target.kind == "story" else str(_shortcode_id(code))
        payload = await self._api(session, f"https://www.instagram.com/api/v1/media/{media_id}/info/", target.url)
        return _post_from_json(payload, media_id, target.url)

    async def _stories(self, session: Session, target: InstagramTarget) -> InstagramPost:
        if target.kind == "story":
            return await self._media_info(session, target)
        if target.kind == "highlight":
            reel_id = f"highlight:{target.code}"
        else:
            # This lookup is known to be intermittently rate-limited by Instagram.
            payload = await self._api(
                session,
                f"https://www.instagram.com/api/v1/users/web_profile_info/?username={target.username}",
                target.url,
            )
            user = (payload.get("data") or {}).get("user") if isinstance(payload, dict) else None
            if not isinstance(user, dict) or not user.get("id"):
                raise InstagramNoMediaError("Instagram не отдал ID профиля для Stories.")
            reel_id = str(user["id"])
        from urllib.parse import quote

        payload = await self._api(
            session,
            f"https://www.instagram.com/api/v1/feed/reels_media/?reel_ids={quote(reel_id, safe='')}",
            target.url,
        )
        nodes: list[dict[str, Any]] = []
        if isinstance(payload, dict):
            reels = payload.get("reels")
            if isinstance(reels, dict):
                for reel in reels.values():
                    if isinstance(reel, dict) and isinstance(reel.get("items"), list):
                        nodes.extend(n for n in reel["items"] if isinstance(n, dict))
            if isinstance(payload.get("items"), list):
                nodes.extend(n for n in payload["items"] if isinstance(n, dict))
        media: list[InstagramMedia] = []
        caption = None
        for node in nodes:
            post = _post_from_json({"items": [node]}, str(node.get("pk") or node.get("id") or ""), target.url)
            caption = caption or post.caption
            for item in post.media:
                media.append(InstagramMedia(item.url, item.is_video, len(media) + 1))
        return InstagramPost(target.code, target.url, caption, media)

    async def _post(self, session: Session, target: InstagramTarget, *, authenticated: bool) -> InstagramPost:
        if target.kind in {"story", "stories", "highlight"}:
            if not authenticated:
                raise InstagramAuthRequiredError("Stories и Highlights требуют Instagram cookies.")
            return await self._stories(session, target)

        errors: list[InstagramDownloadError] = []
        cover: InstagramPost | None = None
        # Prefer browser navigation/embeds before hitting an Android-style
        # media/info route. The APK workbench only establishes that
        # media/%s/info/ exists in Android bytecode, NOT that www Instagram
        # supports the endpoint using WebView cookies.
        for page in (f"{target.url.rstrip('/')}/embed/captioned/", target.url):
            try:
                response = await self._get(session, page, target.url)
                if 300 <= response.status_code < 400:
                    raise InstagramAuthRequiredError("Instagram вернул редирект на авторизацию.")
                if _is_login_wall(response.text):
                    raise InstagramAuthRequiredError("Instagram вернул страницу авторизации.")
                post = _post_from_html(response.text, target.code, target.url)
                if post.media:
                    # JSON carousels and OG video for Reels are sufficient;
                    # a single OG image is only a possible carousel cover.
                    if post.complete_metadata or (target.kind == "reel" and any(m.is_video for m in post.media)):
                        return post
                    cover = cover or post
            except (InstagramRateLimitedError, InstagramChallengeError):
                raise
            except InstagramDownloadError as exc:
                errors.append(exc)
            if cover and target.kind == "reel":
                # A Reel thumbnail alone is never a video. Try next source.
                continue

        # Private-style media/info is a LAST resort. Route literal exists in
        # Instagram 451's APK, but its www browser contract is unverified.
        # Only probe it once per post, not once per HTML failure.
        if authenticated or cover is None:
            try:
                post = await self._media_info(session, target)
                if post.media and (target.kind != "reel" or any(m.is_video for m in post.media)):
                    return post
            except (InstagramRateLimitedError, InstagramChallengeError):
                raise
            except InstagramDownloadError as exc:
                errors.append(exc)

        if cover and target.kind != "reel":
            return cover
        if authenticated and any(isinstance(e, InstagramAuthRequiredError) for e in errors):
            status = await self._browser_session_health(session, target.url)
            if status == "valid":
                raise InstagramNoMediaError(
                    "Instagram принял cookies при проверке профиля, но медиа недоступно: API ограничен либо публикация закрыта."
                )
            if status == "invalid":
                raise InstagramAuthRequiredError(
                    "Instagram отклонил проверку браузерной авторизации. Проверь cookies в официальном браузере."
                )
            raise InstagramDownloadError(
                "Instagram не отдал медиа; браузерную сессию проверить не удалось. Cookies не удалены."
            )
        if any(isinstance(e, InstagramAuthRequiredError) for e in errors):
            raise InstagramAuthRequiredError("Instagram не отдал медиа: нужна авторизация или публикация закрыта.")
        if errors:
            logger.debug("Instagram extraction paths failed: {}", [type(e).__name__ for e in errors])
        raise InstagramNoMediaError("Не удалось извлечь медиа: публикация недоступна или Instagram изменил API.")

    def _download_one_sync(self, session: Session, media: InstagramMedia, referer: str, temp: Path) -> int:
        """Read CDN bytes in a worker thread; never buffer a whole video in RAM."""
        written = 0
        try:
            # Request timeout for HTTPCloak is in MILLISECONDS, unlike Session(timeout).
            with session.get_stream(
                media.url,
                allow_redirects=False,
                timeout=60_000,
                headers=self._cdn_headers(referer),
            ) as response:
                status = int(response.status_code)
                if status == 429:
                    raise InstagramRateLimitedError("CDN HTTP 429: Instagram временно ограничил запросы.")
                if status != 200:
                    raise InstagramDownloadError(f"CDN HTTP {status}.")
                length = _header(response, "Content-Length")
                if length and int(length) > MAX_MEDIA_BYTES:
                    raise InstagramMediaTooLargeError("Медиа превышает лимит Telegram.")
                ctype = _header(response, "Content-Type").lower()
                if ctype.startswith("text/") or "json" in ctype:
                    raise InstagramDownloadError("CDN вернул текст вместо медиа.")
                if not _cdn_host(str(response.url)):
                    raise InstagramDownloadError("CDN перенаправил на посторонний домен.")
                with temp.open("wb") as handle:
                    for chunk in response.iter_content(64 * 1024):
                        if not chunk:
                            continue
                        written += len(chunk)
                        if written > MAX_MEDIA_BYTES:
                            raise InstagramMediaTooLargeError("Медиа превышает лимит Telegram.")
                        handle.write(chunk)
            if written < 12 or not _check_file(temp, media.is_video):
                raise InstagramDownloadError("CDN вернул пустой файл или некорректный формат.")
            return written
        except BaseException:
            temp.unlink(missing_ok=True)
            raise

    async def _download_files(self, session: Session, post: InstagramPost, path: Path) -> InstagramPost:
        path.mkdir(parents=True, exist_ok=True)
        prefix = f"{post.shortcode}_{secrets.token_hex(7)}"
        created: list[Path] = []
        oversize = False
        try:
            for media in post.media:
                if not _cdn_host(media.url):
                    continue
                dest = path / f"{prefix}_{media.index:02}{_file_extension(media)}"
                temp = path / f".{prefix}_{media.index:02}.part"
                try:
                    await asyncio.to_thread(self._download_one_sync, session, media, post.canonical_url, temp)
                    temp.replace(dest)
                    created.append(dest)
                except InstagramRateLimitedError:
                    raise
                except InstagramMediaTooLargeError:
                    oversize = True
                except Exception as exc:
                    logger.warning("Instagram media {} failed: {}", media.index, type(exc).__name__)
                finally:
                    temp.unlink(missing_ok=True)
            if not created:
                message = (
                    "Медиа превышает лимит размера Telegram."
                    if oversize
                    else "Instagram выдал ссылки, но CDN не отдал корректные медиафайлы."
                )
                raise InstagramNoMediaError(message)
            if post.caption:
                caption_path = path / f"{prefix}.txt"
                caption_path.write_text(post.caption[:8000], encoding="utf-8")
                created.append(caption_path)
            post.download_id = prefix
            return post
        except BaseException:
            for file in created:
                file.unlink(missing_ok=True)
            raise

    async def download(self, url: str, download_path: Path = DOWNLOADS_DIR, use_cookies: bool = False) -> InstagramPost:
        from tg_bot.utils.cookies_manager import cookies_manager

        target = _parse_target(url)
        redis = None
        try:
            from core.redis_client import get_redis

            redis = await get_redis()
            if await redis.exists("instagram:global:cooldown"):
                raise InstagramRateLimitedError("Instagram HTTP 429: включена временная пауза запросов.")
        except InstagramRateLimitedError:
            raise
        except Exception:
            logger.warning("Instagram Redis cooldown unavailable; continuing without rate-state persistence")
        slot = None
        cookie_path = None
        try:
            if use_cookies:
                selected = await cookies_manager.get_pooled_cookies("instagram")
                if selected:
                    slot, cookie_path = selected
            # Realme WebView profile is registered lazily, only for the exact
            # measured UA. This remains a browser client, NOT native Android API.
            session = Session(
                preset=self._effective_http_preset(), timeout=TIMEOUT, retry=0, allow_redirects=False, prefer_ipv4=True
            )
            try:
                authenticated = False
                original_cookies = None
                if cookie_path:
                    original_cookies = cookie_path.read_text(encoding="utf-8")
                    authenticated = _load_cookies(session, original_cookies)
                try:
                    target = await self._resolve(session, target)
                    post = await self._post(session, target, authenticated=authenticated)
                    saved = await self._download_files(session, post, download_path)
                    if slot and authenticated and original_cookies:
                        await cookies_manager.refresh_cookies_from_session(
                            slot, original_cookies, session.get_cookies()
                        )
                    return saved
                except InstagramRateLimitedError:
                    if redis:
                        try:
                            await redis.setex("instagram:global:cooldown", 900, "1")
                        except Exception:
                            logger.warning("Unable to store Instagram global backoff")
                    raise
                except (InstagramAuthRequiredError, InstagramChallengeError):
                    if slot and authenticated:
                        # Temporarily suspend only on confirmed auth rejection/challenge.
                        from sys import exc_info

                        reason = exc_info()[1]
                        if isinstance(reason, InstagramChallengeError):
                            await cookies_manager.mark_cookie_cooldown(slot, seconds=3600)
                    raise
            finally:
                session.close()
        finally:
            if cookie_path:
                cookie_path.unlink(missing_ok=True)


async def get_instagram_web_parser() -> InstagramWebDownloader:
    try:
        from tg_bot.services.instagram_ua_service import instagram_ua_service

        user_agent = await instagram_ua_service.get_user_agent()
    except Exception:
        user_agent = DEFAULT_UA
    return InstagramWebDownloader(user_agent=user_agent)


async def init_instagram_web_parser() -> InstagramWebDownloader:
    return await get_instagram_web_parser()


async def reset_instagram_web_parser() -> None:
    """Compatibility no-op: avoids losing cookies or clearing protective backoff."""
    return None


async def get_instagram_shortcode(url: str) -> str | None:
    try:
        target = _parse_target(url)
        if target.kind != "share":
            return target.code
        client = await get_instagram_web_parser()
        session = Session(preset=client._effective_http_preset(), timeout=TIMEOUT, retry=0, allow_redirects=False)
        try:
            return (await client._resolve(session, target)).code
        finally:
            session.close()
    except InstagramDownloadError:
        return None


async def download_instagram_web_media(url: str, use_cookies: bool = False) -> tuple[str | None, str | None]:
    try:
        downloader = await get_instagram_web_parser()
        post = await downloader.download(url, DOWNLOADS_DIR, use_cookies=use_cookies)
        return post.download_id, None
    except InstagramRateLimitedError as exc:
        return None, f"Instagram: ❌ {exc}"
    except InstagramChallengeError as exc:
        return None, f"Instagram: ❌ {exc}"
    except InstagramAuthRequiredError as exc:
        return None, f"Instagram: ❌ {exc} Проверь cookies через /set_cookies."
    except InstagramDownloadError as exc:
        return None, f"Instagram: ❌ {exc}"
    except Exception as exc:
        logger.exception("Unexpected Instagram download exception: {}", type(exc).__name__)
        return None, "Instagram: ❌ Неожиданная ошибка загрузчика."


async def select_instagram_media(shortcode: str, download_path: Path = DOWNLOADS_DIR) -> dict[str, list[Path] | str]:
    # Legacy compatibility: a 'shortcode' may now be a unique per-request download key.
    paths = sorted(
        p
        for p in download_path.iterdir()
        if p.is_file() and (p.name.startswith(shortcode + ".") or p.name.startswith(shortcode + "_"))
    )
    images = [p for p in paths if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}]
    videos = [p for p in paths if p.suffix.lower() in {".mp4", ".mov"}]
    captions = [p for p in paths if p.suffix.lower() == ".txt"]
    return {"images": images, "videos": videos, "caption": captions[0].read_text(encoding="utf-8") if captions else ""}


DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)


async def get_instagram_session_status() -> dict[str, Any]:
    """Local diagnostic only. No network request to Instagram, no cookie values."""
    from tg_bot.utils.cookies_manager import cookies_manager

    slots = await cookies_manager.list_available_cookies()
    cooling: list[str] = []
    global_cooldown = False
    try:
        from core.redis_client import get_redis

        redis = await get_redis()
        global_cooldown = bool(await redis.exists("instagram:global:cooldown"))
        for slot in slots:
            if slot == "instagram" or slot.startswith("instagram:"):
                if await redis.exists(f"cookies:{slot}:cooldown"):
                    cooling.append(slot)
    except Exception:
        return {
            "slots": [s for s in slots if s.startswith("instagram")],
            "redis": False,
            "cooling": [],
            "rate_limited": False,
        }
    return {
        "slots": [s for s in slots if s.startswith("instagram")],
        "redis": True,
        "cooling": cooling,
        "rate_limited": global_cooldown,
    }
