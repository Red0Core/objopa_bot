import asyncio
from pathlib import Path
import traceback
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
from aiogram.utils.media_group import MediaGroupBuilder
import telegramify_markdown

from core.config import DOWNLOADS_DIR
from core.logger import logger
from tg_bot.downloaders import (
    INSTAGRAM_REGEX,
    TWITTER_REGEX,
    downloader_manager,
    DownloaderType,
)
from tg_bot.services.gpt import get_gpt_formatted_chunks
from tg_bot.utils.video_utils import video_processor
from tg_bot.utils.media_processor import media_processor

router = Router()


async def send_images_in_chunks(message: Message, images: list[Path], caption: str | None = None):
    """Разбивает список изображений на чанки по 10 и отправляет их в Telegram"""

    def chunk_list(lst: list[Any], size: int = 10) -> list[list[Any]]:
        """Функция разбивает список на части по size элементов"""
        return [lst[i : i + size] for i in range(0, len(lst), size)]

    image_chunks = chunk_list(images, 10)

    for chunk in image_chunks:
        media_group = MediaGroupBuilder()
        for idx, img in enumerate(chunk):
            media_group.add_photo(media=FSInputFile(img), caption=caption if idx == 0 else None, parse_mode="MarkdownV2" if caption else None)

        await message.reply_media_group(media=media_group.build())
        await asyncio.sleep(5)


async def optimize_video_if_needed(video_path: Path, status_message: Message | None = None) -> Path:
    """
    Оптимизирует видео для отправки в Telegram если необходимо.
    Использует улучшенный VideoProcessor с кэшированием и адаптивным качеством.
    """
    try:
        # Получаем информацию о видео
        video_info = await video_processor.get_video_info(video_path)
        if not video_info:
            logger.warning(f"Could not get video info for {video_path.name}, skipping optimization")
            return video_path
        
        # Обновляем статус с подробностями
        if status_message:
            await status_message.edit_text(
                f"🔍 Анализирую видео: {video_info.size_mb:.1f}MB, "
                f"{video_info.duration:.1f}s, faststart: {'✅' if video_info.has_faststart else '❌'}"
            )
        
        # Определяем нужна ли оптимизация
        needs_optimization = (
            not video_info.has_faststart and "mp4" in video_info.format_name
        ) or video_info.size_mb > 50
        
        if not needs_optimization:
            logger.info(f"Video {video_path.name} is already optimized")
            return video_path
        
        # Обновляем статус
        if status_message:
            quality_profile = "fast" if video_info.size_mb > 100 else "medium"
            await status_message.edit_text(
                f"🔧 Оптимизирую видео ({quality_profile} качество)..."
            )
        
        # Оптимизируем видео
        success, optimized_path, error = await video_processor.optimize_video_for_telegram(video_path)
        
        if success and optimized_path:
            logger.success(f"Video {video_path.name} optimized successfully")
            return optimized_path
        else:
            logger.warning(f"Video optimization failed: {error}")
            return video_path
            
    except Exception as e:
        logger.error(f"Error during video optimization: {e}")
        return video_path


def split_message_by_paragraphs(text: str, max_length: int = 4096) -> list[str]:
    """Разбивает длинное сообщение на части по параграфам."""
    if len(text) <= max_length:
        return [text] if text else []
    
    # Используем существующую функцию или простое разбиение
    try:
        return get_gpt_formatted_chunks(text, max_length=max_length)
    except:
        # Fallback: простое разбиение по символам
        chunks = []
        current_chunk = ""
        
        for line in text.split('\n'):
            if len(current_chunk) + len(line) + 1 <= max_length:
                current_chunk += line + '\n'
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = line + '\n'
        
        if current_chunk:
            chunks.append(current_chunk.strip())
            
        return chunks


def _fallback_split(text: str, max_length: int) -> list[str]:
    """Простое разбиение текста на чанки по строкам с учетом max_length."""
    if not text:
        return []
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        add = ("\n" if current else "") + line
        if len(current) + len(add) <= max_length:
            current += add
        else:
            if current:
                chunks.append(current)
            current = line
    if current:
        chunks.append(current)
    return chunks


def build_caption_chunks(text: str | None) -> list[str]:
    """Строит подпись: первый чанк ≤1024, остальные ≤4096.

    При ошибках форматтера использует простой fallback-алгоритм.
    """
    if not text:
        return []
    try:
        if len(text) <= 1024:
            return get_gpt_formatted_chunks(text, max_length=1024)
        # Получаем первый чанк (≤1024)
        first_parts = get_gpt_formatted_chunks(text, max_length=1024)
        first_chunk = first_parts[0] if first_parts else text[:1024]
        rest_text = text[len(first_chunk):]
        # Остальные чанки (≤4096)
        rest_chunks = get_gpt_formatted_chunks(rest_text, max_length=4096) if rest_text else []
        return [first_chunk] + rest_chunks
    except Exception:
        # Fallback: безопасное разбиение без форматтера
        if len(text) <= 1024:
            return [text]
        first_fallback = _fallback_split(text, 1024)
        first_chunk = first_fallback[0] if first_fallback else text[:1024]
        rest_text = text[len(first_chunk):]
        return [first_chunk] + _fallback_split(rest_text, 4096)


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
        
        # Подготавливаем текст: первый чанк ≤1024, остальные ≤4096
        caption_arr = build_caption_chunks(result.caption)

        # Отправляем видео с оптимизацией
        if videos:
            success = await media_processor.process_and_send(
                message, 
                videos, 
                caption_arr[0] if caption_arr else None,
                use_optimization=True,
                caption_already_formatted=True,
                parse_mode="MarkdownV2"
            )
            # Отправляем оставшиеся части подписи
            for part in caption_arr[1:]:
                await message.reply(part, parse_mode="MarkdownV2")

        # Отправляем изображения  
        if images:
            success = await media_processor.process_and_send(
                message,
                images,
                caption_arr[0] if caption_arr and not videos else None,
                use_optimization=False,  # Изображения не нуждаются в оптимизации
                caption_already_formatted=True,
                parse_mode="MarkdownV2"
            )
            # Отправляем оставшиеся части подписи если видео не было
            start_index = 1 if not videos and caption_arr else 0
            for part in caption_arr[start_index:]:
                await message.reply(part, parse_mode="MarkdownV2")

        await status_message.delete()
        return True
        
    except Exception as e:
        logger.error(f"Error processing Instagram media: {traceback.format_exc()}")
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
        if not result.files and result.caption:
            logger.info(f"Downloaded media has no files but has caption")
            for part in get_gpt_formatted_chunks(result.caption):
                await message.reply(part, parse_mode="MarkdownV2")
        else:
            logger.info(f"Media downloaded successfully using {result.downloader_used.value if result.downloader_used else 'unknown'} from: {url}")
            await send_downloaded_files(message, result.files, result.caption, result.downloader_used)
        await status_message.delete()
        
    except Exception as e:
        logger.error(f"Error in universal download handler: {e}")
        await status_message.edit_text(f"❌ Ошибка при скачивании: {str(e)}")


async def send_downloaded_files(message: Message, files: list[Path], caption: str | None, downloader_used) -> None:
    """Отправляет скачанные файлы в Telegram."""
    if not files or (message.text is None):
        return
    # Обработка для Twitter (если использовался кастомный скачиватель и есть изображения/видео раздельно)
    if downloader_used == DownloaderType.CUSTOM and TWITTER_REGEX.match(message.text.removeprefix("/d ") or ""):
        await send_twitter_files(message, files, caption)
        return
    
    # Универсальная обработка через MediaProcessor
    success = await media_processor.process_and_send(
        message, 
        files, 
        caption,
        use_optimization=True,
        caption_already_formatted=True,
    )
    
    if not success:
        logger.warning("Failed to send files through MediaProcessor, trying fallback")
        # Fallback: отправляем как документы
        for file_path in files:
            await message.reply_document(FSInputFile(file_path))


async def send_twitter_files(message: Message, files: list[Path], caption: str | None) -> None:
    """Специальная обработка для Twitter файлов."""
    images = [f for f in files if f.suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp')]
    videos = [f for f in files if f.suffix.lower() in ('.mp4', '.mov', '.mkv', '.webm')]
    
    is_caption_sended = False
    caption_arr = build_caption_chunks(caption or "")

    # Отправляем изображения
    if images:
        if len(images) > 1:
            await send_images_in_chunks(message, images, caption_arr[0] if caption_arr else None)
            for part in caption_arr[1:]:
                await message.reply(part, parse_mode="MarkdownV2")
        else:
            replied = await message.reply_photo(
                FSInputFile(images[0]),
                caption=caption_arr[0] if caption_arr else None,
                parse_mode="MarkdownV2",
            )
            for part in caption_arr[1:]:
                await replied.reply(part, parse_mode="MarkdownV2")
        is_caption_sended = True

    # Отправляем видео с оптимизацией
    if videos:
        for idx, video in enumerate(videos):
            # Оптимизируем видео
            optimized_video = await optimize_video_if_needed(video)

            video_caption = caption_arr[0] if not is_caption_sended and idx == len(videos) - 1 else None
            await message.reply_video(
                FSInputFile(optimized_video),
                caption=video_caption,
                supports_streaming=True
            )
            
            # Очищаем временные файлы
            if optimized_video != video:
                video_processor.cleanup_temp_files(video, optimized_video)
        if not is_caption_sended:
            for part in caption_arr[1:]:
                await message.reply(part, parse_mode="MarkdownV2")
        is_caption_sended = True
    
    # Отправляем текст твита, если нет медиа файлов
    if not images and not videos and caption_arr:
        for part in caption_arr:
            await message.reply(part, parse_mode="MarkdownV2")


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
                        supports_streaming=True,
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
        from core.config import DOWNLOADS_DIR
        if DOWNLOADS_DIR.exists():
            files_count = len(list(DOWNLOADS_DIR.iterdir()))
            status_report += f"\n📁 **Папка загрузок:** ✅ Доступна ({files_count} файлов)\n"
        else:
            status_report += "\n📁 **Папка загрузок:** ❌ Не найдена\n"
        
        # Проверяем FFmpeg
        try:
            process = await asyncio.create_subprocess_exec(
                "ffmpeg", "-version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await process.communicate()
            
            if process.returncode == 0:
                # Извлекаем версию FFmpeg
                output = stdout.decode()
                version_line = output.split('\n')[0]
                status_report += f"🎬 **FFmpeg:** ✅ {version_line}\n"
            else:
                status_report += "🎬 **FFmpeg:** ❌ Не работает\n"
        except FileNotFoundError:
            status_report += "🎬 **FFmpeg:** ❌ Не установлен\n"
        except Exception as e:
            status_report += f"🎬 **FFmpeg:** ❌ Ошибка проверки - {str(e)}\n"

        await message.reply(telegramify_markdown.markdownify(status_report), parse_mode="MarkdownV2")

    except Exception as e:
        logger.error(f"Error checking downloader status: {e}")
        await message.reply(f"❌ Ошибка проверки статуса: {str(e)}")


@router.message(Command("video_test"))
async def video_test_handler(message: Message, command: CommandObject):
    """Тестирует оптимизацию видео файла."""
    if not command.args:
        await message.reply(telegramify_markdown.markdownify(
            "🎬 **Тест оптимизации видео**\n\n"
            "Использование: `/video_test <путь_к_видео_файлу>`\n\n"
            "Команда проверит видео на наличие faststart и при необходимости оптимизирует его."
        ), parse_mode="MarkdownV2")
        return

    video_name = command.args.strip()
    video_path = DOWNLOADS_DIR / video_name
    
    if not video_path.exists():
        await message.reply(f"❌ Файл `{video_name}` не найден в папке загрузок.")
        return
    
    if video_path.suffix.lower() not in ('.mp4', '.mov', '.mkv', '.webm'):
        await message.reply(f"❌ Файл `{video_name}` не является видео файлом.")
        return
    
    status_message = await message.answer("🔍 Анализирую видео файл...")
    
    try:
        # Проверяем текущее состояние
        has_faststart = await video_processor.check_faststart(video_path)
        file_size_mb = video_path.stat().st_size / (1024 * 1024)
        
        report = f"🎬 **Анализ видео:** `{video_name}`\n\n"
        report += f"📊 **Размер:** {file_size_mb:.1f} MB\n"
        report += f"⚡ **Faststart:** {'✅ Включен' if has_faststart else '❌ Выключен'}\n"
        report += f"📱 **Совместимость с Telegram:** {'✅ Готов' if has_faststart and file_size_mb <= 50 else '⚠️ Требует оптимизации'}\n\n"
        
        if has_faststart and file_size_mb <= 50:
            report += "✅ Видео уже оптимизировано для Telegram!"
            await status_message.edit_text(telegramify_markdown.markdownify(report), parse_mode="MarkdownV2")
        else:
            report += "🔧 Запускаю оптимизацию...\n"
            await status_message.edit_text(telegramify_markdown.markdownify(report), parse_mode="MarkdownV2")
            
            # Оптимизируем видео
            success, optimized_path, error = await video_processor.optimize_video_for_telegram(video_path, max_size_mb=50)
            
            if success and optimized_path:
                new_size_mb = optimized_path.stat().st_size / (1024 * 1024)
                final_report = report + f"✅ **Оптимизация завершена!**\n"
                final_report += f"📊 **Новый размер:** {new_size_mb:.1f} MB\n"
                final_report += f"💾 **Экономия:** {file_size_mb - new_size_mb:.1f} MB\n"
                
                if error:
                    final_report += f"⚠️ **Предупреждение:** {error}\n"
                
                await status_message.edit_text(telegramify_markdown.markdownify(final_report), parse_mode="MarkdownV2")
                
                # Отправляем оптимизированное видео как пример
                await message.reply_video(
                    FSInputFile(optimized_path),
                    supports_streaming=True,
                    caption="🎬 Оптимизированное видео (для демонстрации)"
                )
                
                # Очищаем временный файл если он отличается от оригинала
                if optimized_path != video_path:
                    video_processor.cleanup_temp_files(video_path, optimized_path)
            else:
                error_report = report + f"❌ **Ошибка оптимизации:** {error}\n"
                await status_message.edit_text(telegramify_markdown.markdownify(error_report), parse_mode="MarkdownV2")
        
    except Exception as e:
        logger.error(f"Error in video test: {e}")
        await status_message.edit_text(f"❌ Ошибка анализа видео: {str(e)}")


@router.message(Command("video_stats"))
async def video_stats_handler(message: Message):
    """Показывает статистику системы оптимизации видео."""
    try:
        stats = video_processor.get_optimization_stats()
        
        report = "📊 **Статистика видео процессора**\n\n"
        
        # Кэш
        report += f"💾 **Кэш информации:** {stats['cache_size']} файлов\n\n"
        
        # Конфигурация
        config = stats['config']
        report += "⚙️ **Настройки:**\n"
        report += f"• Лимит размера: {config['max_size_mb']} MB\n"
        report += f"• Порог малых файлов: {config['small_file_threshold']} MB\n"
        report += f"• Preset сжатия: {config['compression_preset']}\n\n"
        
        # Профили качества
        report += f"🎯 **Доступные профили:** {', '.join(stats['quality_profiles'])}\n\n"
        
        # Действия
        report += "🔧 **Команды управления:**\n"
        report += "• `/video_clear_cache` - очистить кэш\n"
        report += "• `/video_test <файл>` - тестировать файл\n"
        report += "• `/d_status` - статус системы\n"

        await message.reply(telegramify_markdown.markdownify(report), parse_mode="MarkdownV2")
        
    except Exception as e:
        logger.error(f"Error getting video stats: {e}")
        await message.reply(f"❌ Ошибка получения статистики: {str(e)}")


@router.message(Command("video_clear_cache"))
async def video_clear_cache_handler(message: Message):
    """Очищает кэш информации о видео файлах."""
    try:
        old_size = len(video_processor._video_info_cache)
        video_processor.clear_cache()
        
        await message.reply(f"✅ Кэш очищен. Удалено записей: {old_size}")
        
    except Exception as e:
        logger.error(f"Error clearing video cache: {e}")
        await message.reply(f"❌ Ошибка очистки кэша: {str(e)}")


@router.message(Command("batch_optimize"))
async def batch_optimize_handler(message: Message, command: CommandObject):
    """Пакетная оптимизация видео файлов в папке загрузок."""
    if not command.args:
        await message.reply(telegramify_markdown.markdownify(
            "📦 **Пакетная оптимизация видео**\n\n"
            "Использование: `/batch_optimize <маска_файлов>`\n\n"
            "Примеры:\n"
            "• `/batch_optimize *.mp4` - все MP4 файлы\n"
            "• `/batch_optimize video_*` - файлы начинающиеся с 'video_'\n"
            "• `/batch_optimize all` - все видео файлы\n\n"
            "⚠️ Операция может занять много времени!"
        ), parse_mode="MarkdownV2")
        return

    pattern = command.args.strip()
    status_message = await message.answer("🔍 Поиск видео файлов...")
    
    try:
        # Находим файлы по паттерну
        video_files = []
        
        if pattern.lower() == "all":
            # Все видео файлы
            for ext in [".mp4", ".mov", ".mkv", ".webm", ".avi"]:
                video_files.extend(DOWNLOADS_DIR.glob(f"*{ext}"))
        else:
            # По паттерну
            video_files = list(DOWNLOADS_DIR.glob(pattern))
            # Фильтруем только видео
            video_files = [f for f in video_files if f.suffix.lower() in [".mp4", ".mov", ".mkv", ".webm", ".avi"]]
        
        if not video_files:
            await status_message.edit_text(f"❌ Видео файлы по маске '{pattern}' не найдены.")
            return
        
        await status_message.edit_text(f"📋 Найдено {len(video_files)} файлов. Начинаю оптимизацию...")
        
        # Оптимизируем с ограничением на 2 одновременных процесса
        results = await video_processor.optimize_multiple_videos(video_files, max_concurrent=2)
        
        # Подсчитываем статистику
        successful = 0
        failed = 0
        total_original_size = 0
        total_optimized_size = 0
        
        for original_path, success, optimized_path, error in results:
            if success and optimized_path:
                successful += 1
                total_original_size += original_path.stat().st_size
                total_optimized_size += optimized_path.stat().st_size
                
                # Очищаем временные файлы
                if optimized_path != original_path:
                    video_processor.cleanup_temp_files(original_path, optimized_path)
            else:
                failed += 1
        
        # Формируем отчет
        total_original_mb = total_original_size / (1024 * 1024)
        total_optimized_mb = total_optimized_size / (1024 * 1024)
        saved_mb = total_original_mb - total_optimized_mb
        saved_percent = (saved_mb / total_original_mb * 100) if total_original_mb > 0 else 0
        
        report = f"📊 **Результаты пакетной оптимизации:**\n\n"
        report += f"✅ Успешно: {successful}\n"
        report += f"❌ Ошибки: {failed}\n"
        report += f"📦 Всего файлов: {len(video_files)}\n\n"
        
        if successful > 0:
            report += f"💾 **Экономия места:**\n"
            report += f"• Было: {total_original_mb:.1f} MB\n"
            report += f"• Стало: {total_optimized_mb:.1f} MB\n"
            report += f"• Сэкономлено: {saved_mb:.1f} MB ({saved_percent:.1f}%)\n"
        
        await status_message.edit_text(telegramify_markdown.markdownify(report), parse_mode="MarkdownV2")
        
    except Exception as e:
        logger.error(f"Error in batch optimization: {e}")
        await status_message.edit_text(f"❌ Ошибка пакетной оптимизации: {str(e)}")
