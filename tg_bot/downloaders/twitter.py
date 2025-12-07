import asyncio
import json
import os
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

import telegramify_markdown
from curl_cffi.requests import AsyncSession
from redis.asyncio import Redis

from core.config import DOWNLOADS_DIR
from core.logger import logger
from core.redis_client import get_redis

AUTH_TOKEN = os.getenv("TWITTER_AUTH_TOKEN")
CSRF_TOKEN = os.getenv("TWITTER_CT0")
# Redis keys and TTLs
REDIS_KEY_AUTH = "twitter_auth_token"
REDIS_KEY_CT0 = "twitter_ct0"
REDIS_KEY_GUEST = "twitter:guest_token"
GUEST_TOKEN_TTL = 60 * 60  # 1 hour


async def _load_tokens_from_redis() -> None:
    """Load auth cookies from Redis if available."""
    global AUTH_TOKEN, CSRF_TOKEN
    try:
        redis: Redis = await get_redis()
        auth = await redis.get(REDIS_KEY_AUTH)
        ct0 = await redis.get(REDIS_KEY_CT0)
        if auth and ct0:
            # Redis returns bytes by default; normalize to str
            auth = _b2s(auth)
            ct0 = _b2s(ct0)
            AUTH_TOKEN = auth  # type: ignore[assignment]
            CSRF_TOKEN = ct0  # type: ignore[assignment]
            logger.info("Loaded Twitter tokens from Redis")
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to load tokens from Redis: {e}")


def set_auth_token(token: str | None) -> None:
    """Set the auth token used for Twitter requests."""
    global AUTH_TOKEN
    AUTH_TOKEN = token


def set_csrf_token(token: str | None) -> None:
    """Set the CSRF token used for Twitter requests."""
    global CSRF_TOKEN
    CSRF_TOKEN = token


TWITTER_REGEX = re.compile(r"https?://(?:www\.)?(?:x|twitter)\.com/[^/]+/status/(?P<id>\d+)")

BEARER_TOKEN = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)

TWITTER_RESULT_BY_REST_ID_QUERY_DEFAULT = "gbBo18jnmlP-Na0tVYgiZA"

TWITTER_FEATURES_DEFAULT = {
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "premium_content_api_read_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
    "responsive_web_grok_analyze_post_followups_enabled": False,
    "responsive_web_jetfuel_frame": False,
    "responsive_web_grok_share_attachment_enabled": True,
    "articles_preview_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "tweet_awards_web_tipping_enabled": False,
    "responsive_web_grok_show_grok_translated_post": False,
    "responsive_web_grok_analysis_button_from_backend": False,
    "creator_subscriptions_quote_tweet_preview_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "payments_enabled": False,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "rweb_tipjar_consumption_enabled": True,
    "verified_phone_label_enabled": False,
    "responsive_web_grok_image_annotation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
    "responsive_web_grok_community_note_auto_translation_is_enabled": False,
    "rweb_xchat_enabled": False,
    "responsive_web_grok_imagine_annotation": True,
    "responsive_web_grok_imagine_annotation_enabled": True,
}

TWITTER_FIELD_TOGGLES_DEFAULT = {
    "withArticleRichContentState": True,
    "withArticlePlainText": False,
    "withGrokAnalyze": False,
    "withDisallowedReplyControls": False,
}

## Removed password-based login flow (username/password onboarding)


# ==== Dynamic GraphQL resolving for TweetResultByRestId (main.js + HTML) ====


@dataclass
class TwitterQuerySpec:
    operation: str
    query_id: str
    bearer: str
    features: dict
    field_toggles: dict


class TwitterGQLResolver:
    """
    Resolves queryId, bearer and features for operation TweetResultByRestId from
    main.js and HTML, caches in Redis, and refreshes on feature errors.
    """

    CACHE_KEY = "twitter:gql:TweetResultByRestId:spec"
    CACHE_TTL = 6 * 60 * 60  # 6 hours

    def __init__(self) -> None:
        self.op_name = "TweetResultByRestId"

    async def _get_cache(self) -> dict | None:
        try:
            redis: Redis = await get_redis()
            raw = await redis.get(self.CACHE_KEY)
            if raw:
                return json.loads(raw)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"QID cache read failed: {e}")
        return None

    async def _set_cache(self, payload: dict) -> None:
        try:
            redis: Redis = await get_redis()
            await redis.set(self.CACHE_KEY, json.dumps(payload, separators=(",", ":")), ex=self.CACHE_TTL)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"QID cache write failed: {e}")

    async def invalidate(self) -> None:
        try:
            redis: Redis = await get_redis()
            await redis.delete(self.CACHE_KEY)
        except Exception:
            pass

    @staticmethod
    def _find_main_js_url(html: str) -> str | None:
        m = re.search(
            r'(?i)(?:src|href)\s*=\s*"(https:\/\/abs.twimg.com\/responsive-web\/client-web\/main.[A-Za-z0-9]+.js)"',
            html,
        )
        return m.group(1) if m else None

    @staticmethod
    def _parse_bearer(text: str) -> str | None:
        """Return Bearer token value (without the 'Bearer ' prefix), prefer the longest match."""
        try:
            matches = re.findall(r"Bearer\s+([A-Za-z0-9%\-]+)", text)
            if matches:
                return max(matches, key=len)
            m2 = re.search(r'authorization"?\s*:\s*"Bearer\s+([^\"]+)', text)
            return m2.group(1).strip() if m2 else None
        except Exception:
            return None

    @staticmethod
    def _parse_export_block(js: str, op_name: str) -> tuple[str | None, list[str], list[str]]:
        """
        Finds queryId and metadata featureSwitches/fieldToggles for the operation.
        Returns:
            tuple: (query_id, feature_switches_list, field_toggles_list)
        """
        qid = None
        m = re.search(r'queryId:"(?P<id>[^"]+)",operationName:"%s"' % re.escape(op_name), js)
        if m:
            qid = m.group("id")

        fs = []
        mfs = re.search(
            r'operationName:"%s".{0,500}?metadata:\{[^}]*featureSwitches:\[(?P<list>[^\]]*)\]' % re.escape(op_name),
            js,
            flags=re.DOTALL,
        )
        if mfs:
            fs = re.findall(r'"([^"]+)"', mfs.group("list"))

        ft = []
        mft = re.search(
            r'operationName:"%s".{0,500}?metadata:\{[^}]*fieldToggles:\[(?P<list>[^\]]*)\]' % re.escape(op_name),
            js,
            flags=re.DOTALL,
        )
        if mft:
            ft = re.findall(r'"([^"]+)"', mft.group("list"))

        return qid, fs, ft

    @staticmethod
    def _parse_initial_state_features(html: str) -> dict:
        """
        Extracts featureSwitch.defaultConfig from window.__INITIAL_STATE__ in HTML.
        Returns mapping name -> bool.
        """
        try:
            m = re.search(r"window\.__INITIAL_STATE__\s*=\s*({.*?});", html, flags=re.DOTALL)
            if not m:
                return {}
            state = json.loads(m.group(1))
            cfg = (state.get("featureSwitch", {}) or {}).get("defaultConfig", {})
            out = {}
            for k, v in cfg.items():
                if isinstance(v, dict) and "value" in v:
                    out[k] = bool(v.get("value"))
            return out
        except Exception as e:  # noqa: BLE001
            logger.debug(f"parse_initial_state failed: {e}")
            return {}

    async def get_spec(self, session: AsyncSession, html: str) -> TwitterQuerySpec | None:
        cached = await self._get_cache()
        if cached:
            return TwitterQuerySpec(
                operation=self.op_name,
                query_id=cached["query_id"],
                bearer=cached.get("bearer") or BEARER_TOKEN,
                features=cached.get("features") or {},
                field_toggles=cached.get("field_toggles") or {},
            )

        main_js_url = self._find_main_js_url(html)
        if not main_js_url:
            logger.error("Не удалось найти main.js, используем DEFAULT fallback")
            return TwitterQuerySpec(
                operation=self.op_name,
                query_id=TWITTER_RESULT_BY_REST_ID_QUERY_DEFAULT,
                bearer=BEARER_TOKEN,
                features=dict(TWITTER_FEATURES_DEFAULT),
                field_toggles=dict(TWITTER_FIELD_TOGGLES_DEFAULT),
            )
        resp = await session.get(main_js_url, impersonate="chrome")
        js = resp.text

        qid, feature_switches, field_toggles_list = self._parse_export_block(js, self.op_name)
        if not qid:
            logger.error("Не удалось найти queryId для TweetResultByRestId, используем DEFAULT fallback")
            return TwitterQuerySpec(
                operation=self.op_name,
                query_id=TWITTER_RESULT_BY_REST_ID_QUERY_DEFAULT,
                bearer=BEARER_TOKEN,
                features=dict(TWITTER_FEATURES_DEFAULT),
                field_toggles=dict(TWITTER_FIELD_TOGGLES_DEFAULT),
            )

        # Prefer bearer from JS; fallback to HTML; then default
        bearer = self._parse_bearer(js) or self._parse_bearer(html) or BEARER_TOKEN

        default_cfg = self._parse_initial_state_features(html)
        features = {}
        for name in feature_switches or []:
            features[name] = bool(default_cfg.get(name, False))
        if not features:
            features = dict(TWITTER_FEATURES_DEFAULT)

        field_toggles = {k: False for k in (field_toggles_list or [])}
        if not field_toggles:
            field_toggles = dict(TWITTER_FIELD_TOGGLES_DEFAULT)

        spec = TwitterQuerySpec(
            operation=self.op_name,
            query_id=qid,
            bearer=bearer,
            features=features,
            field_toggles=field_toggles,
        )

        await self._set_cache(
            {
                "query_id": spec.query_id,
                "bearer": spec.bearer,
                "features": spec.features,
                "field_toggles": spec.field_toggles,
            }
        )
        return spec


async def _get_guest_token(session: AsyncSession, bearer: str, *, force_refresh: bool = False) -> str | None:
    """Get guest token with small Redis cache and robust fallbacks."""
    try:
        redis: Redis = await get_redis()
        if not force_refresh:
            cached = await redis.get(REDIS_KEY_GUEST)
            if cached:
                return _b2s(cached)
    except Exception:
        # cache errors are non-fatal
        pass

    headers = {
        "Authorization": f"Bearer {bearer}",
        "content-type": "application/json",
        "x-twitter-client-language": "en",
        "x-twitter-active-user": "yes",
        "Referer": "https://x.com/",
        "Origin": "https://x.com",
        "User-Agent": "Mozilla/5.0",
    }
    payload = b"{}"
    urls = (
        "https://api.x.com/1.1/guest/activate.json",
        "https://api.twitter.com/1.1/guest/activate.json",
    )
    last_err = None
    for url in urls:
        try:
            # ensure ct0 cookie/header present for activation, like gallery-dl does
            _ensure_guest_headers(session, headers)
            resp = await session.post(url, headers=headers, data=payload, impersonate="chrome")
            if resp.ok:
                data = resp.json()
                token = data.get("guest_token")
                if token:
                    try:
                        redis = await get_redis()
                        await redis.set(REDIS_KEY_GUEST, token, ex=GUEST_TOKEN_TTL)
                    except Exception:
                        pass
                    # set 'gt' cookie like gallery-dl
                    try:
                        session.cookies.set("gt", token, domain=".x.com")
                    except Exception:
                        try:
                            session.cookies.set("gt", token)
                        except Exception:
                            pass
                    return token
            else:
                last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
    if last_err:
        logger.error(f"Error fetching guest token: {last_err}")
    return None


def _ensure_guest_headers(session: AsyncSession, headers: dict) -> None:
    """Ensure ct0 cookie/header and browser-ish headers for guest requests."""
    # ensure ct0 cookie exists
    ct0 = None
    try:
        c = session.cookies.get("ct0")
        ct0 = c if isinstance(c, str) else getattr(c, "value", None)
    except Exception:
        ct0 = None
    if not ct0:
        ct0 = generate_token(16)
        try:
            session.cookies.set("ct0", ct0, domain=".x.com")
        except Exception:
            try:
                session.cookies.set("ct0", ct0)
            except Exception:
                pass
    headers["x-csrf-token"] = ct0
    headers.setdefault("Accept", "*/*")
    headers.setdefault("content-type", "application/json")
    headers.setdefault("x-twitter-client-language", "en")
    headers.setdefault("x-twitter-active-user", "yes")
    session.cookies.delete("auth_token")  # Remove auth_token from guest!


def generate_token(size: int = 16) -> str:
    """Generate a random hexadecimal token (used for ct0)."""
    return random.getrandbits(size * 8).to_bytes(size, "big").hex()


## Removed password-based login helpers


async def ensure_tokens() -> None:
    """Load existing tokens from Redis/env; no password-based login attempts."""
    await _load_tokens_from_redis()


async def download_twitter_media(
    url: str, download_path: Path = DOWNLOADS_DIR
) -> Tuple[List[Path], List[Path], str | None, str | None]:
    """Download images and videos from a tweet via GraphQL.

    Args:
        url (str): The URL of the tweet to download media from.
        download_path (Path, optional): The directory to save downloaded media. Defaults to DOWNLOADS_DIR.

    Returns:
        tuple (Tuple[List[Path], List[Path], str | None, str | None]): A tuple containing the paths of downloaded images and videos, and any error messages.
    """
    download_path.mkdir(exist_ok=True)
    id_match = TWITTER_REGEX.match(url)
    if not id_match:
        return [], [], None, "Неверная ссылка Twitter"
    tweet_id = id_match.group("id")

    await ensure_tokens()
    data: dict | None = None
    used_guest = False
    async with AsyncSession() as session:
        try:
            html_resp = await session.get(url, impersonate="chrome")
            html = html_resp.text
        except Exception as e:  # noqa: BLE001
            logger.error(f"Twitter HTML fetch error: {e}")
            return [], [], None, "Не удалось получить страницу твита"

        resolver = TwitterGQLResolver()
        spec = await resolver.get_spec(session, html)
        if not spec:
            return [], [], None, "Не удалось получить параметры GraphQL"

        variables = {
            "tweetId": tweet_id,
            "withCommunity": False,
            "includePromotedContent": False,
            "withVoice": False,
        }
        params = {
            "variables": json.dumps(variables, separators=(",", ":")),
            "features": json.dumps(spec.features, separators=(",", ":")),
            "fieldToggles": json.dumps(spec.field_toggles, separators=(",", ":")),
        }

        async def fetch(spec_obj: TwitterQuerySpec, use_auth: bool) -> tuple[dict | None, str | None]:
            hdrs = {
                "Authorization": f"Bearer {spec_obj.bearer}",
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://x.com/",
            }
            if use_auth:
                if not (AUTH_TOKEN and CSRF_TOKEN):
                    return None, "Отсутствуют токены авторизации"
                hdrs["x-csrf-token"] = CSRF_TOKEN
                session.cookies.set("auth_token", AUTH_TOKEN)
                session.cookies.set("ct0", CSRF_TOKEN)
            else:
                guest = await _get_guest_token(session, spec_obj.bearer)
                if not guest:
                    # try to force refresh once
                    guest = await _get_guest_token(session, spec_obj.bearer, force_refresh=True)
                if not guest:
                    return None, "Не удалось получить guest token"
                hdrs["x-guest-token"] = guest
                # align with gallery-dl: ensure ct0 header and set 'gt' cookie for guest
                try:
                    _ensure_guest_headers(session, hdrs)
                except Exception:
                    pass
                try:
                    session.cookies.set("gt", guest, domain=".x.com")
                except Exception:
                    try:
                        session.cookies.set("gt", guest)
                    except Exception:
                        pass
            try:
                # Try both hosts to improve resiliency
                hosts = (
                    "https://api.x.com",
                    "https://api.twitter.com",
                )
                last_err: str | None = None
                for host in hosts:
                    resp = await session.get(
                        f"{host}/graphql/{spec_obj.query_id}/{spec_obj.operation}",
                        params=params,
                        headers=hdrs,
                        impersonate="chrome",
                    )
                    # update ct0 from response cookies if present
                    try:
                        new_ct0 = resp.cookies.get("ct0")
                        if new_ct0:
                            hdrs["x-csrf-token"] = new_ct0
                    except Exception:
                        pass
                    if not use_auth and resp.status_code in (401, 403):
                        # refresh guest token and retry once
                        guest = await _get_guest_token(session, spec_obj.bearer, force_refresh=True)
                        if guest:
                            hdrs["x-guest-token"] = guest
                            try:
                                session.cookies.set("gt", guest, domain=".x.com")
                            except Exception:
                                try:
                                    session.cookies.set("gt", guest)
                                except Exception:
                                    pass
                            try:
                                _ensure_guest_headers(session, hdrs)
                            except Exception:
                                pass
                            # retry on same host after token refresh
                            resp = await session.get(
                                f"{host}/graphql/{spec_obj.query_id}/{spec_obj.operation}",
                                params=params,
                                headers=hdrs,
                                impersonate="chrome",
                            )
                        # if still unauthorized, try next host
                        if resp.status_code in (401, 403):
                            last_err = f"HTTP {resp.status_code}: {resp.text[:100]}"
                            continue
                    if resp.status_code >= 400:
                        last_err = f"HTTP {resp.status_code}: {resp.text[:100]}"
                        continue
                    data2 = resp.json()
                    if data2.get("errors"):
                        msg = "; ".join(e.get("message", "") for e in data2["errors"])
                        last_err = msg
                        continue
                    return data2, None
                # if loop completes with no return
                return None, last_err or "Unknown error"
            except Exception as e:  # noqa: BLE001
                return None, str(e)

        data, auth_err = await fetch(spec, True)
        if data is None:
            if auth_err and ("not authorized" in auth_err or "Could not authenticate you" in auth_err):
                logger.error("AUTH TOKEN and CT0 is old or invalid. Trying guest token...")
            else:
                logger.warning(f"Auth request failed: {auth_err}. Trying guest token")
            data, guest_err = await fetch(spec, False)
            if data is None:
                # If features error - invalidate cache and retry once
                if guest_err and ("cannot be null" in guest_err or "features" in guest_err):
                    logger.info("Feature error detected, refreshing spec cache and retrying")
                    await resolver.invalidate()
                    spec = await resolver.get_spec(session, html)
                    if not spec:
                        return (
                            [],
                            [],
                            None,
                            "Не удалось обновить параметры GraphQL (auth token failed, guest token failed)",
                        )
                    params["features"] = json.dumps(spec.features, separators=(",", ":"))
                    params["fieldToggles"] = json.dumps(spec.field_toggles, separators=(",", ":"))
                    data, guest_err = await fetch(spec, False)
                    if data is None:
                        logger.error(f"Guest request failed after refresh: {guest_err}")
                        set_auth_token(None)
                        set_csrf_token(None)
                        return (
                            [],
                            [],
                            None,
                            "Ошибка доступа к Twitter (auth token failed, guest token failed). Обновите токены",
                        )
                    used_guest = True
                else:
                    logger.error(f"Guest request failed: {guest_err}")
                    set_auth_token(None)
                    set_csrf_token(None)
                    return (
                        [],
                        [],
                        None,
                        "Ошибка доступа к Twitter (auth token failed, guest token failed). Обновите токены",
                    )
            else:
                used_guest = True
        else:
            # Auth token worked
            logger.info("Используем auth token")

        if used_guest:
            logger.info("Используем guest token")

    try:
        result = data["data"]["tweetResult"]["result"]
        result = result.get("tweet", result)
    except Exception:
        token_info = "(guest token)" if used_guest else "(auth token)"
        return [], [], None, f"Некорректный ответ Twitter {token_info}"

    # If no media on the main node, try quoted/retweeted nodes
    def pick_media_node(node: dict) -> dict:
        legacy = node.get("legacy", {}) if isinstance(node, dict) else {}
        media = (legacy.get("extended_entities", {}) or legacy.get("entities", {})).get("media", [])
        if media:
            return node
        for alt_key in ("quoted_status_result", "retweeted_status_result"):
            alt = (node.get(alt_key) or {}).get("result") if isinstance(node, dict) else None
            if isinstance(alt, dict):
                leg = alt.get("legacy", {})
                med = (leg.get("extended_entities", {}) or leg.get("entities", {})).get("media", [])
                if med:
                    return alt
        return node

    result = pick_media_node(result)

    screen_name = (
        (result.get("core", {}).get("user_results", {}).get("result", {}).get("legacy", {}).get("screen_name"))
        or (result.get("core", {}).get("user_results", {}).get("result", {}).get("core", {}).get("screen_name"))
        or ""
    )
    full_text = result.get("legacy", {}).get("full_text", "")
    caption_text = f"{full_text}\n[{screen_name}](https://x.com/{screen_name})" if screen_name else full_text
    caption = telegramify_markdown.markdownify(caption_text)

    media_items = result.get("legacy", {}).get("extended_entities", {}).get("media", [])
    image_files = []
    video_files = []
    async with AsyncSession() as session2:
        sem = asyncio.Semaphore(4)

        async def download_item(item: dict):
            mtype = item.get("type")
            media_url = item.get("media_url_https")
            if mtype == "photo" and media_url:
                base, ext = os.path.splitext(media_url)
                img_url = f"{base}?format={ext.lstrip('.')}\u0026name=orig".replace("\\u0026", "&")
                filename = download_path / f"{Path(base).name}{ext}"

                async with sem:
                    try:
                        img_resp = await session2.get(img_url, impersonate="chrome")
                        img_resp.raise_for_status()
                        filename.write_bytes(img_resp.content)
                        image_files.append(filename)
                    except Exception as e:  # noqa: BLE001
                        logger.error(f"Error downloading {img_url}: {e}")

            elif mtype in {"video", "animated_gif"}:
                variants = item.get("video_info", {}).get("variants", [])
                mp4s = [v for v in variants if v.get("content_type") == "video/mp4"]
                if not mp4s:
                    return
                best = max(mp4s, key=lambda v: v.get("bitrate", 0))
                v_url = best.get("url")
                if not v_url:
                    return
                ext = ".mp4"
                stem = Path(media_url).stem if media_url else "video"
                filename = download_path / f"{stem}{ext}"
                async with sem:
                    try:
                        v_resp = await session2.get(v_url, impersonate="chrome")
                        v_resp.raise_for_status()
                        filename.write_bytes(v_resp.content)
                        video_files.append(filename)
                    except Exception as e:  # noqa: BLE001
                        logger.error(f"Error downloading {v_url}: {e}")

        await asyncio.gather(*(download_item(it) for it in media_items))

    return image_files, video_files, caption, None


def _b2s(val: object) -> str:
    """Normalize Redis bytes/bytearray/str into str."""
    if isinstance(val, (bytes, bytearray)):
        return val.decode("utf-8", errors="ignore")
    return val if isinstance(val, str) else str(val)
