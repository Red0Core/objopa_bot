"""
Сервис для управления User-Agent для Instagram через Redis.
Позволяет динамически изменять User-Agent без перезапуска бота.
"""

import asyncio
from typing import Optional

from core.redis_client import get_redis
from core.logger import logger


class InstagramUserAgentService:
    """Сервис для управления User-Agent для Instagram."""

    REDIS_KEY = "instagram:current_user_agent"
    # Актуальный User-Agent для Instagram (июль 2025)
    DEFAULT_USER_AGENT = "Mozilla/5.0 (Linux; Android 14; RMX3301 Build/UKQ1.230924.001; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/138.0.0.0 Mobile Safari/537.36 Instagram 386.0.0.46.84 Android (34/14; 640dpi; 1440x2912; realme; RMX3301; RED8ACL1; qcom; de_DE; 727763711; IABMV/1)"

    def __init__(self):
        self._current_user_agent: Optional[str] = None
        self._lock = asyncio.Lock()

    async def get_user_agent(self) -> str:
        """Получает текущий User-Agent из Redis или возвращает дефолтный."""
        async with self._lock:
            redis = await get_redis()

            try:
                # Пытаемся получить текущий User-Agent из Redis
                user_agent = await redis.get(self.REDIS_KEY)

                if not user_agent:
                    # Если в Redis нет User-Agent'а, устанавливаем дефолтный
                    await redis.set(self.REDIS_KEY, self.DEFAULT_USER_AGENT)
                    user_agent = self.DEFAULT_USER_AGENT

                self._current_user_agent = user_agent
                logger.debug(f"Using User-Agent for Instagram: {user_agent}")
                return user_agent

            except Exception as e:
                logger.error(f"Error getting User-Agent from Redis: {e}")
                # В случае ошибки возвращаем дефолтный
                self._current_user_agent = self.DEFAULT_USER_AGENT
                return self.DEFAULT_USER_AGENT

    async def set_user_agent(self, user_agent: str) -> bool:
        """Устанавливает новый User-Agent в Redis."""
        try:
            redis = await get_redis()
            await redis.set(self.REDIS_KEY, user_agent)
            self._current_user_agent = user_agent
            logger.info(f"Set new User-Agent: {user_agent}")
            return True

        except Exception as e:
            logger.error(f"Error setting User-Agent in Redis: {e}")
            return False

    async def get_current_user_agent_from_redis(self) -> Optional[str]:
        """Возвращает текущий User-Agent из Redis."""
        try:
            redis = await get_redis()
            user_agent = await redis.get(self.REDIS_KEY)
            return user_agent

        except Exception as e:
            logger.error(f"Error getting current User-Agent from Redis: {e}")
            return None

    async def reset_to_default(self) -> bool:
        """Сбрасывает User-Agent на дефолтный."""
        return await self.set_user_agent(self.DEFAULT_USER_AGENT)

    def get_current_user_agent(self) -> Optional[str]:
        """Возвращает текущий User-Agent без обращения к Redis."""
        return self._current_user_agent


# Создаем глобальный экземпляр сервиса
instagram_ua_service = InstagramUserAgentService()
