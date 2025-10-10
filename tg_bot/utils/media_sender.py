import asyncio
from pathlib import Path
from typing import cast

from aiogram.types import (
    FSInputFile,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    MediaUnion,
    Message,
)

from core.logger import logger
from tg_bot.utils.caption_formatter import caption_formatter
from tg_bot.utils.video_utils import video_processor


class MediaSender:
    """Простой отправщик медиа без логики форматирования."""
    
    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
    AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".m4a", ".flac"}
    
    @staticmethod
    async def send(
        message: Message,
        files: list[Path],
        caption: str | None = None,
        optimize_video: bool = True
    ) -> bool:
        """
        Отправляет медиа файлы с caption.
        
        Args:
            message: Сообщение для ответа
            files: Список файлов
            caption: Текст подписи (будет автоматически отформатирован)
            optimize_video: Оптимизировать видео для Telegram
            
        Returns:
            True если успешно
        """
        if not files:
            # Если только текст без файлов
            if caption:
                parts = caption_formatter.format_and_split(caption, first_max=4096, rest_max=4096)
                for part in parts:
                    await message.reply(part, parse_mode="MarkdownV2")
                return True
            return False
        
        try:
            # Форматируем caption один раз
            caption_parts = caption_formatter.format_and_split(caption) if caption else []
            
            # Группируем по типам
            images = [f for f in files if f.suffix.lower() in MediaSender.IMAGE_EXTS]
            videos = [f for f in files if f.suffix.lower() in MediaSender.VIDEO_EXTS]
            audio = [f for f in files if f.suffix.lower() in MediaSender.AUDIO_EXTS]
            docs = [f for f in files if f.suffix.lower() not in (MediaSender.IMAGE_EXTS | MediaSender.VIDEO_EXTS | MediaSender.AUDIO_EXTS)]
            
            caption_used = False
            
            # Отправляем изображения (группами по 10)
            if images:
                await MediaSender._send_images(message, images, caption_parts[0] if caption_parts else None)
                caption_used = True
            
            # Отправляем видео (по одному с оптимизацией)
            if videos:
                first_caption = caption_parts[0] if caption_parts and not caption_used else None
                await MediaSender._send_videos(message, videos, first_caption, optimize_video)
                caption_used = True
            
            # Отправляем аудио
            if audio:
                first_caption = caption_parts[0] if caption_parts and not caption_used else None
                await MediaSender._send_audio(message, audio, first_caption)
                caption_used = True
            
            # Отправляем документы
            if docs:
                first_caption = caption_parts[0] if caption_parts and not caption_used else None
                await MediaSender._send_documents(message, docs, first_caption)
                caption_used = True
            
            # Отправляем оставшиеся части caption
            start_idx = 1 if caption_used else 0
            for part in caption_parts[start_idx:]:
                await message.reply(part, parse_mode="MarkdownV2")
            
            return True
            
        except Exception as e:
            logger.error(f"Error sending media: {e}")
            return False
    
    @staticmethod
    async def _send_images(message: Message, images: list[Path], caption: str | None):
        """Отправляет изображения группами по 10."""
        for i in range(0, len(images), 10):
            chunk = images[i:i + 10]
            media_group = [
                InputMediaPhoto(media=FSInputFile(img))
                for img in chunk
            ]
            
            # Caption только на первое фото в первой группе
            chunk_caption = caption if i == 0 and caption else None
            if chunk_caption and media_group:
                media_group[0] = InputMediaPhoto(
                    media=FSInputFile(chunk[0]),
                    caption=chunk_caption,
                    parse_mode="MarkdownV2"
                )
            
            await message.reply_media_group(media=cast(list[MediaUnion], media_group))
            
            if i + 10 < len(images):
                await asyncio.sleep(5)
    
    @staticmethod
    async def _send_videos(message: Message, videos: list[Path], caption: str | None, optimize: bool):
        """Отправляет видео по одному с оптимизацией."""
        for idx, video in enumerate(videos):
            video_path = video
            
            # Оптимизация
            if optimize:
                try:
                    success, optimized, _ = await video_processor.optimize_video_for_telegram(video)
                    if success and optimized:
                        video_path = optimized
                except Exception as e:
                    logger.warning(f"Video optimization failed: {e}")
            
            # Caption только на последнее видео
            video_caption = caption if idx == len(videos) - 1 and caption else None
            
            await message.reply_video(
                FSInputFile(video_path),
                caption=video_caption,
                parse_mode="MarkdownV2" if video_caption else None,
                supports_streaming=True
            )
            
            # Очистка временных файлов
            if video_path != video:
                try:
                    video_processor.cleanup_temp_files(video, video_path)
                except Exception:
                    pass
    
    @staticmethod
    async def _send_audio(message: Message, audio: list[Path], caption: str | None):
        """Отправляет аудио группами по 10."""
        for i in range(0, len(audio), 10):
            chunk = audio[i:i + 10]
            media_group = [
                InputMediaAudio(media=FSInputFile(a))
                for a in chunk
            ]
            
            chunk_caption = caption if i == 0 and caption else None
            if chunk_caption and media_group:
                media_group[0] = InputMediaAudio(
                    media=FSInputFile(chunk[0]),
                    caption=chunk_caption,
                    parse_mode="MarkdownV2"
                )
            
            await message.reply_media_group(media=cast(list[MediaUnion], media_group))
            
            if i + 10 < len(audio):
                await asyncio.sleep(5)
    
    @staticmethod
    async def _send_documents(message: Message, docs: list[Path], caption: str | None):
        """Отправляет документы группами по 10."""
        for i in range(0, len(docs), 10):
            chunk = docs[i:i + 10]
            media_group = [
                InputMediaDocument(media=FSInputFile(d))
                for d in chunk
            ]
            
            chunk_caption = caption if i == 0 and caption else None
            if chunk_caption and media_group:
                media_group[0] = InputMediaDocument(
                    media=FSInputFile(chunk[0]),
                    caption=chunk_caption,
                    parse_mode="MarkdownV2"
                )
            
            await message.reply_media_group(media=cast(list[MediaUnion], media_group))
            
            if i + 10 < len(docs):
                await asyncio.sleep(5)


# Глобальный экземпляр
media_sender = MediaSender()
