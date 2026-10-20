"""
Роутер для управления User-Agent'ами Instagram через команды бота.
Только для администраторов.
"""

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from telegramify_markdown import markdownify

from core.logger import logger
from tg_bot.services.instagram_ua_service import instagram_ua_service

router = Router()


@router.message(Command("ua_current"))
async def current_user_agent_handler(message: Message):
    """Показывает статус User-Agent для Instagram."""
    from core.config import COOKIES_ALLOW_USERS_ID

    if not message.from_user or message.from_user.id not in COOKIES_ALLOW_USERS_ID:
        await message.reply("❌ Недостаточно прав.")
        return

    try:
        current_ua = await instagram_ua_service.get_current_user_agent_from_redis()

        if current_ua:
            await message.reply(markdownify(f"🎯 **User-Agent:** `{current_ua}`"), parse_mode="MarkdownV2")
        else:
            await message.reply(
                markdownify("❓ **User-Agent статус:** ⚠️ Не установлен. Используйте `/ua_set` для установки."),
                parse_mode="MarkdownV2",
            )
    except Exception as e:
        logger.error(f"Error getting current user agent: {e}")
        await message.reply(f"❌ Ошибка: {str(e)}")


@router.message(Command("ua_set"))
async def set_user_agent_handler(message: Message, command: CommandObject):
    """Устанавливает новый User-Agent для Instagram."""
    from core.config import COOKIES_ALLOW_USERS_ID

    if not message.from_user or message.from_user.id not in COOKIES_ALLOW_USERS_ID:
        await message.reply("❌ Недостаточно прав.")
        return

    if not command.args:
        await message.reply(
            markdownify(
                "❌ Нужно указать User-Agent!\n"
                "Использование: `/ua_set <User-Agent>`\n\n"
                "Пример:\n"
                "`/ua_set Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15`"
            ),
            parse_mode="MarkdownV2",
        )
        return

    user_agent = command.args.strip()

    if len(user_agent) < 20:
        await message.reply("❌ User-Agent слишком короткий. Минимум 20 символов.")
        return

    try:
        success = await instagram_ua_service.set_user_agent(user_agent)

        if success:
            await message.reply("✅ User-Agent успешно установлен")
        else:
            await message.reply("❌ Не удалось установить User-Agent.")

    except Exception as e:
        logger.error(f"Error setting user agent: {e}")
        await message.reply(f"❌ Ошибка при установке: {str(e)}")


@router.message(Command("ua_reset"))
async def reset_user_agent_handler(message: Message):
    """Сбрасывает User-Agent на дефолтный."""
    from core.config import COOKIES_ALLOW_USERS_ID

    if not message.from_user or message.from_user.id not in COOKIES_ALLOW_USERS_ID:
        await message.reply("❌ Недостаточно прав.")
        return

    try:
        success = await instagram_ua_service.reset_to_default()

        if success:
            await message.reply("🔄 User-Agent сброшен на дефолтный")
        else:
            await message.reply("❌ Не удалось сбросить User-Agent.")

    except Exception as e:
        logger.error(f"Error resetting user agent: {e}")
        await message.reply(f"❌ Ошибка при сбросе: {str(e)}")


@router.message(Command("insta_session"))
async def instagram_session_info_handler(message: Message):
    """Report actual active Instagram provider and Redis health, never legacy Instaloader."""
    from core.config import COOKIES_ALLOW_USERS_ID

    if not message.from_user or message.from_user.id not in COOKIES_ALLOW_USERS_ID:
        await message.reply("❌ Недостаточно прав.")
        return
    from tg_bot.downloaders.instagram_web_parser import get_instagram_session_status

    status = await get_instagram_session_status()
    active = ", ".join(status["slots"]) if status["slots"] else "нет"
    cooling = ", ".join(status["cooling"]) if status["cooling"] else "нет"
    details = (
        "📊 **Instagram:** HTTPCloak / Chrome 155 Android (browser context)\n"
        f"🔴 **Redis:** {'✅' if status['redis'] else '❌'}\n"
        f"🍪 **Cookie slots:** {active}\n"
        f"⏸ **Slots cooldown:** {cooling}\n"
        f"⚠️ **Global rate limit:** {'ДА' if status['rate_limited'] else 'нет'}\n"
        "Статус локальный: не проверяет авторизацию запросом к Instagram."
    )
    await message.reply(markdownify(details), parse_mode="MarkdownV2")


@router.message(Command("insta_reset"))
async def instagram_reset_handler(message: Message):
    """Compatibility command: do not destroy cookies or disable rate limiting."""
    from core.config import COOKIES_ALLOW_USERS_ID

    if not message.from_user or message.from_user.id not in COOKIES_ALLOW_USERS_ID:
        await message.reply("❌ Недостаточно прав.")
        return
    from tg_bot.downloaders.instagram_web_parser import reset_instagram_web_parser

    await reset_instagram_web_parser()
    await message.reply(
        "✅ Instagram-загрузчик не держит сессию в памяти. Cookies сохранены в Redis; защитная пауза не сбрасывается."
    )


@router.message(Command("ua_help"))
async def ua_help_handler(message: Message):
    """Показывает справку по командам User-Agent."""
    help_text = """
🔧 **Управление User-Agent для Instagram**

**Основные команды:**
• `/ua_current` - показать статус UA
• `/ua_set <UA>` - установить новый UA  
• `/ua_reset` - сбросить на дефолтный
• `/ua_help` - эта справка

**Управление Instagram системой:**
• `/insta_session` - статус системы
• `/insta_reset` - сброс кэша для пересоздания с новым UA

**Использование:**
1. Используйте `/ua_set` с актуальным User-Agent браузера
2. Для получения User-Agent откройте браузер и найдите в настройках разработчика
3. Рекомендуется использовать User-Agent мобильных браузеров

💡 **Совет:** Регулярно обновляйте User-Agent для лучшей совместимости с Instagram.
"""
    await message.reply(markdownify(help_text), parse_mode="MarkdownV2")
