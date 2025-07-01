import asyncio
from pathlib import Path
from typing import Any

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    FSInputFile,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    MediaUnion,
    Message,
)
import telegramify_markdown

from core.config import DOWNLOADS_PATH
from core.logger import logger
from tg_bot.downloaders import (
    INSTAGRAM_REGEX,
    TWITTER_REGEX,
    downloader_manager,
    DownloaderType,
)
from tg_bot.services.gpt import split_message_by_paragraphs

router = Router()


async def send_images_in_chunks(message: Message, images: list[Path], caption: str | None = None):
    """Разбивает список изображений на чанки по 10 и отправляет их в Telegram"""

    def chunk_list(lst: list[Any], size: int = 10) -> list[list[Any]]:
        """Функция разбивает список на части по size элементов"""
        return [lst[i : i + size] for i in range(0, len(lst), size)]

    image_chunks = chunk_list(images, 10)

    for i, chunk in enumerate(image_chunks):
        media_group: list[MediaUnion] = [InputMediaPhoto(media=FSInputFile(img)) for img in chunk]

        # Отправляем первый альбом с подписью, остальные без
        if i == 0 and caption:
            await message.reply_media_group(media=media_group, caption=caption)
        else:
            await message.reply_media_group(media=media_group)
        await asyncio.sleep(5)


async def process_instagram(message: Message, url: str) -> bool:
    """Handle Instagram URL download and sending. Returns True if successful."""
    status_message = await message.answer("⏳ Загружаю медиа из Instagram...")

    try:
        result = await downloader_manager.download_media(url)
        
        if not result.success:
            await status_message.edit_text(result.error or "❌ Не удалось загрузить медиа.")
            return False

        # Разделяем файлы по типам
        images: list[Path] = []
        videos: list[Path] = []

        for file_path in result.files:
            suffix = file_path.suffix.lower()
            if suffix in (".jpg", ".jpeg", ".png"):
                if "reel" in url:
                    continue
                images.append(file_path)
            elif suffix in (".mp4", ".mov"):
                videos.append(file_path)

        # Отправляем видео
        if videos:
            for video in videos:
                await message.reply_video(FSInputFile(video), caption=result.caption)

        # Отправляем изображения
        caption_arr = split_message_by_paragraphs(result.caption or "")
        if len(images) > 1:
            await send_images_in_chunks(message, images, caption_arr[0] if caption_arr else None)
            for part in caption_arr[1:]:
                await message.reply(part)
        elif len(images) == 1:
            replied_photo = await message.reply_photo(
                FSInputFile(images[0]), caption=caption_arr[0] if caption_arr else None
            )
            for part in caption_arr[1:]:
                await replied_photo.reply(part)

        await status_message.delete()
        return True
        
    except Exception as e:
        logger.error(f"Error processing Instagram media: {e}")
        await status_message.edit_text(f"❌ Ошибка при обработке медиа: {str(e)}")
        return False


@router.message(Command("insta"))
async def instagram_handler(message: Message, command: CommandObject):
    if not command.args:
        await message.answer(telegramify_markdown.markdownify("❌ Ты не указал ссылку! Используй: `/insta <ссылка>`"), parse_mode='MarkdownV2')
        return

    url = command.args.strip()

    if not INSTAGRAM_REGEX.match(url):
        await message.answer(telegramify_markdown.markdownify("❌ Это не похоже на ссылку Instagram. Попробуй еще раз."), parse_mode='MarkdownV2')
        return

    await process_instagram(message, url)


@router.message(Command("d"))
async def universal_download_handler(message: Message, command: CommandObject):
    if not command.args:
        await message.answer(telegramify_markdown.markdownify("❌ Ты не указал ссылку! Используй: `/d <ссылка>`"), parse_mode='MarkdownV2')
        return

    url = command.args.strip()

    # Для Instagram URL используем специальный обработчик с улучшенным UI
    if INSTAGRAM_REGEX.match(url):
        await process_instagram(message, url)
        return

    # Для всех остальных URL используем универсальный менеджер
    status_message = await message.answer("⏳ Загружаю медиа...")

    try:
        result = await downloader_manager.download_media(url)
        
        if not result.success:
            await status_message.edit_text(
                telegramify_markdown.markdownify(result.error)
                if result.error else "❌ Не удалось скачать медиа.", 
                parse_mode="MarkdownV2"
            )
            return

        # Обрабатываем скачанные файлы
        await send_downloaded_files(message, result.files, result.caption, result.downloader_used)
        await status_message.delete()
        
        logger.info(f"Media downloaded successfully using {result.downloader_used.value if result.downloader_used else 'unknown'} from: {url}")
        
    except Exception as e:
        logger.error(f"Error in universal download handler: {e}")
        await status_message.edit_text(f"❌ Ошибка при скачивании: {str(e)}")


async def send_downloaded_files(message: Message, files: list[Path], caption: str | None, downloader_used) -> None:
    """Отправляет скачанные файлы в Telegram."""
    if not files:
        return
    
    # Обработка для Twitter (если использовался кастомный скачиватель и есть изображения/видео раздельно)
    if downloader_used == DownloaderType.CUSTOM and TWITTER_REGEX.match(message.text or ""):
        await send_twitter_files(message, files, caption)
        return
    
    # Универсальная обработка для остальных случаев
    media_group: list[MediaUnion] = []
    audio_files: list[MediaUnion] = []
    
    for file_path in files:
        suffix = file_path.suffix.lower()
        input_file = FSInputFile(file_path)
        
        if suffix in (".jpg", ".jpeg", ".png", ".webp"):
            media_group.append(InputMediaPhoto(media=input_file))
        elif suffix in (".mp4", ".mov", ".mkv", ".webm"):
            media_group.append(InputMediaVideo(media=input_file))
        elif suffix in (".mp3", ".wav", ".ogg"):
            audio_files.append(InputMediaAudio(media=input_file))
        else:
            media_group.append(InputMediaDocument(media=input_file))

    # Отправляем медиа группами по 10
    for i in range(0, len(media_group), 10):
        chunk = media_group[i : i + 10]
        if i == 0 and caption:
            await message.reply_media_group(media=chunk, caption=caption)
        else:
            await message.reply_media_group(media=chunk)

    # Отправляем аудио файлы отдельно
    for i in range(0, len(audio_files), 10):
        chunk = audio_files[i : i + 10]
        if i == 0 and caption and not media_group:  # Подпись только если нет других медиа
            await message.reply_media_group(media=chunk, caption=caption)
        else:
            await message.reply_media_group(media=chunk)


async def send_twitter_files(message: Message, files: list[Path], caption: str | None) -> None:
    """Специальная обработка для Twitter файлов."""
    images = [f for f in files if f.suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp')]
    videos = [f for f in files if f.suffix.lower() in ('.mp4', '.mov', '.mkv', '.webm')]
    
    success = False
    caption_arr = split_message_by_paragraphs(caption or "")
    
    # Отправляем изображения
    if images:
        if len(images) > 1:
            await send_images_in_chunks(message, images, caption_arr[0] if caption_arr else None)
            for part in caption_arr[1:]:
                await message.reply(telegramify_markdown.markdownify(part), parse_mode="MarkdownV2")
        else:
            replied = await message.reply_photo(
                FSInputFile(images[0]),
                caption=telegramify_markdown.markdownify(caption_arr[0]) if caption_arr else None,
                parse_mode="MarkdownV2",
            )
            for part in caption_arr[1:]:
                await replied.reply(telegramify_markdown.markdownify(part), parse_mode="MarkdownV2")
        success = True

    # Отправляем видео
    if videos:
        for idx, video in enumerate(videos):
            await message.reply_video(
                FSInputFile(video),
                caption=caption if not success and idx == 0 else None,
            )
        success = True


@router.message(Command("d_test"))
async def download_handler(message: Message, command: CommandObject):
    """Тестирует систему скачивания с диагностикой."""
    if not command.args:
        await message.reply(telegramify_markdown.markdownify(
            "🔧 **Тест системы скачивания**\n\n"
            "Использование: `/d_test <ссылка>`\n\n"
            "Команда протестирует все доступные методы скачивания "
            "и покажет подробную диагностику работы системы."
        ), parse_mode="MarkdownV2")
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
            report += f"✅ **Результат:** Успешно\n"
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
            report += f"❌ **Результат:** Неудача\n"
            if result.error:
                report += f"🚫 **Ошибка:** `{result.error}`\n"

        await status_message.edit_text(telegramify_markdown.markdownify(report), parse_mode="MarkdownV2")

        # Если есть файлы, показываем первый как пример
        if result.success and result.files:
            first_file = result.files[0]
            suffix = first_file.suffix.lower()
            
            try:
                if suffix in (".jpg", ".jpeg", ".png", ".webp"):
                    await message.reply_photo(
                        FSInputFile(first_file), 
                        caption="📷 Пример скачанного файла"
                    )
                elif suffix in (".mp4", ".mov", ".mkv", ".webm"):
                    await message.reply_video(
                        FSInputFile(first_file), 
                        caption="🎥 Пример скачанного файла"
                    )
                else:
                    await message.reply_document(
                        FSInputFile(first_file), 
                        caption="📄 Пример скачанного файла"
                    )
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
            
            status_report += f"📱 **Instagram User-Agent сервис:**\n"
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
            from tg_bot.downloaders import INSTAGRAM_REGEX, TWITTER_REGEX
            status_report += "• Instagram: ✅ Доступен\n"
            status_report += "• Twitter: ✅ Доступен\n"
        except Exception as e:
            status_report += f"• Кастомные скачиватели: ❌ {str(e)}\n"
        
        try:
            from tg_bot.downloaders.ytdlp import download_with_ytdlp
            status_report += "• yt-dlp: ✅ Доступен\n"
        except Exception as e:
            status_report += f"• yt-dlp: ❌ {str(e)}\n"
        
        try:
            from tg_bot.downloaders.gallery_dl import download_with_gallery_dl
            status_report += "• gallery-dl: ✅ Доступен\n"
        except Exception as e:
            status_report += f"• gallery-dl: ❌ {str(e)}\n"
        
        # Проверяем папку загрузок
        from core.config import DOWNLOADS_PATH
        if DOWNLOADS_PATH.exists():
            files_count = len(list(DOWNLOADS_PATH.iterdir()))
            status_report += f"\n📁 **Папка загрузок:** ✅ Доступна ({files_count} файлов)\n"
        else:
            status_report += "\n📁 **Папка загрузок:** ❌ Не найдена\n"

        await message.reply(telegramify_markdown.markdownify(status_report), parse_mode="MarkdownV2")

    except Exception as e:
        logger.error(f"Error checking downloader status: {e}")
        await message.reply(f"❌ Ошибка проверки статуса: {str(e)}")
