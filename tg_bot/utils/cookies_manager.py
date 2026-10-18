"""Redis-backed Netscape cookies, isolated temporary files, and per-slot backoff.

Existing public methods/Redis keys retained for yt-dlp, gallery-dl and Telegram commands.
Redis is an external TLS/password-protected service configured by core.redis_client.
"""

from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

from core.config import STORAGE_DIR
from core.logger import logger
from core.redis_client import get_redis

COOKIES_DIR = STORAGE_DIR / "cookies"
COOKIES_DIR.mkdir(parents=True, exist_ok=True)
COOKIES_REGEX = re.compile(
    r"Use --cookies-from-browser|Account authentication is required|Use --cookies|login required|requires login",
    re.IGNORECASE,
)


class CookiesManager:
    COOKIES_EXPIRE_DAYS = 30

    @staticmethod
    def get_site_name(url: str) -> str:
        try:
            host = (urlparse(url).hostname or "").lower().removeprefix("www.")
            return host.split(".")[0]
        except ValueError:
            return ""

    @staticmethod
    def extract_site_from_cookies_file(cookies_path: Path) -> str | None:
        """Get site from Netscape format (including #HttpOnly_ domain rows)."""
        try:
            for line in cookies_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("#HttpOnly_"):
                    line = line.removeprefix("#HttpOnly_")
                elif not line.strip() or line.startswith("#"):
                    continue
                parts = line.split("\t", 6)
                if len(parts) != 7:
                    continue
                domain = parts[0].lower().lstrip(".")
                if domain.endswith(".instagram.com") or domain == "instagram.com":
                    return "instagram"
                if domain and re.fullmatch(r"[a-z\d.-]+", domain):
                    return domain.split(".")[0]
        except (OSError, UnicodeError) as exc:
            logger.error("Could not extract cookie domain: {}", type(exc).__name__)
        return None

    @staticmethod
    def _key(site: str) -> str:
        if not re.fullmatch(r"[a-z0-9_-]+(?::[1-4])?", site.lower()):
            raise ValueError("Invalid cookies site slot")
        return "cookies:" + site.lower()

    @staticmethod
    async def save_cookies(site_name: str, cookies_path: Path) -> bool:
        try:
            content = cookies_path.read_text(encoding="utf-8")
            if len(content) > 256_000:
                return False
            key = CookiesManager._key(site_name)
            if key == "cookies:instagram" or key.startswith("cookies:instagram:"):
                # Keep raw cookies untouched; ensure a real browser session was exported.
                rows = [line.removeprefix("#HttpOnly_") for line in content.splitlines()]
                has_session = any(
                    len(parts := row.split("\t", 6)) == 7
                    and parts[5] == "sessionid"
                    and parts[6]
                    and (parts[0].lstrip(".").lower() == "instagram.com" or parts[0].lower().endswith(".instagram.com"))
                    for row in rows
                )
                if not has_session:
                    logger.warning("Refusing Instagram cookies without sessionid")
                    return False
            redis = await get_redis()
            ttl = int(timedelta(days=CookiesManager.COOKIES_EXPIRE_DAYS).total_seconds())
            await redis.setex(key, ttl, content)
            await redis.setex(f"{key}:timestamp", ttl, datetime.now(timezone.utc).isoformat())
            await redis.delete(f"{key}:cooldown")
            logger.info("Saved cookies for slot {}", site_name)
            return True
        except Exception as exc:
            logger.error("Cookie storage failed: {}", type(exc).__name__)
            return False

    @staticmethod
    async def get_cookies(site_name: str) -> Path | None:
        """Return a unique mode-0600 Netscape file; caller MUST unlink it."""
        try:
            redis = await get_redis()
            content = await redis.get(CookiesManager._key(site_name))
            if not content:
                return None
            fd, filename = tempfile.mkstemp(
                prefix=f"{site_name.lower().replace(':', '_')}_", suffix=".txt", dir=COOKIES_DIR
            )
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(content)
            except BaseException:
                os.unlink(filename)
                raise
            return Path(filename)
        except Exception as exc:
            logger.error("Could not read cookies for {}: {}", site_name, type(exc).__name__)
            return None

    @staticmethod
    async def mark_cookies_expired(site_name: str) -> bool:
        # Kept for explicit callers; network errors must NOT invoke this method.
        return await CookiesManager.delete_cookies(site_name)

    @staticmethod
    def has_cookies_error(error: str) -> bool:
        return bool(COOKIES_REGEX.search(error))

    @staticmethod
    def _pool_slots(site_root: str) -> list[str]:
        root = site_root.lower()
        if not re.fullmatch(r"[a-z0-9_-]+", root):
            return []
        return [root, *[f"{root}:{index}" for index in range(1, 5)]]

    @staticmethod
    async def has_pooled_cookies(site_root: str) -> bool:
        try:
            redis = await get_redis()
            for slot in CookiesManager._pool_slots(site_root):
                if await redis.exists(CookiesManager._key(slot)):
                    return True
        except Exception as exc:
            logger.debug("Cookie pool lookup failed: {}", type(exc).__name__)
        return False

    @staticmethod
    async def get_pooled_cookies(site_root: str, exclude: set[str] | None = None) -> tuple[str, Path] | None:
        """Prefer the primary account; never rotate on rate-limits or transient errors."""
        try:
            redis = await get_redis()
            for slot in CookiesManager._pool_slots(site_root):
                if exclude and slot in exclude:
                    continue
                if await redis.exists(f"{CookiesManager._key(slot)}:cooldown"):
                    continue
                path = await CookiesManager.get_cookies(slot)
                if path:
                    return slot, path
        except Exception as exc:
            logger.warning("Cookie pool access failed: {}", type(exc).__name__)
        return None

    @staticmethod
    async def mark_cookie_cooldown(slot: str, seconds: int = 900) -> None:
        try:
            redis = await get_redis()
            await redis.setex(f"{CookiesManager._key(slot)}:cooldown", max(60, seconds), "1")
            logger.warning("Cookies slot {} temporarily suspended for {} seconds", slot, seconds)
        except Exception as exc:
            logger.error("Cookie cooldown failed: {}", type(exc).__name__)

    @staticmethod
    async def refresh_cookies_from_session(site_name: str, old_content: str, jar: object) -> bool:
        """Persist rotated cookies and renew Redis TTL on successful authenticated use.

        Optimistic WATCH prevents an older in-flight request overwriting /set_cookies.
        """
        try:
            import time

            # HTTPCloak Cookie.expires is an RFC1123 string, NOT a Unix timestamp.
            # Preserve the original #HttpOnly_ Netscape prefix when refreshing.
            existing: dict[tuple[str, str, str], tuple[list[str], bool]] = {}
            for raw in old_content.splitlines():
                http_only = raw.startswith("#HttpOnly_")
                stripped = raw.removeprefix("#HttpOnly_")
                cols = stripped.split("\t", 6)
                if len(cols) == 7:
                    existing[(cols[0].lstrip(".").lower(), cols[2], cols[5])] = (cols, http_only)
            changed = False
            for cookie in jar:  # HTTPCloak get_cookies(): list[Cookie]
                domain = str(cookie.domain).lstrip(".").lower()
                if domain != "instagram.com" and not domain.endswith(".instagram.com"):
                    continue
                if not cookie.secure or not cookie.name or not cookie.value:
                    continue
                key = (domain, cookie.path or "/", cookie.name)
                previous = existing.get(key)
                expires = getattr(cookie, "expires", None)
                expiry = 0
                if expires:
                    try:
                        if isinstance(expires, (int, float)) or str(expires).isdigit():
                            expiry = int(expires)
                        else:
                            expiry = int(parsedate_to_datetime(str(expires)).timestamp())
                    except (ValueError, TypeError, OverflowError):
                        logger.warning("Invalid Instagram cookie expiry ignored for {}", cookie.name)
                        continue
                if not expiry and int(getattr(cookie, "max_age", 0) or 0) > 0:
                    expiry = int(time.time()) + int(cookie.max_age)
                if expiry and expiry < time.time():
                    continue
                if not expiry and previous and previous[0][4].isdigit():
                    # Keep the original expiration when the new representation omits it.
                    expiry = int(previous[0][4])
                domain_field = cookie.domain if cookie.domain.startswith(".") else "." + domain
                columns = [domain_field, "TRUE", cookie.path or "/", "TRUE", str(expiry), cookie.name, cookie.value]
                http_only = bool(getattr(cookie, "http_only", False)) or bool(previous and previous[1])
                if previous is None or previous[0] != columns or previous[1] != http_only:
                    existing[key] = (columns, http_only)
                    changed = True
            if not any(cols[5] == "sessionid" and cols[6] for cols, _ in existing.values()):
                return False
            body = None
            if changed:
                body = (
                    "# Netscape HTTP Cookie File\n"
                    + "\n".join(
                        ("#HttpOnly_" if http_only else "") + "\t".join(cols) for cols, http_only in existing.values()
                    )
                    + "\n"
                )
                if len(body) > 256_000:
                    return False
            redis = await get_redis()
            key = CookiesManager._key(site_name)
            ttl = int(timedelta(days=CookiesManager.COOKIES_EXPIRE_DAYS).total_seconds())
            async with redis.pipeline(transaction=True) as pipe:
                await pipe.watch(key)
                if await pipe.get(key) != old_content:
                    return False
                pipe.multi()
                if body is None:
                    pipe.expire(key, ttl)
                else:
                    pipe.setex(key, ttl, body)
                pipe.setex(f"{key}:timestamp", ttl, datetime.now(timezone.utc).isoformat())
                await pipe.execute()
            return True
        except Exception as exc:
            logger.warning("Could not refresh Instagram cookies in Redis: {}", type(exc).__name__)
            return False

    @staticmethod
    async def list_available_cookies() -> dict[str, str]:
        result: dict[str, str] = {}
        try:
            redis = await get_redis()
            async for key in redis.scan_iter(match="cookies:*"):
                if key.endswith((":timestamp", ":cooldown", ":rr", ":invalid")):
                    continue
                slot = key.removeprefix("cookies:")
                if not re.fullmatch(r"[a-z0-9_-]+(?::[1-4])?", slot):
                    continue
                timestamp = await redis.get(f"{key}:timestamp")
                result[slot] = timestamp or "unknown"
        except Exception as exc:
            logger.error("Failed to list cookies: {}", type(exc).__name__)
        return result

    @staticmethod
    async def delete_cookies(site_name: str) -> bool:
        try:
            redis = await get_redis()
            key = CookiesManager._key(site_name)
            await redis.delete(key, f"{key}:timestamp", f"{key}:cooldown")
            return True
        except Exception as exc:
            logger.error("Delete cookies failed: {}", type(exc).__name__)
            return False


cookies_manager = CookiesManager()
