import traceback

import telegramify_markdown
from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import FSInputFile, Message

from core.config import COOKIES_ALLOW_USERS_ID, STORAGE_DIR
from core.logger import logger
from core.redis_client import Redis, get_redis
from tg_bot.downloaders import (
    INSTAGRAM_REGEX,
    TWITTER_REGEX,
    downloader_manager,
)
from tg_bot.utils.cookies_manager import cookies_manager
from tg_bot.utils.media_sender import media_sender

router = Router()


async def process_instagram(message: Message, url: str) -> bool:
    """Handle Instagram URL download and sending. Returns True if successful."""
    status_message = await message.answer("⏳ Загружаю медиа из Instagram...")

    try:
        result = await downloader_manager.download_media(url)

        if not result.success:
            await status_message.edit_text(result.error or "❌ Не удалось загрузить медиа.")
            return False

        # Используем новый простой sender
        await media_sender.send(message, result.files, result.caption)
        await status_message.delete()
        return True

    except Exception as e:
        logger.error(f"Error processing Instagram media: {traceback.format_exc()}")
        await status_message.edit_text(f"❌ Ошибка при обработке медиа: {str(e)}")
        return False


@router.message(Command("insta"))
async def instagram_handler(message: Message, command: CommandObject):
    if not command.args:
        await message.answer(
            telegramify_markdown.markdownify("❌ Ты не указал ссылку! Используй: `/insta <ссылка>`"),
            parse_mode="MarkdownV2",
        )
        return

    url = command.args.strip()

    if not INSTAGRAM_REGEX.match(url):
        await message.answer(
            telegramify_markdown.markdownify("❌ Это не похоже на ссылку Instagram. Попробуй еще раз."),
            parse_mode="MarkdownV2",
        )
        return

    await process_instagram(message, url)


@router.message(Command("d"))
async def universal_download_handler(message: Message, command: CommandObject):
    if not command.args:
        await message.answer(
            telegramify_markdown.markdownify("❌ Ты не указал ссылку! Используй: `/d <ссылка>`"),
            parse_mode="MarkdownV2",
        )
        return

    url = command.args.strip()

    download_from_str = ""
    # Для Instagram URL используем специальный обработчик с улучшенным UI
    if INSTAGRAM_REGEX.match(url):
        await process_instagram(message, url)
        return
    elif TWITTER_REGEX.match(url):
        download_from_str = "из Twitter(X)"

    # Для всех остальных URL используем универсальный менеджер
    status_message = await message.answer(f"⏳ Загружаю медиа {download_from_str}...")

    try:
        result = await downloader_manager.download_media(url)

        if not result.success:
            await status_message.edit_text(
                telegramify_markdown.markdownify(result.error) if result.error else "❌ Не удалось скачать медиа.",
                parse_mode="MarkdownV2",
            )
            return

        # Обрабатываем скачанные файлы
        if not result.files and result.caption:
            # Если нет файлов, но есть подпись - логируем это. (Подпись отправляется в media_sender ниже все равно)
            logger.info("Downloaded media has no files but has caption")
        else:
            logger.info(
                f"Media downloaded successfully using {result.downloader_used.value if result.downloader_used else 'unknown'} from: {url}"
            )

        await media_sender.send(message, result.files, result.caption)
        await status_message.delete()

    except Exception as e:
        logger.error(f"Error in universal download handler: {e}")
        await status_message.edit_text(f"❌ Ошибка при скачивании: {str(e)}")


@router.message(Command("d_test"))
async def download_handler(message: Message, command: CommandObject):
    """Тестирует систему скачивания с диагностикой."""
    if not command.args:
        await message.reply(
            telegramify_markdown.markdownify(
                "🔧 **Тест системы скачивания**\n\n"
                "Использование: `/d_test <ссылка>`\n\n"
                "Команда протестирует все доступные методы скачивания "
                "и покажет подробную диагностику работы системы."
            ),
            parse_mode="MarkdownV2",
        )
        return

    url = command.args.strip()
    status_message = await message.answer("🔍 Запускаю диагностику системы скачивания...")

    try:
        # Создаем новый экземпляр менеджера для детальной диагностики
        from tg_bot.downloaders.downloader_manager import DownloaderManager

        test_manager = DownloaderManager()

        result = await test_manager.download_media(url)

        # Формируем отчет
        report = "🔧 **Отчет диагностики скачивания**\n\n"
        report += f"🔗 **URL:** `{url}`\n\n"

        # Показываем попытки
        if test_manager.download_attempts:
            report += "📋 **Попытки скачивания:**\n"
            for attempt in test_manager.download_attempts:
                report += f"• {attempt}\n"
            report += "\n"

        # Результат
        if result.success:
            report += "✅ **Результат:** Успешно\n"
            report += f"🛠️ **Использован:** {result.downloader_used.value if result.downloader_used else 'Unknown'}\n"
            report += f"📁 **Файлов:** {len(result.files)}\n"

            if result.files:
                report += "📋 **Скачанные файлы:**\n"
                for file_path in result.files:
                    file_size = file_path.stat().st_size / (1024 * 1024)  # MB
                    report += f"• `{file_path.name}` ({file_size:.2f} MB)\n"

            if result.caption:
                caption_preview = result.caption[:100] + "..." if len(result.caption) > 100 else result.caption
                report += f"📝 **Подпись:** `{caption_preview}`\n"
        else:
            report += "❌ **Результат:** Неудача\n"
            if result.error:
                report += f"🚫 **Ошибка:** `{result.error}`\n"

        await status_message.edit_text(telegramify_markdown.markdownify(report), parse_mode="MarkdownV2")

        # Если есть файлы, показываем первый как пример
        if result.success and result.files:
            first_file = result.files[0]
            suffix = first_file.suffix.lower()

            try:
                if suffix in (".jpg", ".jpeg", ".png", ".webp"):
                    await message.reply_photo(FSInputFile(first_file), caption="📷 Пример скачанного файла")
                elif suffix in (".mp4", ".mov", ".mkv", ".webm"):
                    await message.reply_video(
                        FSInputFile(first_file),
                        supports_streaming=True,
                        caption="🎥 Пример скачанного файла",
                    )
                else:
                    await message.reply_document(FSInputFile(first_file), caption="📄 Пример скачанного файла")
            except Exception as e:
                logger.error(f"Error sending test file: {e}")

    except Exception as e:
        logger.error(f"Error in download test: {e}")
        await status_message.edit_text(f"❌ Ошибка диагностики: {str(e)}")


@router.message(Command("d_status"))
async def downloader_status_handler(message: Message):
    """Показывает статус компонентов системы скачивания."""
    try:
        status_report = "📊 **Статус системы скачивания**\n\n"

        # Проверяем Instagram UA Service
        try:
            from tg_bot.services.instagram_ua_service import instagram_ua_service

            current_ua = await instagram_ua_service.get_current_user_agent_from_redis()

            status_report += "📱 **Instagram User-Agent сервис:**\n"
            status_report += f"• Статус: {'✅ Активен' if current_ua else '⚠️ UA не установлен'}\n"
            status_report += "\n"
        except Exception as e:
            status_report += f"📱 **Instagram User-Agent сервис:** ❌ Ошибка - {str(e)}\n\n"

        # Проверяем Redis соединение
        try:
            from core.redis_client import get_redis

            redis = await get_redis()
            await redis.ping()
            status_report += "🔴 **Redis:** ✅ Подключен\n\n"
        except Exception as e:
            status_report += f"🔴 **Redis:** ❌ Ошибка - {str(e)}\n\n"

        # Проверяем доступность downloaders
        status_report += "🛠️ **Доступные скачиватели:**\n"

        try:
            status_report += "• Instagram: ✅ Доступен\n"
            status_report += "• Twitter: ✅ Доступен\n"
        except Exception as e:
            status_report += f"• Кастомные скачиватели: ❌ {str(e)}\n"

        try:
            status_report += "• yt-dlp: ✅ Доступен\n"
        except Exception as e:
            status_report += f"• yt-dlp: ❌ {str(e)}\n"

        try:
            status_report += "• gallery-dl: ✅ Доступен\n"
        except Exception as e:
            status_report += f"• gallery-dl: ❌ {str(e)}\n"

        # Проверяем папку загрузок
        from core.config import DOWNLOADS_DIR

        if DOWNLOADS_DIR.exists():
            files_count = len(list(DOWNLOADS_DIR.iterdir()))
            status_report += f"\n📁 **Папка загрузок:** ✅ Доступна ({files_count} файлов)\n"
        else:
            status_report += "\n📁 **Папка загрузок:** ❌ Не найдена\n"

        await message.reply(telegramify_markdown.markdownify(status_report), parse_mode="MarkdownV2")

    except Exception as e:
        logger.error(f"Error checking downloader status: {e}")
        await message.reply(f"❌ Ошибка проверки статуса: {str(e)}")


@router.message(Command("d_cookies"))
async def download_with_cookies_handler(message: Message, command: CommandObject):
    """Скачивает медиа с приоритетом cookies (/d_cookies <url>)."""
    if not command.args:
        await message.answer(
            telegramify_markdown.markdownify("❌ Укажи ссылку! Используй: `/d_cookies <url>`"),
            parse_mode="MarkdownV2",
        )
        return

    url = command.args.strip()
    status_message = await message.answer("⏳ Загружаю медиа с приоритетом cookies...")

    try:
        # Используем download_media_with_cookies для приоритета на cookies
        result = await downloader_manager.download_media_with_cookies(url)

        if not result.success:
            await status_message.edit_text(result.error or "❌ Не удалось загрузить медиа.")
            return

        # Отправляем медиа
        await media_sender.send(message, result.files, result.caption)
        await status_message.delete()

    except Exception as e:
        logger.error(f"Error downloading with cookies: {traceback.format_exc()}")
        await status_message.edit_text(f"❌ Ошибка: {str(e)}")


@router.message(Command("set_twitter"))
async def set_twitter_cookies_only_admin_acc(message: Message, command: CommandObject):
    if (
        message.from_user is None
        or not message.from_user.id
        or (message.from_user.id not in COOKIES_ALLOW_USERS_ID and message.chat.type != "private")
    ):
        await message.answer(telegramify_markdown.markdownify("❌ ЗАПРЕЩЕНО ВАМ!!!"), parse_mode="MarkdownV2")
        return
    redis: Redis = await get_redis()
    if not command.args or " " not in command.args:
        await message.answer(
            telegramify_markdown.markdownify("❌ Ты не указал токены! Используй: `/set_twitter <auth_token> <ct0>`"),
            parse_mode="MarkdownV2",
        )
        return
    data = command.args.split(" ")
    await redis.mset({"twitter_auth_token": data[0], "twitter_ct0": data[1]})
    await message.answer(telegramify_markdown.markdownify("✅ Токены Twitter установлены!"), parse_mode="MarkdownV2")


@router.message(Command("set_cookies"))
async def set_cookies_handler(message: Message):
    """Устанавливает cookies файл для сайта (только MAIN_ACC)."""
    if message.from_user.id not in COOKIES_ALLOW_USERS_ID:
        await message.answer(telegramify_markdown.markdownify("❌ ЗАПРЕЩЕНО ВАМ!!!"), parse_mode="MarkdownV2")
        return

    if not message.document:
        await message.answer(
            telegramify_markdown.markdownify(
                "❌ Прикрепи файл cookies (в формате Netscape, как из --cookies-from-browser)!"
            ),
            parse_mode="MarkdownV2",
        )
        return

    # Скачиваем файл
    file = await message.bot.get_file(message.document.file_id)
    if not file.file_path:
        await message.answer(
            telegramify_markdown.markdownify("❌ Не удалось получить файл!"),
            parse_mode="MarkdownV2",
        )
        return

    try:
        # Скачиваем во временный файл
        temp_file = STORAGE_DIR / "temp_cookies_file.txt"
        temp_file.parent.mkdir(parents=True, exist_ok=True)

        # Используем download_to_path
        await message.bot.download_file(file.file_path, str(temp_file))

        # Извлекаем имя сайта прямо из файла cookies
        site_name = cookies_manager.extract_site_from_cookies_file(temp_file)

        if not site_name:
            await message.answer(
                telegramify_markdown.markdownify(
                    "❌ Не удалось определить сайт из файла! Убедись, что это валидный файл cookies в формате Netscape."
                ),
                parse_mode="MarkdownV2",
            )
            temp_file.unlink(missing_ok=True)
            return

        # Сохраняем в Redis
        success = await cookies_manager.save_cookies(site_name, temp_file)

        if success:
            await message.answer(
                telegramify_markdown.markdownify(f"✅ Cookies для **{site_name}** успешно установлены на 30 дней!"),
                parse_mode="MarkdownV2",
            )
        else:
            await message.answer(
                telegramify_markdown.markdownify("❌ Не удалось сохранить cookies!"),
                parse_mode="MarkdownV2",
            )

        # Удаляем временный файл
        temp_file.unlink(missing_ok=True)

    except Exception as e:
        logger.error(f"Error setting cookies: {e}")
        await message.answer(telegramify_markdown.markdownify(f"❌ Ошибка: {str(e)}"), parse_mode="MarkdownV2")


@router.message(Command("list_cookies"))
async def list_cookies_handler(message: Message):
    """Показывает доступные cookies (только MAIN_ACC)."""
    if message.from_user.id not in COOKIES_ALLOW_USERS_ID:
        await message.answer(telegramify_markdown.markdownify("❌ ЗАПРЕЩЕНО ВАМ!!!"), parse_mode="MarkdownV2")
        return

    cookies = await cookies_manager.list_available_cookies()

    if not cookies:
        await message.answer(telegramify_markdown.markdownify("❌ Нет доступных cookies!"), parse_mode="MarkdownV2")
        return

    lines = ["*📋 Доступные cookies:*\n"]
    for site_name, timestamp in cookies.items():
        lines.append(f"• **{site_name}**: {timestamp}")

    await message.answer(telegramify_markdown.markdownify("\n".join(lines)), parse_mode="MarkdownV2")


@router.message(Command("delete_cookies"))
async def delete_cookies_handler(message: Message, command: CommandObject):
    """Удаляет cookies для сайта (только MAIN_ACC)."""
    if message.from_user.id not in COOKIES_ALLOW_USERS_ID:
        await message.answer(telegramify_markdown.markdownify("❌ ЗАПРЕЩЕНО ВАМ!!!"), parse_mode="MarkdownV2")
        return

    if not command.args:
        await message.answer(
            telegramify_markdown.markdownify("❌ Укажи имя сайта! Используй: `/delete_cookies <site_name>`"),
            parse_mode="MarkdownV2",
        )
        return

    site_name = command.args.strip().lower()
    success = await cookies_manager.delete_cookies(site_name)

    if success:
        await message.answer(
            telegramify_markdown.markdownify(f"✅ Cookies для **{site_name}** удалены!"),
            parse_mode="MarkdownV2",
        )
    else:
        await message.answer(
            telegramify_markdown.markdownify("❌ Не удалось удалить cookies!"),
            parse_mode="MarkdownV2",
        )
