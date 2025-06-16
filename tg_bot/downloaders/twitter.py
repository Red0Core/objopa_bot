import json
import os
import re
from pathlib import Path
from typing import List, Tuple

import telegramify_markdown
from curl_cffi.requests import AsyncSession
from redis.asyncio import Redis

from core.config import DOWNLOADS_PATH
from core.logger import logger
from core.redis_client import get_redis

AUTH_TOKEN = os.getenv("TWITTER_AUTH_TOKEN")
CSRF_TOKEN = os.getenv("TWITTER_CT0")
TWITTER_USERNAME = os.getenv("TWITTER_USERNAME")
TWITTER_PASSWORD = os.getenv("TWITTER_PASSWORD")


async def _load_tokens_from_redis() -> None:
    """Load auth cookies from Redis if available."""
    global AUTH_TOKEN, CSRF_TOKEN
    try:
        redis: Redis = await get_redis()
        auth = await redis.get("twitter_auth_token")
        ct0 = await redis.get("twitter_ct0")
        if auth and ct0:
            AUTH_TOKEN = auth
            CSRF_TOKEN = ct0
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

BEARER_TOKEN = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"

TWITTER_FEATURES = {
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
}

TWITTER_FIELD_TOGGLES = {
    "withArticleRichContentState": True,
    "withArticlePlainText": False,
    "withGrokAnalyze": False,
    "withDisallowedReplyControls": False,
}

_LOGIN_INIT_DATA = json.dumps(
    {
        "input_flow_data": {
            "flow_context": {
                "debug_overrides": {},
                "start_location": {"location": "unknown"},
            }
        },
        "subtask_versions": {
            "action_list": 2,
            "alert_dialog": 1,
            "app_download_cta": 1,
            "check_logged_in_account": 1,
            "choice_selection": 3,
            "contacts_live_sync_permission_prompt": 0,
            "cta": 7,
            "email_verification": 2,
            "end_flow": 1,
            "enter_date": 1,
            "enter_email": 2,
            "enter_password": 5,
            "enter_phone": 2,
            "enter_recaptcha": 1,
            "enter_text": 5,
            "enter_username": 2,
            "generic_urt": 3,
            "in_app_notification": 1,
            "interest_picker": 3,
            "js_instrumentation": 1,
            "menu_dialog": 1,
            "notifications_permission_prompt": 2,
            "open_account": 2,
            "open_home_timeline": 1,
            "open_link": 1,
            "phone_verification": 4,
            "privacy_options": 1,
            "security_key": 3,
            "select_avatar": 4,
            "select_banner": 2,
            "settings_list": 7,
            "show_code": 1,
            "sign_up": 2,
            "sign_up_review": 4,
            "tweet_selection_urt": 1,
            "update_users": 1,
            "upload_media": 1,
            "user_recommendations_list": 4,
            "user_recommendations_urt": 1,
            "wait_spinner": 3,
            "web_modal": 1,
        },
    },
    separators=(",", ":"),
).encode()


async def _get_guest_token(session: AsyncSession, bearer: str) -> str | None:
    try:
        resp = await session.post(
            "https://api.x.com/1.1/guest/activate.json",
            headers={"Authorization": f"Bearer {bearer}"},
            data=b"",
            impersonate="chrome",
        )
        if resp.ok:
            data = resp.json()
            return data.get("guest_token")
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error fetching guest token: {e}")
    return None


async def _call_login_api(
    session: AsyncSession,
    headers: dict,
    flow_token: str | None,
    subtask_inputs: list | None = None,
    query: dict | None = None,
) -> tuple[str, str]:
    data = (
        json.dumps(
            {"flow_token": flow_token, "subtask_inputs": subtask_inputs},
            separators=(",", ":"),
        ).encode()
        if subtask_inputs is not None
        else _LOGIN_INIT_DATA
    )
    resp = await session.post(
        "https://api.x.com/1.1/onboarding/task.json",
        params=query or {},
        headers=headers,
        data=data,
        impersonate="chrome",
    )
    result = resp.json()
    if resp.status_code >= 400:
        msg = result.get("errors", [{}])[0].get("message")
        raise RuntimeError(f"Login failed: {msg}")
    if result.get("status") != "success":
        raise RuntimeError("Login unsuccessful")
    flow_token = result.get("flow_token")
    subtask = next((s.get("subtask_id") for s in result.get("subtasks", [])), "")
    return subtask, flow_token


async def login_and_get_tokens(username: str, password: str) -> tuple[str, str] | None:
    async with AsyncSession() as session:
        guest = await _get_guest_token(session, BEARER_TOKEN)
        if not guest:
            return None
        headers = {
            "Authorization": f"Bearer {BEARER_TOKEN}",
            "content-type": "application/json",
            "x-guest-token": guest,
            "x-twitter-client-language": "en",
            "x-twitter-active-user": "yes",
            "Referer": "https://x.com/",
            "Origin": "https://x.com",
        }

        subtask, flow_token = await _call_login_api(
            session, headers, None, query={"flow_name": "login"}
        )
        while True:
            if subtask == "LoginJsInstrumentationSubtask":
                subtask, flow_token = await _call_login_api(
                    session,
                    headers,
                    flow_token,
                    [
                        {
                            "subtask_id": subtask,
                            "js_instrumentation": {
                                "response": "{}",
                                "link": "next_link",
                            },
                        }
                    ],
                )
            elif subtask == "LoginEnterUserIdentifierSSO":
                subtask, flow_token = await _call_login_api(
                    session,
                    headers,
                    flow_token,
                    [
                        {
                            "subtask_id": subtask,
                            "settings_list": {
                                "setting_responses": [
                                    {
                                        "key": "user_identifier",
                                        "response_data": {"text_data": {"result": username}},
                                    }
                                ],
                                "link": "next_link",
                            },
                        }
                    ],
                )
            elif subtask == "LoginEnterPassword":
                subtask, flow_token = await _call_login_api(
                    session,
                    headers,
                    flow_token,
                    [
                        {
                            "subtask_id": subtask,
                            "enter_password": {
                                "password": password,
                                "link": "next_link",
                            },
                        }
                    ],
                )
            elif subtask == "AccountDuplicationCheck":
                subtask, flow_token = await _call_login_api(
                    session,
                    headers,
                    flow_token,
                    [
                        {
                            "subtask_id": subtask,
                            "check_logged_in_account": {"link": "AccountDuplicationCheck_false"},
                        }
                    ],
                )
            elif subtask == "DenyLoginSubtask":
                raise RuntimeError(
                    "Twitter rejected this login attempt as suspicious. USE AUTH_TOKEN and CSRF_TOKEN"
                )
            elif subtask == "LoginSuccessSubtask":
                auth = session.cookies.get("auth_token")
                ct0 = session.cookies.get("ct0")
                if auth and ct0:
                    return auth.value, ct0.value
                raise RuntimeError("Missing auth cookies")
            else:
                raise RuntimeError(f"Unhandled subtask {subtask}")

    return None


async def ensure_tokens() -> None:
    global AUTH_TOKEN, CSRF_TOKEN
    if AUTH_TOKEN and CSRF_TOKEN:
        return
    await _load_tokens_from_redis()
    if AUTH_TOKEN and CSRF_TOKEN:
        return
    if TWITTER_USERNAME and TWITTER_PASSWORD:
        try:
            tokens = await login_and_get_tokens(TWITTER_USERNAME, TWITTER_PASSWORD)
            if tokens:
                AUTH_TOKEN, CSRF_TOKEN = tokens
                logger.success("Obtained Twitter auth tokens")
        except Exception as e:  # noqa: BLE001
            logger.error(
                "Twitter login failed: %s. Provide TWITTER_AUTH_TOKEN and TWITTER_CT0"
                " or update via /worker/set-twitter-cookies",
                e,
            )


async def download_twitter_media(
    url: str, download_path: Path = DOWNLOADS_PATH
) -> Tuple[List[Path], List[Path], str | None, str | None]:
    """Download images and videos from a tweet via GraphQL."""
    download_path.mkdir(exist_ok=True)
    await ensure_tokens()
    async with AsyncSession() as session:
        try:
            html_resp = await session.get(url, impersonate="chrome")
            html = html_resp.text
        except Exception as e:  # noqa: BLE001
            logger.error(f"Twitter HTML fetch error: {e}")
            return [], [], None, "Не удалось получить страницу твита"

        script_match = re.search(
            r'src="(https://abs.twimg.com/responsive-web/client-web/main[^\"]+\.js)"',
            html,
        )
        if not script_match:
            return [], [], None, "Не удалось найти main.js"
        js_url = script_match.group(1)

        try:
            js_resp = await session.get(js_url, impersonate="chrome")
            js = js_resp.text
        except Exception as e:  # noqa: BLE001
            logger.error(f"Twitter JS fetch error: {e}")
            return [], [], None, "Не удалось получить скрипт"

        qid_match = re.search(r'queryId:"(?P<id>[^"]+)",operationName:"TweetResultByRestId"', js)
        if not qid_match:
            return [], [], None, "Не удалось найти queryId"
        query_id = qid_match.group("id")

        bearer_match = re.search(r'BEARER_TOKEN:"(?P<token>[^"]+)"', js)
        bearer_token = bearer_match.group("token") if bearer_match else BEARER_TOKEN

        id_match = TWITTER_REGEX.match(url)
        if not id_match:
            return [], [], None, "Неверная ссылка Twitter"
        tweet_id = id_match.group("id")

        variables = {
            "tweetId": tweet_id,
            "withCommunity": False,
            "includePromotedContent": False,
            "withVoice": False,
        }
        params = {
            "variables": json.dumps(variables, separators=(",", ":")),
            "features": json.dumps(TWITTER_FEATURES, separators=(",", ":")),
            "fieldToggles": json.dumps(TWITTER_FIELD_TOGGLES, separators=(",", ":")),
        }

        async def fetch(use_auth: bool) -> tuple[dict | None, str | None]:
            hdrs = {
                "Authorization": f"Bearer {bearer_token}",
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
                guest = await _get_guest_token(session, bearer_token)
                if not guest:
                    return None, "Не удалось получить guest token"
                hdrs["x-guest-token"] = guest
            try:
                resp = await session.get(
                    f"https://api.x.com/graphql/{query_id}/TweetResultByRestId",
                    params=params,
                    headers=hdrs,
                    impersonate="chrome",
                )
                if resp.status_code >= 400:
                    return None, f"HTTP {resp.status_code}: {resp.text[:100]}"
                data = resp.json()
                if data.get("errors"):
                    msg = "; ".join(e.get("message", "") for e in data["errors"])
                    return None, msg
                return data, None
            except Exception as e:  # noqa: BLE001
                return None, str(e)

        data, auth_err = await fetch(True)
        if data is None:
            logger.warning(f"Auth request failed: {auth_err}. Trying guest token")
            data, guest_err = await fetch(False)
            if data is None:
                logger.error(f"Guest request failed: {guest_err}")
                set_auth_token(None)
                set_csrf_token(None)
                return [], [], None, "Ошибка доступа к Twitter. Обновите токены"
            else:
                logger.info("Используем guest token вместо авторизации")

    try:
        result = data["data"]["tweetResult"]["result"]
    except Exception:
        return [], [], None, "Некорректный ответ Twitter"

    screen_name = (
        (
            result.get("core", {})
            .get("user_results", {})
            .get("result", {})
            .get("legacy", {})
            .get("screen_name")
        )
        or (
            result.get("core", {})
            .get("user_results", {})
            .get("result", {})
            .get("core", {})
            .get("screen_name")
        )
        or ""
    )
    full_text = result.get("legacy", {}).get("full_text", "")
    caption_text = (
        f"{full_text}\n[{screen_name}](https://x.com/{screen_name})" if screen_name else full_text
    )
    caption = telegramify_markdown.markdownify(caption_text)

    media_items = result.get("legacy", {}).get("extended_entities", {}).get("media", [])
    image_files = []
    video_files = []
    async with AsyncSession() as session2:
        for item in media_items:
            mtype = item.get("type")
            media_url = item.get("media_url_https")
            if mtype == "photo" and media_url:
                base, ext = os.path.splitext(media_url)
                img_url = f"{base}?format={ext.lstrip('.')}\u0026name=orig"
                filename = download_path / f"{Path(base).name}{ext}"
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
                    continue
                best = max(mp4s, key=lambda v: v.get("bitrate", 0))
                v_url = best.get("url")
                if not v_url:
                    continue
                ext = ".mp4"
                filename = download_path / f"{Path(media_url).stem}{ext}"
                try:
                    v_resp = await session2.get(v_url, impersonate="chrome")
                    v_resp.raise_for_status()
                    filename.write_bytes(v_resp.content)
                    video_files.append(filename)
                except Exception as e:  # noqa: BLE001
                    logger.error(f"Error downloading {v_url}: {e}")

    return image_files, video_files, caption, None
