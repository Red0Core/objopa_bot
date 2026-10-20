"""Redis user agent configuration for the browser-context Instagram downloader."""

import asyncio

from core.logger import logger
from core.redis_client import get_redis


class InstagramUserAgentService:
    REDIS_KEY = "instagram:current_user_agent"
    DEFAULT_USER_AGENT = (
        "Mozilla/5.0 (Linux; Android 16; RMX3709 Build/BP2A.250605.015; wv) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
        "Chrome/155.0.8059.73 Mobile Safari/537.36 Instagram 451.0.0.53.80 "
        "Android (36/16; 560dpi; 1240x2772; realme; RMX3709; REE2B2L1; qcom; ru_RU; 1089931504; IABMV/1)"
    )

    def __init__(self):
        self._current_user_agent: str | None = None
        self._lock = asyncio.Lock()

    async def get_user_agent(self) -> str:
        try:
            async with self._lock:
                redis = await get_redis()
                value = await redis.get(self.REDIS_KEY)
                if not value or ("Instagram 386.0.0.46.84" in value and "RMX3301" in value):
                    # Migrate only the old hardcoded 2025 default, retain real custom UAs.
                    await redis.set(self.REDIS_KEY, self.DEFAULT_USER_AGENT)
                    value = self.DEFAULT_USER_AGENT
                self._current_user_agent = value
                return value
        except Exception as exc:
            logger.warning("Instagram UA Redis unavailable: {}", type(exc).__name__)
            return self._current_user_agent or self.DEFAULT_USER_AGENT

    async def set_user_agent(self, user_agent: str) -> bool:
        if (
            not isinstance(user_agent, str)
            or not 40 <= len(user_agent) <= 1024
            or "\n" in user_agent
            or "\r" in user_agent
        ):
            return False
        try:
            redis = await get_redis()
            await redis.set(self.REDIS_KEY, user_agent)
            self._current_user_agent = user_agent
            return True
        except Exception as exc:
            logger.error("Instagram UA write failed: {}", type(exc).__name__)
            return False

    async def get_current_user_agent_from_redis(self) -> str | None:
        try:
            redis = await get_redis()
            return await redis.get(self.REDIS_KEY)
        except Exception:
            return None

    async def reset_to_default(self) -> bool:
        return await self.set_user_agent(self.DEFAULT_USER_AGENT)

    def get_current_user_agent(self) -> str | None:
        return self._current_user_agent


instagram_ua_service = InstagramUserAgentService()
